"""RunPod Serverless worker for IbrahimDayax Somali speech recognition."""
import base64
import io
import os
import re
import subprocess
import tempfile
import time
import wave
from pathlib import Path
from urllib.parse import urlsplit

import numpy as np
import runpod
import torch
from transformers import AutoModelForCTC, AutoProcessor
from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError


MODEL_ID = os.getenv(
    'ASR_MODEL_ID',
    'IbrahimDayax/xls-r-1b-somali-stt-asr',
)
HF_CACHE_ROOT = Path(os.getenv(
    'RUNPOD_HF_CACHE_ROOT',
    '/runpod-volume/huggingface-cache/hub',
))
SAMPLE_RATE = 16000
CHUNK_SECONDS = float(os.getenv('ASR_CHUNK_SECONDS', '28'))
OVERLAP_SECONDS = float(os.getenv('ASR_OVERLAP_SECONDS', '1'))
MAX_AUDIO_BYTES = int(os.getenv('MAX_AUDIO_BYTES', str(13 * 1024 * 1024)))
GPU_BATCH_SIZE = max(1, int(os.getenv('ASR_GPU_BATCH_SIZE', '2')))
MAX_MEDIA_DURATION_SECONDS = int(os.getenv('MAX_MEDIA_DURATION_SECONDS', '1800'))
MAX_REMOTE_MEDIA_BYTES = int(os.getenv('MAX_REMOTE_MEDIA_BYTES', str(100 * 1024 * 1024)))
MAX_REMOTE_WAV_BYTES = MAX_MEDIA_DURATION_SECONDS * SAMPLE_RATE * 2 + 44
MEDIA_DOWNLOAD_TIMEOUT_SECONDS = int(os.getenv('MEDIA_DOWNLOAD_TIMEOUT_SECONDS', '180'))
YOUTUBE_DOMAINS = ('youtube.com', 'youtube-nocookie.com', 'youtu.be')
BGUTIL_SERVER_HOME = '/opt/bgutil-ytdlp-pot-provider/server'


def _resolve_cached_model_path(model_id):
    """Return RunPod's mounted Hugging Face snapshot when it is available."""
    if '/' not in model_id:
        return model_id

    organization, name = model_id.split('/', 1)
    model_root = HF_CACHE_ROOT / f'models--{organization}--{name}'
    snapshots_root = model_root / 'snapshots'
    candidates = []

    refs_main = model_root / 'refs' / 'main'
    if refs_main.is_file():
        try:
            revision = refs_main.read_text(encoding='utf-8').strip()
        except OSError:
            revision = ''
        if revision:
            candidates.append(snapshots_root / revision)

    if snapshots_root.is_dir():
        try:
            candidates.extend(
                path for path in snapshots_root.iterdir() if path.is_dir()
            )
        except OSError:
            pass

    for candidate in candidates:
        if (candidate / 'config.json').is_file():
            return str(candidate)
    return model_id


def _load_model():
    started = time.perf_counter()
    if not torch.cuda.is_available():
        raise RuntimeError('This Dayax worker requires a CUDA GPU.')
    token = os.getenv('HF_TOKEN', '').strip() or None
    model_source = _resolve_cached_model_path(MODEL_ID)
    cached_model = model_source != MODEL_ID
    if cached_model:
        print(f'[ASR] Loading cached Dayax model from {model_source}', flush=True)
    else:
        print(
            '[ASR] RunPod model cache was not mounted; downloading Dayax from '
            'Hugging Face.',
            flush=True,
        )
    load_options = {
        'local_files_only': cached_model,
    }
    if not cached_model:
        load_options['token'] = token
    processor = AutoProcessor.from_pretrained(model_source, **load_options)
    try:
        model = AutoModelForCTC.from_pretrained(
            model_source,
            dtype=torch.float16,
            low_cpu_mem_usage=True,
            **load_options,
        )
    except TypeError:
        model = AutoModelForCTC.from_pretrained(
            model_source,
            torch_dtype=torch.float16,
            low_cpu_mem_usage=True,
            **load_options,
        )
    model.to('cuda')
    model.eval()
    return processor, model, round(time.perf_counter() - started, 2)


# Load once when the worker starts, not once per audio request.
PROCESSOR, MODEL, MODEL_LOAD_SECONDS = _load_model()


def _validate_youtube_url(raw_url):
    value = str(raw_url or '').strip()
    if not value or len(value) > 2048:
        raise ValueError('A valid public YouTube URL is required.')
    parsed = urlsplit(value)
    hostname = (parsed.hostname or '').casefold()
    if parsed.scheme.casefold() not in {'http', 'https'}:
        raise ValueError('The YouTube URL must use HTTP or HTTPS.')
    if parsed.username or parsed.password or parsed.port not in (None, 80, 443):
        raise ValueError('The YouTube URL contains unsupported credentials or a port.')
    if not any(
        hostname == domain or hostname.endswith(f'.{domain}')
        for domain in YOUTUBE_DOMAINS
    ):
        raise ValueError('Only public YouTube video links are accepted by this worker.')
    return value


def _run(command, timeout):
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or 'Media conversion failed.'
        raise RuntimeError(detail[-1000:])
    return completed


def _probe_duration(media_path):
    completed = _run([
        'ffprobe', '-v', 'error', '-show_entries', 'format=duration',
        '-of', 'default=noprint_wrappers=1:nokey=1', str(media_path),
    ], timeout=30)
    try:
        duration = float(completed.stdout.strip())
    except (TypeError, ValueError) as exc:
        raise ValueError('The YouTube audio duration could not be determined.') from exc
    if duration <= 0:
        raise ValueError('The YouTube audio duration is invalid.')
    if duration > MAX_MEDIA_DURATION_SECONDS:
        raise ValueError(
            f'This recording exceeds the {MAX_MEDIA_DURATION_SECONDS // 60}-minute limit.'
        )
    return duration


def _youtube_cookie_file(destination):
    """Materialize an optional Netscape cookie secret without logging it."""
    encoded = os.getenv('YOUTUBE_COOKIES_B64', '').strip()
    if not encoded:
        return None
    if len(encoded) > 4 * 1024 * 1024:
        raise ValueError('The configured YouTube cookie secret is too large.')
    try:
        padding = '=' * (-len(encoded) % 4)
        cookie_bytes = base64.b64decode(encoded + padding, validate=True)
    except (ValueError, TypeError) as exc:
        raise ValueError('YOUTUBE_COOKIES_B64 is not valid base64.') from exc
    if not cookie_bytes or len(cookie_bytes) > 2 * 1024 * 1024:
        raise ValueError('The configured YouTube cookie file is empty or too large.')
    cookie_text = cookie_bytes.decode('utf-8', errors='strict')
    if 'youtube.com' not in cookie_text.casefold():
        raise ValueError('The configured cookie file does not contain YouTube cookies.')
    cookie_path = destination / 'youtube-cookies.txt'
    cookie_path.write_text(cookie_text, encoding='utf-8')
    cookie_path.chmod(0o600)
    return cookie_path


def _youtube_proxy_url():
    value = os.getenv('YOUTUBE_PROXY_URL', '').strip()
    if not value:
        return ''
    parsed = urlsplit(value)
    if parsed.scheme.casefold() not in {'http', 'https', 'socks5', 'socks5h'}:
        raise ValueError('YOUTUBE_PROXY_URL uses an unsupported proxy scheme.')
    if not parsed.hostname:
        raise ValueError('YOUTUBE_PROXY_URL is invalid.')
    return value


def _download_youtube_audio(media_url, destination):
    started = time.perf_counter()
    cookie_file = _youtube_cookie_file(destination)
    proxy_url = _youtube_proxy_url()
    base_options = {
        'quiet': True,
        'no_warnings': True,
        'noprogress': True,
        'noplaylist': True,
        'playlistend': 1,
        'max_filesize': MAX_REMOTE_MEDIA_BYTES,
        'restrictfilenames': True,
        'socket_timeout': 30,
        'retries': 2,
        'fragment_retries': 2,
        'extractor_retries': 2,
        'concurrent_fragment_downloads': 4,
        'cachedir': False,
        'ffmpeg_location': '/usr/bin',
        'js_runtimes': {'deno': {'path': '/usr/local/bin/deno'}},
    }
    if cookie_file:
        base_options['cookiefile'] = str(cookie_file)
    if proxy_url:
        base_options['proxy'] = proxy_url

    # YouTube increasingly requires playback attestation for direct HTTPS
    # formats.  HLS from the Android VR client is the documented no-token
    # fallback; web_embedded is retained for videos unavailable to that client.
    strategies = []
    if cookie_file:
        strategies.append({
            'name': 'authenticated-mweb-pot',
            'format': 'bestaudio[acodec!=none]/best[acodec!=none]',
            'extractor_args': {
                'youtube': {'player_client': ['mweb']},
                'youtubepot-bgutilscript': {
                    'server_home': [BGUTIL_SERVER_HOME],
                },
            },
        })
    strategies.extend((
        {
            'name': 'default-audio',
            'format': 'bestaudio[acodec!=none]/bestaudio/best[acodec!=none]',
        },
        {
            'name': 'android-vr-hls',
            'format': (
                'worst[protocol=m3u8_native][acodec!=none]/'
                'worst[protocol=m3u8][acodec!=none]/'
                'best[protocol=m3u8_native][acodec!=none]/'
                'best[protocol=m3u8][acodec!=none]'
            ),
            'extractor_args': {
                'youtube': {'player_client': ['android_vr']},
            },
        },
        {
            'name': 'web-embedded',
            'format': 'bestaudio[acodec!=none]/best[acodec!=none]',
            'extractor_args': {
                'youtube': {'player_client': ['web_embedded']},
            },
        },
    ))
    failures = []
    info = None
    attempt_dir = None
    for strategy in strategies:
        attempt_dir = destination / strategy['name']
        attempt_dir.mkdir(parents=True, exist_ok=True)
        options = {
            **base_options,
            'format': strategy['format'],
            'outtmpl': str(attempt_dir / 'youtube-%(id)s.%(ext)s'),
        }
        if strategy.get('extractor_args'):
            options['extractor_args'] = strategy['extractor_args']
        try:
            with YoutubeDL(options) as downloader:
                info = downloader.extract_info(media_url, download=True)
            break
        except DownloadError as exc:
            detail = re.sub(r'\s+', ' ', str(exc)).strip()
            failures.append(f"{strategy['name']}: {detail[:300]}")
            info = None

    if info is None or attempt_dir is None:
        combined = ' | '.join(failures)
        normalized = combined.casefold()
        if (
            'sign in to confirm' in normalized
            or 'not a bot' in normalized
            or 'use --cookies-from-browser or --cookies' in normalized
        ):
            raise RuntimeError('YOUTUBE_AUTH_REQUIRED')
        raise RuntimeError(f'YouTube retrieval failed: {combined[:900]}')

    if not isinstance(info, dict) or info.get('_type') in {'playlist', 'multi_video'}:
        raise ValueError('Playlist and multi-video links are not supported.')
    if info.get('is_live') or str(info.get('live_status') or '').casefold() in {
        'is_live', 'is_upcoming', 'post_live',
    }:
        raise ValueError('Livestreams are not supported.')

    ignored = {'.part', '.ytdl', '.json', '.jpg', '.jpeg', '.png', '.webp'}
    candidates = [
        path for path in attempt_dir.rglob('*')
        if path.is_file() and path.suffix.casefold() not in ignored
    ]
    if not candidates:
        raise RuntimeError('YouTube did not provide a downloadable audio track.')
    media_path = max(candidates, key=lambda path: path.stat().st_size)
    if media_path.stat().st_size > MAX_REMOTE_MEDIA_BYTES:
        raise ValueError('The downloaded YouTube media exceeds the worker size limit.')
    return media_path, info, round(time.perf_counter() - started, 2)


def _convert_to_wav(input_path, wav_path):
    started = time.perf_counter()
    _run([
        'ffmpeg', '-y', '-v', 'error', '-i', str(input_path), '-vn',
        '-ac', '1', '-ar', str(SAMPLE_RATE), '-c:a', 'pcm_s16le', str(wav_path),
    ], timeout=MEDIA_DOWNLOAD_TIMEOUT_SECONDS)
    if not wav_path.is_file() or not wav_path.stat().st_size:
        raise RuntimeError('YouTube audio conversion produced an empty WAV file.')
    return round(time.perf_counter() - started, 2)


def _read_wav(audio_bytes):
    try:
        with wave.open(io.BytesIO(audio_bytes), 'rb') as audio:
            if (
                audio.getnchannels() != 1
                or audio.getsampwidth() != 2
                or audio.getframerate() != SAMPLE_RATE
            ):
                raise ValueError('Audio must be 16 kHz mono PCM WAV.')
            raw = audio.readframes(audio.getnframes())
    except wave.Error as exc:
        raise ValueError('The supplied WAV audio is invalid.') from exc
    samples = np.frombuffer(raw, dtype='<i2').astype(np.float32) / 32768.0
    if not samples.size:
        raise ValueError('The supplied audio is empty.')
    return samples


def _split_audio(samples):
    chunk_size = int(CHUNK_SECONDS * SAMPLE_RATE)
    overlap_size = int(OVERLAP_SECONDS * SAMPLE_RATE)
    step = chunk_size - overlap_size
    if step <= 0:
        raise ValueError('Invalid chunk and overlap configuration.')
    if len(samples) <= chunk_size:
        return [samples]
    chunks = []
    for start in range(0, len(samples), step):
        chunk = samples[start:start + chunk_size]
        if len(chunk) < SAMPLE_RATE:
            break
        chunks.append(chunk)
        if start + chunk_size >= len(samples):
            break
    return chunks


def _merge_transcripts(existing, incoming, maximum_overlap_words=18):
    left = str(existing or '').strip().split()
    right = str(incoming or '').strip().split()
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


def _transcribe_batch(chunks):
    inputs = PROCESSOR(
        chunks,
        sampling_rate=SAMPLE_RATE,
        return_tensors='pt',
        padding=True,
    )
    model_inputs = {
        key: value.to(device='cuda', dtype=torch.float16)
        if value.is_floating_point()
        else value.to('cuda')
        for key, value in inputs.items()
    }
    with torch.inference_mode():
        token_ids = torch.argmax(MODEL(**model_inputs).logits, dim=-1).cpu()
    return [text.strip() for text in PROCESSOR.batch_decode(token_ids)]


def handler(job):
    job_input = job.get('input') or {}
    encoded_audio = str(job_input.get('audio_base64') or '').strip()
    media_url = str(job_input.get('media_url') or '').strip()
    if bool(encoded_audio) == bool(media_url):
        return {
            'error': 'Provide exactly one of audio_base64 or media_url.',
            'error_code': 'invalid_worker_input',
        }

    source_metadata = {}
    duration = 0.0
    if encoded_audio:
        try:
            audio_bytes = base64.b64decode(encoded_audio, validate=True)
        except (ValueError, TypeError) as exc:
            return {
                'error': f'audio_base64 is invalid: {exc}',
                'error_code': 'invalid_audio_base64',
            }
        maximum_audio_bytes = MAX_AUDIO_BYTES
    else:
        try:
            validated_url = _validate_youtube_url(media_url)
            with tempfile.TemporaryDirectory() as temporary:
                temporary_dir = Path(temporary)
                media_path, info, download_seconds = _download_youtube_audio(
                    validated_url,
                    temporary_dir,
                )
                duration = _probe_duration(media_path)
                wav_path = temporary_dir / 'audio_16khz_mono.wav'
                conversion_seconds = _convert_to_wav(media_path, wav_path)
                audio_bytes = wav_path.read_bytes()
                source_metadata = {
                    'source_title': str(info.get('title') or 'YouTube media').strip()[:300],
                    'source_uploader': str(
                        info.get('uploader') or info.get('channel') or ''
                    ).strip()[:200],
                    'source_platform': 'YouTube',
                    'filename': media_path.name,
                    'duration_seconds': round(duration, 2),
                    'download_seconds': download_seconds,
                    'conversion_seconds': conversion_seconds,
                }
        except RuntimeError as exc:
            if str(exc) == 'YOUTUBE_AUTH_REQUIRED':
                return {
                    'error': 'YouTube also blocked retrieval from the GPU worker.',
                    'error_code': 'media_auth_required',
                }
            return {
                'error': str(exc),
                'error_code': 'media_download_failed',
            }
        except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
            return {
                'error': str(exc),
                'error_code': 'media_download_failed',
            }
        maximum_audio_bytes = MAX_REMOTE_WAV_BYTES

    if not audio_bytes or len(audio_bytes) > maximum_audio_bytes:
        return {
            'error': 'Audio is empty or exceeds the worker limit.',
            'error_code': 'audio_size_invalid',
        }

    try:
        samples = _read_wav(audio_bytes)
        if not duration:
            duration = len(samples) / SAMPLE_RATE
        chunks = _split_audio(samples)
        started = time.perf_counter()
        transcript = ''
        for start in range(0, len(chunks), GPU_BATCH_SIZE):
            batch = chunks[start:start + GPU_BATCH_SIZE]
            for chunk_text in _transcribe_batch(batch):
                transcript = _merge_transcripts(transcript, chunk_text)
        if not transcript.strip():
            return {'error': 'Dayax did not produce a transcript.'}
        return {
            'transcript': transcript.strip(),
            'model_id': MODEL_ID,
            'device': f'RunPod GPU: {torch.cuda.get_device_name(0)}',
            'model_load_seconds': MODEL_LOAD_SECONDS,
            'transcription_seconds': round(time.perf_counter() - started, 2),
            'chunk_count': len(chunks),
            'duration_seconds': round(duration, 2),
            **source_metadata,
        }
    except (RuntimeError, ValueError) as exc:
        return {'error': str(exc)}


runpod.serverless.start({'handler': handler})
