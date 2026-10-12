"""Reusable Dayax Somali ASR service for uploaded audio and video."""
import base64
import hashlib
import json
import importlib.util
import ipaddress
import math
import os
import re
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import uuid
import wave
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlsplit, urlunsplit

import numpy as np
import requests
from werkzeug.utils import secure_filename

from config import Config
from .transcript_claim_service import (
    extract_complete_verbatim_claim,
    extract_single_verbatim_claim,
)
from .gemini_service import (
    extract_main_verbatim_claim_candidate,
)


SUPPORTED_MEDIA_EXTENSIONS = {'.wav', '.mp3', '.m4a', '.mp4'}
MINIMUM_MEDIA_SEGMENT_SECONDS = 1.0
SUPPORTED_MEDIA_URL_DOMAINS = (
    'youtube.com',
    'youtube-nocookie.com',
    'youtu.be',
    'facebook.com',
    'fb.watch',
    'instagram.com',
    'tiktok.com',
    'twitter.com',
    'x.com',
    'vimeo.com',
    'dailymotion.com',
    'dai.ly',
    'soundcloud.com',
    't.me',
)


class MediaASRError(RuntimeError):
    """A user-facing media or ASR error."""

    def __init__(self, message, code='media_error'):
        super().__init__(message)
        self.code = code


def _remote_asr_configured():
    return bool(Config.RUNPOD_API_KEY and Config.RUNPOD_ASR_ENDPOINT_ID)


def _selected_asr_backend():
    backend = str(Config.ASR_BACKEND or 'auto').strip().casefold()
    if backend not in {'auto', 'local', 'remote'}:
        raise MediaASRError("ASR_BACKEND must be 'auto', 'local', or 'remote'.")
    if backend == 'remote':
        if not _remote_asr_configured():
            raise MediaASRError(
                'RunPod Dayax ASR is selected but RUNPOD_API_KEY or '
                'RUNPOD_ASR_ENDPOINT_ID is missing.'
            )
        return 'remote'
    if backend == 'auto' and _remote_asr_configured():
        return 'remote'
    return 'local'


def _creation_flags():
    return subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0


def _portable_binary_candidates(executable_name):
    filename = f'{executable_name}.exe' if os.name == 'nt' else executable_name
    base_dir = Path(Config.BASE_DIR)
    candidates = [base_dir / 'tools' / 'ffmpeg' / 'bin' / filename]
    if os.name == 'nt':
        # Reuse the verified standalone prototype installation on this workstation.
        candidates.append(
            Path(r'D:\parapharsing tool\somali_asr_test\tools\ffmpeg\bin')
            / filename
        )
        winget_root = (
            Path.home() / 'AppData' / 'Local' / 'Microsoft' / 'WinGet' / 'Packages'
        )
        try:
            if winget_root.is_dir():
                candidates.extend(winget_root.glob(
                    f'Gyan.FFmpeg*{os.sep}ffmpeg-*{os.sep}bin{os.sep}{filename}'
                ))
        except OSError:
            pass
    return candidates


def resolve_media_binary(executable_name):
    environment_name = f'{executable_name.upper()}_PATH'
    configured = os.getenv(environment_name, '').strip()
    if configured and Path(configured).is_file():
        return str(Path(configured))
    resolved = shutil.which(executable_name)
    if resolved:
        return resolved
    for candidate in _portable_binary_candidates(executable_name):
        if candidate.is_file():
            return str(candidate)
    raise MediaASRError(
        f'{executable_name} is unavailable. Configure {environment_name} or install FFmpeg.'
    )


def media_runtime_status():
    try:
        backend = _selected_asr_backend()
        return {
            'ready': True,
            'ffmpeg': resolve_media_binary('ffmpeg'),
            'ffprobe': resolve_media_binary('ffprobe'),
            'asr_backend': backend,
            'asr_model': Config.ASR_MODEL_ID,
            'public_media_links_enabled': Config.ENABLE_PUBLIC_MEDIA_LINKS,
            'url_download_ready': (
                Config.ENABLE_PUBLIC_MEDIA_LINKS
                and importlib.util.find_spec('yt_dlp') is not None
            ),
            'url_platforms': list(SUPPORTED_MEDIA_URL_DOMAINS),
        }
    except MediaASRError as exc:
        return {'ready': False, 'error': str(exc)}


def _run(command, timeout):
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=_creation_flags(),
        )
    except subprocess.TimeoutExpired as exc:
        raise MediaASRError('Media processing exceeded the allowed time.') from exc
    if completed.returncode != 0:
        detail = completed.stderr.strip() or 'The media command failed.'
        raise MediaASRError(detail[-1200:])
    return completed


def _host_matches_supported_platform(hostname):
    return any(
        hostname == domain or hostname.endswith(f'.{domain}')
        for domain in SUPPORTED_MEDIA_URL_DOMAINS
    )


def _validate_public_media_url(raw_url):
    """Validate a user URL before it is passed to the platform extractor."""
    value = str(raw_url or '').strip()
    if not value:
        raise MediaASRError('Paste a public audio or video link.')
    if len(value) > 2048:
        raise MediaASRError('The media link is too long.')

    try:
        parsed = urlsplit(value)
    except ValueError as exc:
        raise MediaASRError('Enter a valid public media link.') from exc
    if parsed.scheme.casefold() not in {'http', 'https'}:
        raise MediaASRError('Media links must use HTTP or HTTPS.')
    if parsed.username or parsed.password:
        raise MediaASRError('Links containing embedded credentials are not allowed.')
    if not parsed.hostname:
        raise MediaASRError('The media link has no valid hostname.')
    try:
        port = parsed.port
    except ValueError as exc:
        raise MediaASRError('The media link contains an invalid port.') from exc
    if port not in (None, 80, 443):
        raise MediaASRError('Only standard public web ports are allowed.')

    try:
        hostname = parsed.hostname.rstrip('.').encode('idna').decode('ascii').casefold()
    except UnicodeError as exc:
        raise MediaASRError('The media hostname is invalid.') from exc
    if not _host_matches_supported_platform(hostname):
        raise MediaASRError(
            'This platform is not supported. Use a public YouTube, Facebook, '
            'Instagram, TikTok, X, Vimeo, Dailymotion, SoundCloud, or Telegram link.'
        )

    try:
        addresses = {
            item[4][0]
            for item in socket.getaddrinfo(
                hostname,
                port or (443 if parsed.scheme.casefold() == 'https' else 80),
                type=socket.SOCK_STREAM,
            )
        }
    except (OSError, socket.gaierror) as exc:
        raise MediaASRError('The media platform could not be reached.') from exc
    if not addresses:
        raise MediaASRError('The media platform could not be resolved.')
    for address in addresses:
        try:
            ip = ipaddress.ip_address(address.split('%', 1)[0])
        except ValueError as exc:
            raise MediaASRError('The media platform returned an invalid address.') from exc
        if not ip.is_global:
            raise MediaASRError('Private or local network media links are not allowed.')

    netloc = hostname
    if port:
        netloc = f'{hostname}:{port}'
    return urlunsplit((parsed.scheme.casefold(), netloc, parsed.path or '/', parsed.query, ''))


def _media_url_identity(media_url):
    """Return a stable identity for alternate URLs of the same media item."""
    parsed = urlsplit(str(media_url or '').strip())
    host = (parsed.hostname or '').rstrip('.').casefold()
    path_parts = [part for part in parsed.path.split('/') if part]
    video_id = ''
    if host == 'youtu.be' or host.endswith('.youtu.be'):
        video_id = path_parts[0] if path_parts else ''
    elif host == 'youtube.com' or host.endswith('.youtube.com'):
        if parsed.path.rstrip('/') == '/watch':
            video_id = (parse_qs(parsed.query).get('v') or [''])[0]
        elif path_parts and path_parts[0].casefold() in {'shorts', 'embed', 'live'}:
            video_id = path_parts[1] if len(path_parts) > 1 else ''
    if re.fullmatch(r'[A-Za-z0-9_-]{6,}', video_id or ''):
        return f'youtube:{video_id}'
    return urlunsplit((
        parsed.scheme.casefold(),
        host,
        parsed.path.rstrip('/') or '/',
        parsed.query,
        '',
    ))


def _probe_duration(input_path):
    command = [
        resolve_media_binary('ffprobe'), '-v', 'error',
        '-show_entries', 'format=duration', '-of', 'json', str(input_path),
    ]
    completed = _run(command, timeout=60)
    try:
        duration = float(json.loads(completed.stdout)['format']['duration'])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise MediaASRError('The uploaded media has no readable duration.') from exc
    if not math.isfinite(duration) or duration <= 0:
        raise MediaASRError('The uploaded media is empty or corrupted.')
    return duration


def _validated_clip_bounds(
    source_duration,
    clip_start_seconds=None,
    clip_end_seconds=None,
):
    """Return a safe, bounded segment without modifying the source file."""
    try:
        start = 0.0 if clip_start_seconds is None else float(clip_start_seconds)
        end = (
            float(source_duration)
            if clip_end_seconds is None
            else float(clip_end_seconds)
        )
    except (TypeError, ValueError) as exc:
        raise MediaASRError('Enter valid start and end times for the media segment.') from exc

    if not math.isfinite(start) or not math.isfinite(end):
        raise MediaASRError('Enter valid start and end times for the media segment.')
    if start < 0:
        raise MediaASRError('The segment start time cannot be negative.')
    if start >= source_duration:
        raise MediaASRError('The segment start time must be before the media ends.')
    if end > source_duration + 0.25:
        raise MediaASRError('The segment end time is beyond the media duration.')
    end = min(end, float(source_duration))
    segment_duration = end - start
    if segment_duration < MINIMUM_MEDIA_SEGMENT_SECONDS:
        raise MediaASRError(
            f'Select at least {int(MINIMUM_MEDIA_SEGMENT_SECONDS)} second of media.'
        )
    if segment_duration > Config.ASR_MAX_DURATION_SECONDS:
        raise MediaASRError(
            f'The selected segment exceeds the '
            f'{Config.ASR_MAX_DURATION_SECONDS // 60}-minute limit.'
        )
    clip_applied = start > 0.001 or end < source_duration - 0.25
    return start, end, segment_duration, clip_applied


def _convert_to_wav(
    input_path,
    output_path,
    clip_start_seconds=0.0,
    clip_duration_seconds=None,
):
    command = [
        resolve_media_binary('ffmpeg'), '-nostdin', '-hide_banner',
        '-loglevel', 'error', '-threads', '0', '-y',
    ]
    # Fast input seeking prevents decoding discarded video frames before the
    # selected segment. The original upload remains unchanged.
    if clip_start_seconds > 0:
        command.extend(['-ss', f'{clip_start_seconds:.3f}'])
    command.extend(['-i', str(input_path)])
    if clip_duration_seconds is not None:
        command.extend(['-t', f'{clip_duration_seconds:.3f}'])
    command.extend([
        '-map', '0:a:0', '-vn', '-ac', '1', '-ar', '16000',
        '-c:a', 'pcm_s16le', str(output_path),
    ])
    _run(command, timeout=Config.ASR_FFMPEG_TIMEOUT_SECONDS)
    if not output_path.is_file() or output_path.stat().st_size <= 44:
        raise MediaASRError('No usable audio track was found in the uploaded file.')


def _load_wav(wav_path):
    try:
        with wave.open(str(wav_path), 'rb') as audio:
            if (
                audio.getnchannels() != 1
                or audio.getsampwidth() != 2
                or audio.getframerate() != 16000
            ):
                raise MediaASRError('Converted audio is not 16 kHz mono PCM.')
            raw = audio.readframes(audio.getnframes())
    except (wave.Error, OSError) as exc:
        raise MediaASRError('The converted audio could not be decoded.') from exc
    samples = np.frombuffer(raw, dtype='<i2').astype(np.float32) / 32768.0
    if not samples.size:
        raise MediaASRError('The uploaded file contains no audio samples.')
    rms = float(np.sqrt(np.mean(np.square(samples), dtype=np.float64)))
    if not math.isfinite(rms) or rms < 1e-5:
        raise MediaASRError('The uploaded audio is silent or too quiet.')
    return samples


def _file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def _cache_path(cache_key):
    return Path(Config.ASR_RESULT_CACHE_DIR) / f'{cache_key}.json'


def _read_cached_transcription(cache_key, maximum_age_seconds=None):
    if not Config.ASR_RESULT_CACHE_ENABLED or not cache_key:
        return None
    path = _cache_path(cache_key)
    try:
        if not path.is_file():
            return None
        if maximum_age_seconds is not None:
            age_seconds = time.time() - path.stat().st_mtime
            if age_seconds > maximum_age_seconds:
                return None
        cached = json.loads(path.read_text(encoding='utf-8'))
        if cached.get('model_id') != Config.ASR_MODEL_ID:
            return None
        if not str(cached.get('transcript') or '').strip():
            return None
        return cached
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None


def _write_cached_transcription(cache_key, output):
    if not Config.ASR_RESULT_CACHE_ENABLED or not cache_key:
        return
    cache_dir = Path(Config.ASR_RESULT_CACHE_DIR)
    cache_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        'model_id': Config.ASR_MODEL_ID,
        'transcript': output['transcript'],
        'transcription_seconds': output.get('transcription_seconds', 0),
        'chunk_count': output.get('chunk_count', 0),
        'runtime': output.get('runtime', {}),
        'cached_at': time.time(),
    }
    temporary_path = cache_dir / f'.{cache_key}.{uuid.uuid4().hex}.tmp'
    temporary_path.write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding='utf-8',
    )
    temporary_path.replace(_cache_path(cache_key))


def _cached_output(cached):
    output = dict(cached)
    runtime = dict(output.get('runtime') or {})
    runtime.update({
        'model_id': Config.ASR_MODEL_ID,
        'mode': 'dayax',
        'backend': runtime.get('backend', 'cache'),
    })
    output['runtime'] = runtime
    # This request performed no inference. Preserve original timing in the
    # cache file, but report zero for the current cached response.
    output['transcription_seconds'] = 0.0
    output['cache_hit'] = True
    return output


def _merge_transcripts(existing, incoming, maximum_overlap_words=18):
    left = (existing or '').strip().split()
    right = (incoming or '').strip().split()
    if not left:
        return ' '.join(right)
    if not right:
        return ' '.join(left)
    limit = min(maximum_overlap_words, len(left), len(right))
    overlap = 0
    for size in range(limit, 0, -1):
        if [word.casefold() for word in left[-size:]] == [
            word.casefold() for word in right[:size]
        ]:
            overlap = size
            break
    return ' '.join(left + right[overlap:])


def _split_audio(samples, chunk_seconds, overlap_seconds):
    chunk_size = int(chunk_seconds * 16000)
    overlap_size = int(overlap_seconds * 16000)
    step = chunk_size - overlap_size
    if step <= 0:
        raise MediaASRError('Invalid ASR chunk configuration.')
    if len(samples) <= chunk_size:
        return [samples]
    chunks = []
    for start in range(0, len(samples), step):
        chunk = samples[start:start + chunk_size]
        if len(chunk) < 16000:
            break
        # Avoid expensive inference for truly silent windows.
        if float(np.sqrt(np.mean(np.square(chunk), dtype=np.float64))) >= 1e-5:
            chunks.append(chunk)
        if start + chunk_size >= len(samples):
            break
    return chunks


def _discover_cache_dir():
    configured = os.getenv('ASR_CACHE_DIR', '').strip() or os.getenv('HF_HOME', '').strip()
    candidates = []
    if configured:
        candidates.append(Path(configured))
    candidates.extend([
        Path(Config.BASE_DIR) / 'asr_cache',
        Path(r'D:\parapharsing tool\somali_asr_test\hub'),
        Path.home() / '.cache' / 'huggingface' / 'hub',
    ])
    repository_folder = f"models--{Config.ASR_MODEL_ID.replace('/', '--')}"
    for candidate in candidates:
        if (candidate / repository_folder).is_dir():
            return candidate
    return candidates[0]


@dataclass
class ASRRuntime:
    model_id: str
    mode: str
    device: str
    load_seconds: float
    cache_dir: str
    backend: str = 'local'


class SomaliASREngine:
    def __init__(self, mode, model_id):
        self.mode = mode
        self.model_id = model_id
        self.processor = None
        self.model = None
        self.torch = None
        self.device = None
        self.load_seconds = 0.0
        self.cache_dir = _discover_cache_dir()
        self._load_lock = threading.Lock()
        self._inference_lock = threading.Lock()

    @property
    def loaded(self):
        return self.processor is not None and self.model is not None

    def load(self):
        if self.loaded:
            return self.runtime()
        with self._load_lock:
            if self.loaded:
                return self.runtime()
            started = time.perf_counter()
            try:
                import torch
                from transformers import AutoModelForCTC, AutoProcessor
            except ImportError as exc:
                raise MediaASRError(
                    'PyTorch and Transformers are required for media transcription.'
                ) from exc

            self.torch = torch
            if torch.cuda.is_available():
                self.device = torch.device('cuda')
                dtype = torch.float16
            else:
                self.device = torch.device('cpu')
                dtype = torch.float32
                requested_threads = int(os.getenv('ASR_CPU_THREADS', '0') or 0)
                thread_count = requested_threads or min(max(os.cpu_count() or 2, 2), 8)
                torch.set_num_threads(thread_count)
                try:
                    torch.set_num_interop_threads(max(1, min(2, thread_count // 2)))
                except RuntimeError:
                    pass
                # Wav2Vec2/XLS-R inference benefits from oneDNN on supported
                # x86 CPUs. This does not change model weights or decoding.
                if hasattr(torch.backends, 'mkldnn'):
                    torch.backends.mkldnn.enabled = True

            self.cache_dir.mkdir(parents=True, exist_ok=True)
            token = os.getenv('HF_TOKEN', '').strip() or None
            try:
                self.processor = AutoProcessor.from_pretrained(
                    self.model_id,
                    token=token,
                    cache_dir=str(self.cache_dir),
                )
                kwargs = {
                    'token': token,
                    'cache_dir': str(self.cache_dir),
                    'low_cpu_mem_usage': bool(
                        importlib.util.find_spec('accelerate')
                    ),
                }
                try:
                    self.model = AutoModelForCTC.from_pretrained(
                        self.model_id, dtype=dtype, **kwargs
                    )
                except TypeError:
                    self.model = AutoModelForCTC.from_pretrained(
                        self.model_id, torch_dtype=dtype, **kwargs
                    )
                self.model.to(self.device)
                self.model.eval()
            except Exception as exc:
                self.processor = None
                self.model = None
                message = str(exc)
                if any(value in message.casefold() for value in ('gated', '401', '403')):
                    message = 'Accept the gated model conditions and configure HF_TOKEN.'
                raise MediaASRError(
                    f"Could not load Somali ASR model '{self.model_id}': {message}"
                ) from exc
            self.load_seconds = time.perf_counter() - started
            return self.runtime()

    def runtime(self):
        device = 'CPU'
        if self.device is not None and self.device.type == 'cuda':
            device = f'CUDA: {self.torch.cuda.get_device_name(0)}'
        return ASRRuntime(
            model_id=self.model_id,
            mode=self.mode,
            device=device,
            load_seconds=self.load_seconds,
            cache_dir=str(self.cache_dir),
            backend='local',
        )

    def _transcribe_chunk(self, chunk):
        inputs = self.processor(
            chunk,
            sampling_rate=16000,
            return_tensors='pt',
            padding=False,
        )
        model_inputs = {}
        parameter_dtype = next(self.model.parameters()).dtype
        for key, value in inputs.items():
            value = value.to(self.device)
            if value.is_floating_point():
                value = value.to(parameter_dtype)
            model_inputs[key] = value
        with self.torch.inference_mode():
            token_ids = self.torch.argmax(
                self.model(**model_inputs).logits,
                dim=-1,
            ).cpu()
        return self.processor.batch_decode(token_ids)[0].strip()

    def transcribe(self, samples):
        runtime = self.load()
        chunks = _split_audio(
            samples,
            Config.ASR_CHUNK_SECONDS,
            Config.ASR_OVERLAP_SECONDS,
        )
        if not chunks:
            raise MediaASRError('No speech-containing audio windows were detected.')
        transcript = ''
        started = time.perf_counter()
        try:
            with self._inference_lock:
                for chunk in chunks:
                    transcript = _merge_transcripts(
                        transcript,
                        self._transcribe_chunk(chunk),
                    )
        except RuntimeError as exc:
            if 'out of memory' in str(exc).casefold():
                if self.device.type == 'cuda':
                    self.torch.cuda.empty_cache()
                raise MediaASRError(
                    'ASR ran out of memory. Use a shorter recording or configure '
                    'the RunPod GPU backend.'
                ) from exc
            raise MediaASRError(f'ASR transcription failed: {exc}') from exc
        if not transcript.strip():
            raise MediaASRError('The ASR model did not produce a transcript.')
        return {
            'transcript': transcript.strip(),
            'transcription_seconds': round(time.perf_counter() - started, 2),
            'chunk_count': len(chunks),
            'runtime': asdict(runtime),
        }


def _submit_runpod_request(job_input, duration=0):
    """Submit one Dayax job and normalize the RunPod queue response."""
    endpoint_id = Config.RUNPOD_ASR_ENDPOINT_ID
    wait_ms = max(1000, min(Config.RUNPOD_ASR_WAIT_MS, 300000))
    url = f'https://api.runpod.ai/v2/{endpoint_id}/runsync?wait={wait_ms}'
    payload = {'input': job_input}
    started = time.perf_counter()
    try:
        response = requests.post(
            url,
            headers={
                'Authorization': f'Bearer {Config.RUNPOD_API_KEY}',
                'Content-Type': 'application/json',
                'Accept': 'application/json',
            },
            json=payload,
            timeout=Config.RUNPOD_ASR_REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        result = response.json()
    except requests.Timeout as exc:
        raise MediaASRError(
            'RunPod Dayax transcription timed out. Retry after the GPU worker starts.'
        ) from exc
    except requests.RequestException as exc:
        detail = ''
        if getattr(exc, 'response', None) is not None:
            detail = str(exc.response.text or '')[-500:]
        raise MediaASRError(
            f'RunPod Dayax request failed. {detail or str(exc)}'
        ) from exc
    except (ValueError, TypeError) as exc:
        raise MediaASRError('RunPod returned an invalid JSON response.') from exc

    status = str(result.get('status') or '').upper()
    if status and status != 'COMPLETED':
        error = result.get('error') or result.get('output') or status
        raise MediaASRError(f'RunPod Dayax job did not complete: {error}')
    output = result.get('output')
    if isinstance(output, list) and len(output) == 1:
        output = output[0]
    if isinstance(output, str):
        transcript = output
        worker_data = {}
    elif isinstance(output, dict):
        if output.get('error'):
            detail = str(output['error'])
            error_code = str(output.get('error_code') or 'runpod_error')
            if 'audio_base64 is required' in detail.casefold() and job_input.get('media_url'):
                error_code = 'runpod_media_url_unsupported'
            raise MediaASRError(
                f'RunPod Dayax failed: {detail}',
                code=error_code,
            )
        transcript = (
            output.get('transcript')
            or output.get('text')
            or output.get('output')
            or ''
        )
        worker_data = output
    else:
        transcript = ''
        worker_data = {}
    transcript = str(transcript or '').strip()
    if not transcript:
        error = worker_data.get('error') if isinstance(worker_data, dict) else None
        raise MediaASRError(
            f'RunPod Dayax returned no transcript{": " + str(error) if error else "."}'
        )
    elapsed = time.perf_counter() - started
    execution_ms = result.get('executionTime')
    worker_transcription_seconds = worker_data.get('transcription_seconds')
    try:
        worker_transcription_seconds = float(worker_transcription_seconds)
    except (TypeError, ValueError):
        worker_transcription_seconds = elapsed
    normalized = {
        'transcript': transcript,
        'transcription_seconds': round(worker_transcription_seconds, 2),
        'chunk_count': int(worker_data.get('chunk_count') or 1),
        'runtime': {
            'model_id': Config.ASR_MODEL_ID,
            'mode': 'dayax',
            'device': str(worker_data.get('device') or 'RunPod GPU'),
            'load_seconds': round(
                float(worker_data.get('model_load_seconds') or 0),
                2,
            ),
            'cache_dir': '',
            'backend': 'runpod',
            'job_id': result.get('id'),
            'delay_seconds': round(float(result.get('delayTime') or 0) / 1000, 2),
            'execution_seconds': round(float(execution_ms or 0) / 1000, 2),
            'request_seconds': round(elapsed, 2),
            'audio_duration_seconds': round(float(duration), 2),
        },
    }
    return normalized, worker_data


def _transcribe_with_runpod(wav_path, duration):
    """Send normalized WAV audio to the configured Dayax GPU worker."""
    maximum_bytes = Config.RUNPOD_ASR_MAX_PAYLOAD_MB * 1024 * 1024
    audio_bytes = Path(wav_path).read_bytes()
    if len(audio_bytes) > maximum_bytes:
        raise MediaASRError(
            'The normalized audio is too large for one RunPod request. '
            'Use a shorter recording or increase the configured worker payload limit.'
        )
    output, _worker_data = _submit_runpod_request({
        'audio_base64': base64.b64encode(audio_bytes).decode('ascii'),
        'audio_format': 'wav',
        'sampling_rate': 16000,
        'model_id': Config.ASR_MODEL_ID,
    }, duration=duration)
    return output


def _is_youtube_url(media_url):
    hostname = (urlsplit(str(media_url or '')).hostname or '').casefold()
    return any(
        hostname == domain or hostname.endswith(f'.{domain}')
        for domain in ('youtube.com', 'youtube-nocookie.com', 'youtu.be')
    )


def _transcribe_youtube_url_with_runpod(media_url):
    """Let RunPod retrieve and transcribe YouTube audio outside the VPS IP."""
    output, worker_data = _submit_runpod_request({
        'media_url': media_url,
        'model_id': Config.ASR_MODEL_ID,
    })
    try:
        duration = float(worker_data.get('duration_seconds') or 0)
    except (TypeError, ValueError):
        duration = 0.0
    if duration > Config.ASR_MAX_DURATION_SECONDS:
        raise MediaASRError(
            f'This recording exceeds the {Config.ASR_MAX_DURATION_SECONDS // 60}-minute limit.'
        )
    output['runtime']['audio_duration_seconds'] = round(duration, 2)
    source_metadata = {
        'source_type': 'public_url',
        'source_url': media_url,
        'source_title': str(worker_data.get('source_title') or 'YouTube media').strip()[:300],
        'source_uploader': str(worker_data.get('source_uploader') or '').strip()[:200],
        'source_platform': str(worker_data.get('source_platform') or 'YouTube').strip()[:100],
        'download_seconds': round(float(worker_data.get('download_seconds') or 0), 2),
    }
    conversion_seconds = round(float(worker_data.get('conversion_seconds') or 0), 2)
    filename = str(worker_data.get('filename') or 'youtube-audio').strip()[:255]
    return output, duration, conversion_seconds, filename, source_metadata


_engines = {}
_engines_lock = threading.Lock()


def _engine_for_mode(mode):
    if mode not in Config.ASR_MODELS:
        raise MediaASRError('Select a valid ASR processing mode.')
    with _engines_lock:
        if mode not in _engines:
            _engines[mode] = SomaliASREngine(mode, Config.ASR_MODELS[mode])
        return _engines[mode]


def _output_from_cached_record(record):
    return {
        'transcript': record['raw_transcript'],
        'transcription_seconds': 0.0,
        'chunk_count': int(record.get('chunk_count') or 0),
        'runtime': {
            'model_id': Config.ASR_MODEL_ID,
            'mode': 'dayax',
            'device': record.get('device') or 'Saved transcript',
            'load_seconds': 0.0,
            'cache_dir': str(Config.ASR_TRANSCRIPT_DIR),
            'backend': 'cache',
        },
        'cache_hit': True,
    }


def _existing_url_record(validated_url):
    if not Config.ASR_RESULT_CACHE_ENABLED:
        return None
    transcript_dir = Path(Config.ASR_TRANSCRIPT_DIR)
    if not transcript_dir.is_dir():
        return None
    now = time.time()
    maximum_age = Config.ASR_URL_CACHE_TTL_SECONDS
    for path in sorted(
        transcript_dir.glob('*.json'),
        key=lambda candidate: candidate.stat().st_mtime,
        reverse=True,
    ):
        try:
            if maximum_age and now - path.stat().st_mtime > maximum_age:
                continue
            record = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            continue
        if (
            _media_url_identity(record.get('source_url'))
            == _media_url_identity(validated_url)
            and record.get('model_id') == Config.ASR_MODEL_ID
            and str(record.get('raw_transcript') or '').strip()
        ):
            return record
    return None


def _extract_atomic_transcript_claims(transcript):
    """Select one main claim, then enforce exact transcript-span integrity."""
    paragraphs = [
        paragraph.strip()
        for paragraph in re.split(r'\n\s*\n+', str(transcript or ''))
        if paragraph.strip()
    ]
    word_count = len(re.findall(r'\S+', str(transcript or '')))
    is_long_transcript = (
        len(paragraphs) > 3
        or word_count > Config.TRANSCRIPT_CLAIM_EXTRACTION_MIN_WORDS
    )
    if not is_long_transcript:
        claims = extract_complete_verbatim_claim(
            transcript,
            context_words=Config.TRANSCRIPT_CONTEXT_WORDS,
        )
        return claims, 'complete_transcript_verbatim_v6'

    candidate = extract_main_verbatim_claim_candidate(transcript)
    direct_claims = extract_single_verbatim_claim(
        transcript,
        candidate,
        context_words=Config.TRANSCRIPT_CONTEXT_WORDS,
    )
    if direct_claims:
        return direct_claims, 'llm_main_verbatim_v7'
    return [], 'main_claim_extraction_unavailable'


def _store_transcript_record(
    output,
    display_filename,
    mode,
    user_id,
    total_started,
    duration,
    conversion_seconds=0,
    source_metadata=None,
    source_duration=None,
    clip_start_seconds=0.0,
    clip_end_seconds=None,
    clip_applied=False,
):
    """Persist one completed transcript regardless of where media was fetched."""
    transcript_dir = Path(Config.ASR_TRANSCRIPT_DIR)
    transcript_dir.mkdir(parents=True, exist_ok=True)
    claims, claim_method = _extract_atomic_transcript_claims(output['transcript'])
    transcript_id = uuid.uuid4().hex
    record = {
        'transcript_id': transcript_id,
        'user_id': user_id,
        'filename': display_filename,
        'source_type': 'upload',
        'duration_seconds': round(duration, 2),
        'source_duration_seconds': round(
            float(source_duration if source_duration is not None else duration), 2
        ),
        'clip_start_seconds': round(float(clip_start_seconds or 0), 2),
        'clip_end_seconds': round(
            float(clip_end_seconds if clip_end_seconds is not None else duration), 2
        ),
        'clip_applied': bool(clip_applied),
        'conversion_seconds': round(conversion_seconds, 2),
        'transcription_seconds': output['transcription_seconds'],
        'total_processing_seconds': round(time.perf_counter() - total_started, 2),
        'model_id': output['runtime']['model_id'],
        'asr_mode': mode,
        'device': output['runtime']['device'],
        'asr_backend': output['runtime'].get('backend', 'local'),
        'cache_hit': bool(output.get('cache_hit')),
        'model_load_seconds': round(output['runtime']['load_seconds'], 2),
        'runpod_request_seconds': round(
            float(output['runtime'].get('request_seconds') or 0), 2
        ),
        'runpod_queue_seconds': round(
            float(output['runtime'].get('delay_seconds') or 0), 2
        ),
        'runpod_execution_seconds': round(
            float(output['runtime'].get('execution_seconds') or 0), 2
        ),
        'chunk_count': output['chunk_count'],
        'raw_transcript': output['transcript'],
        'claims': claims,
        'claim_method': claim_method,
    }
    if source_metadata:
        record.update(source_metadata)
    (transcript_dir / f'{transcript_id}.json').write_text(
        json.dumps(record, ensure_ascii=False, indent=2),
        encoding='utf-8',
    )
    return record


def _transcribe_media_path(
    input_path,
    display_filename,
    mode,
    user_id,
    total_started,
    source_metadata=None,
    cache_key=None,
    cache_maximum_age_seconds=None,
    clip_start_seconds=None,
    clip_end_seconds=None,
):
    transcript_dir = Path(Config.ASR_TRANSCRIPT_DIR)
    transcript_dir.mkdir(parents=True, exist_ok=True)
    wav_path = input_path.parent / 'audio_16khz_mono.wav'
    if not input_path.is_file() or not input_path.stat().st_size:
        raise MediaASRError('The media file is empty.')
    if input_path.stat().st_size > Config.ASR_MAX_UPLOAD_BYTES:
        raise MediaASRError(
            f'Media files are limited to {Config.ASR_MAX_UPLOAD_MB} MB.'
        )

    source_duration = _probe_duration(input_path)
    (
        clip_start,
        clip_end,
        duration,
        clip_applied,
    ) = _validated_clip_bounds(
        source_duration,
        clip_start_seconds=clip_start_seconds,
        clip_end_seconds=clip_end_seconds,
    )
    cached = _read_cached_transcription(
        cache_key,
        maximum_age_seconds=cache_maximum_age_seconds,
    )
    conversion_seconds = 0.0
    if cached:
        output = _cached_output(cached)
    else:
        conversion_started = time.perf_counter()
        _convert_to_wav(
            input_path,
            wav_path,
            clip_start_seconds=clip_start,
            clip_duration_seconds=duration,
        )
        conversion_seconds = time.perf_counter() - conversion_started
        backend = _selected_asr_backend()
        if backend == 'remote':
            output = _transcribe_with_runpod(wav_path, duration)
        else:
            samples = _load_wav(wav_path)
            output = _engine_for_mode(mode).transcribe(samples)
        output['cache_hit'] = False
        _write_cached_transcription(cache_key, output)

    return _store_transcript_record(
        output=output,
        display_filename=display_filename,
        mode=mode,
        user_id=user_id,
        total_started=total_started,
        duration=duration,
        conversion_seconds=conversion_seconds,
        source_metadata=source_metadata,
        source_duration=source_duration,
        clip_start_seconds=clip_start,
        clip_end_seconds=clip_end,
        clip_applied=clip_applied,
    )


def transcribe_upload(
    upload,
    mode='dayax',
    user_id=None,
    clip_start_seconds=None,
    clip_end_seconds=None,
):
    original_filename = str(upload.filename or '').strip()
    suffix = Path(original_filename).suffix.casefold()
    if suffix not in SUPPORTED_MEDIA_EXTENSIONS:
        allowed = ', '.join(sorted(SUPPORTED_MEDIA_EXTENSIONS))
        raise MediaASRError(f'Unsupported media format. Use: {allowed}.')

    runtime_dir = Path(Config.ASR_RUNTIME_DIR)
    runtime_dir.mkdir(parents=True, exist_ok=True)
    total_started = time.perf_counter()
    with tempfile.TemporaryDirectory(dir=runtime_dir) as temporary:
        temporary_dir = Path(temporary)
        safe_filename = secure_filename(original_filename) or f'uploaded{suffix}'
        input_path = temporary_dir / safe_filename
        upload.save(input_path)
        cache_seed = (
            f'{Config.ASR_MODEL_ID}:upload:'.encode('utf-8')
            + bytes.fromhex(_file_sha256(input_path))
        )
        if clip_start_seconds is not None or clip_end_seconds is not None:
            try:
                start_token = float(clip_start_seconds or 0)
                end_token = float(clip_end_seconds)
            except (TypeError, ValueError) as exc:
                raise MediaASRError(
                    'Enter valid start and end times for the media segment.'
                ) from exc
            cache_seed += (
                f':clip:{start_token:.3f}:{end_token:.3f}'.encode('utf-8')
            )
        cache_key = hashlib.sha256(cache_seed).hexdigest()
        return _transcribe_media_path(
            input_path=input_path,
            display_filename=original_filename,
            mode=mode,
            user_id=user_id,
            total_started=total_started,
            cache_key=cache_key,
            clip_start_seconds=clip_start_seconds,
            clip_end_seconds=clip_end_seconds,
        )


def _checked_remote_info(info):
    if not isinstance(info, dict):
        raise MediaASRError('The platform did not return readable media information.')
    if info.get('_type') in {'playlist', 'multi_video'} or info.get('entries'):
        raise MediaASRError('Playlist links are not supported. Paste one video or audio link.')
    live_status = str(info.get('live_status') or '').casefold()
    if info.get('is_live') or live_status in {'is_live', 'is_upcoming', 'post_live'}:
        raise MediaASRError('Livestreams are not supported. Use a completed recording.')
    duration = info.get('duration')
    try:
        duration = float(duration) if duration is not None else None
    except (TypeError, ValueError):
        duration = None
    if duration and duration > Config.ASR_MAX_DURATION_SECONDS:
        raise MediaASRError(
            f'This recording exceeds the {Config.ASR_MAX_DURATION_SECONDS // 60}-minute limit.'
        )
    size = info.get('filesize') or info.get('filesize_approx')
    try:
        size = int(size) if size is not None else None
    except (TypeError, ValueError):
        size = None
    if size and size > Config.ASR_MAX_UPLOAD_BYTES:
        raise MediaASRError(
            f'The remote media exceeds the {Config.ASR_MAX_UPLOAD_MB} MB limit.'
        )
    return info


def _yt_dlp_access_options():
    """Return optional private/runtime settings for yt-dlp."""
    options = {}
    deno_path = Config.YTDLP_DENO_PATH or shutil.which('deno') or ''
    if deno_path and Path(deno_path).is_file():
        options['js_runtimes'] = {
            'deno': {'path': str(Path(deno_path).resolve())},
        }

    cookie_file = Config.YTDLP_COOKIE_FILE
    if cookie_file and Path(cookie_file).is_file():
        options['cookiefile'] = str(Path(cookie_file).resolve())
    return options


def _download_public_media_url(media_url, destination):
    try:
        from yt_dlp import YoutubeDL
        from yt_dlp.utils import DownloadError
    except ImportError as exc:
        raise MediaASRError(
            'Public media-link support is unavailable because yt-dlp is not installed.'
        ) from exc

    common_options = {
        'quiet': True,
        'no_warnings': True,
        'noprogress': True,
        'noplaylist': True,
        'playlistend': 1,
        'socket_timeout': Config.MEDIA_URL_SOCKET_TIMEOUT_SECONDS,
        'retries': 2,
        'fragment_retries': 2,
        'concurrent_fragment_downloads': 4,
        'cachedir': False,
        'extractor_retries': 2,
        **_yt_dlp_access_options(),
    }
    try:
        with YoutubeDL({**common_options, 'skip_download': True}) as inspector:
            inspected = _checked_remote_info(
                inspector.extract_info(media_url, download=False)
            )

        output_template = str(destination / '%(extractor_key)s-%(id)s.%(ext)s')
        download_options = {
            **common_options,
            'format': 'bestaudio[acodec!=none]/bestaudio/best[acodec!=none]',
            'outtmpl': output_template,
            'max_filesize': Config.ASR_MAX_UPLOAD_BYTES,
            'restrictfilenames': True,
            'ffmpeg_location': str(Path(resolve_media_binary('ffmpeg')).parent),
        }
        with YoutubeDL(download_options) as downloader:
            downloaded_info = _checked_remote_info(
                downloader.extract_info(media_url, download=True)
            )
    except MediaASRError:
        raise
    except DownloadError as exc:
        detail = re.sub(r'\s+', ' ', str(exc)).strip()
        normalized_detail = detail.casefold()
        if (
            'sign in to confirm you’re not a bot' in normalized_detail
            or "sign in to confirm you're not a bot" in normalized_detail
            or 'use --cookies-from-browser or --cookies' in normalized_detail
        ):
            raise MediaASRError(
                'YouTube blocked automatic retrieval from this cloud server. '
                'Upload the audio or video file instead. An administrator can '
                'also configure a private server-side YouTube cookie file.',
                code='media_auth_required',
            ) from exc
        raise MediaASRError(
            'Could not retrieve this public media link. It may be private, '
            'login-protected, unavailable, or unsupported by the platform.',
            code='media_download_failed',
        ) from exc
    except Exception as exc:
        raise MediaASRError(
            f'The public media link could not be processed: {str(exc)[:350]}'
        ) from exc

    ignored_suffixes = {'.part', '.ytdl', '.json', '.jpg', '.jpeg', '.png', '.webp'}
    candidates = [
        path for path in destination.rglob('*')
        if path.is_file() and path.suffix.casefold() not in ignored_suffixes
    ]
    if not candidates:
        raise MediaASRError('The platform did not provide a downloadable audio track.')
    media_path = max(candidates, key=lambda path: path.stat().st_size)
    if not media_path.resolve().is_relative_to(destination.resolve()):
        raise MediaASRError('The downloaded media path was rejected.')
    if media_path.stat().st_size > Config.ASR_MAX_UPLOAD_BYTES:
        raise MediaASRError(
            f'The downloaded media exceeds the {Config.ASR_MAX_UPLOAD_MB} MB limit.'
        )
    return media_path, downloaded_info or inspected


def transcribe_media_url(media_url, mode='dayax', user_id=None):
    validated_url = _validate_public_media_url(media_url)
    runtime_dir = Path(Config.ASR_RUNTIME_DIR)
    runtime_dir.mkdir(parents=True, exist_ok=True)
    total_started = time.perf_counter()
    cache_key = hashlib.sha256(
        f'{Config.ASR_MODEL_ID}:url:{_media_url_identity(validated_url)}'.encode('utf-8')
    ).hexdigest()
    existing_record = _existing_url_record(validated_url)
    if existing_record:
        output = _output_from_cached_record(existing_record)
        if (
            existing_record.get('claim_method') in {
                'complete_transcript_verbatim_v6',
                'llm_main_cohesive_verbatim_v6',
                'llm_main_verbatim_v6',
                'llm_main_cohesive_fallback_v6',
            }
            and isinstance(existing_record.get('claims'), list)
        ):
            claims = existing_record['claims']
            claim_method = existing_record['claim_method']
        else:
            claims, claim_method = _extract_atomic_transcript_claims(
                output['transcript']
            )
            existing_record['claims'] = claims
            existing_record['claim_method'] = claim_method
            transcript_id = str(existing_record.get('transcript_id') or '').strip()
            if re.fullmatch(r'[a-f0-9]{32}', transcript_id):
                record_path = Path(Config.ASR_TRANSCRIPT_DIR) / f'{transcript_id}.json'
                record_path.write_text(
                    json.dumps(existing_record, ensure_ascii=False, indent=2),
                    encoding='utf-8',
                )
        return {
            **existing_record,
            'user_id': user_id,
            'conversion_seconds': 0.0,
            'transcription_seconds': 0.0,
            'total_processing_seconds': round(time.perf_counter() - total_started, 2),
            'asr_mode': mode,
            'asr_backend': 'cache',
            'cache_hit': True,
            'model_load_seconds': 0.0,
            'claims': claims,
            'claim_method': claim_method,
        }

    if _selected_asr_backend() == 'remote' and _is_youtube_url(validated_url):
        try:
            (
                output,
                duration,
                conversion_seconds,
                display_filename,
                source_metadata,
            ) = _transcribe_youtube_url_with_runpod(validated_url)
        except MediaASRError as exc:
            # Keep compatibility with the currently deployed audio-only worker
            # while its container is being upgraded to the media_url contract.
            if exc.code != 'runpod_media_url_unsupported':
                raise
        else:
            output['cache_hit'] = False
            _write_cached_transcription(cache_key, output)
            return _store_transcript_record(
                output=output,
                display_filename=display_filename,
                mode=mode,
                user_id=user_id,
                total_started=total_started,
                duration=duration,
                conversion_seconds=conversion_seconds,
                source_metadata=source_metadata,
            )

    with tempfile.TemporaryDirectory(dir=runtime_dir) as temporary:
        temporary_dir = Path(temporary)
        download_started = time.perf_counter()
        media_path, info = _download_public_media_url(validated_url, temporary_dir)
        download_seconds = time.perf_counter() - download_started
        title = str(info.get('title') or media_path.stem).strip()[:300]
        uploader = str(info.get('uploader') or info.get('channel') or '').strip()[:200]
        platform = str(
            info.get('extractor_key') or info.get('extractor') or urlsplit(validated_url).hostname
        ).strip()[:100]
        return _transcribe_media_path(
            input_path=media_path,
            display_filename=media_path.name,
            mode=mode,
            user_id=user_id,
            total_started=total_started,
            source_metadata={
                'source_type': 'public_url',
                'source_url': validated_url,
                'source_title': title,
                'source_uploader': uploader,
                'source_platform': platform,
                'download_seconds': round(download_seconds, 2),
            },
            cache_key=cache_key,
            cache_maximum_age_seconds=Config.ASR_URL_CACHE_TTL_SECONDS,
        )
