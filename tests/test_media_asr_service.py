import io
import json
import math
import struct
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from werkzeug.datastructures import FileStorage

from config import Config
from services.media_asr_service import (
    MediaASRError,
    _extract_atomic_transcript_claims,
    _is_youtube_url,
    _media_url_identity,
    _selected_asr_backend,
    _validated_clip_bounds,
    _validate_public_media_url,
    _yt_dlp_access_options,
    media_runtime_status,
    transcribe_upload,
    transcribe_media_url,
)


def _one_second_wav(seconds=1):
    stream = io.BytesIO()
    with wave.open(stream, 'wb') as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        frames = bytearray()
        for index in range(int(16000 * seconds)):
            sample = int(2500 * math.sin(2 * math.pi * 220 * index / 16000))
            frames.extend(struct.pack('<h', sample))
        audio.writeframes(bytes(frames))
    stream.seek(0)
    return stream


class _FakeEngine:
    def transcribe(self, _samples):
        transcript = (
            'Baarlamaanka ayaa maanta ansixiyay sharciga doorashooyinka dalka.'
        )
        return {
            'transcript': transcript,
            'transcription_seconds': 0.01,
            'chunk_count': 1,
            'runtime': {
                'model_id': 'test/asr',
                'mode': 'dayax',
                'device': 'CPU',
                'load_seconds': 0.01,
                'cache_dir': 'test-cache',
                'backend': 'local',
            },
        }


class MediaASRServiceTests(unittest.TestCase):
    def test_yt_dlp_uses_configured_runtime_and_private_cookie_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Path(temporary) / 'deno'
            cookie_file = Path(temporary) / 'cookies.txt'
            runtime.write_bytes(b'fake runtime')
            cookie_file.write_text('# Netscape HTTP Cookie File\n', encoding='utf-8')
            with (
                patch.object(Config, 'YTDLP_DENO_PATH', str(runtime)),
                patch.object(Config, 'YTDLP_COOKIE_FILE', str(cookie_file)),
            ):
                options = _yt_dlp_access_options()
        self.assertEqual(options['js_runtimes']['deno']['path'], str(runtime.resolve()))
        self.assertEqual(options['cookiefile'], str(cookie_file.resolve()))

    def test_short_transcript_keeps_complete_substantive_text(self):
        transcript = (
            'kooxda alshabaab ayaa saaka weerar culus ku qaaday degmada '
            'ceeldheer ee gobolka galgaduud dowladda federaalka soomaaliya '
            'ayaa sheegtay in ciidamada ay iska caabiyeen weerarka'
        )
        with patch(
            'services.media_asr_service.extract_main_verbatim_claim_candidate'
        ) as selector:
            claims, method = _extract_atomic_transcript_claims(transcript)
        selector.assert_not_called()
        self.assertEqual(method, 'complete_transcript_verbatim_v6')
        self.assertEqual(claims[0]['exact_text'], transcript)

    def test_long_transcript_uses_only_one_llm_selector(self):
        transcript = ' '.join(
            ['qoraal'] * 181
            + ['baarlamaanka', 'ayaa', 'ansixiyay', 'sharciga', 'doorashooyinka']
        )
        candidate = 'baarlamaanka ayaa ansixiyay sharciga doorashooyinka'
        with (
            patch(
                'services.media_asr_service.extract_main_verbatim_claim_candidate',
                return_value=candidate,
            ) as selector,
            patch(
                'services.gemini_service.extract_verbatim_claim_candidates'
            ) as removed_selector,
        ):
            claims, method = _extract_atomic_transcript_claims(transcript)
        selector.assert_called_once_with(transcript)
        removed_selector.assert_not_called()
        self.assertEqual(method, 'llm_main_verbatim_v7')
        self.assertEqual(claims[0]['exact_text'], candidate)

    def test_auto_backend_uses_runpod_when_credentials_exist(self):
        with (
            patch.object(Config, 'ASR_BACKEND', 'auto'),
            patch.object(Config, 'RUNPOD_API_KEY', 'secret'),
            patch.object(Config, 'RUNPOD_ASR_ENDPOINT_ID', 'endpoint'),
        ):
            self.assertEqual(_selected_asr_backend(), 'remote')

    def test_remote_backend_requires_credentials(self):
        with (
            patch.object(Config, 'ASR_BACKEND', 'remote'),
            patch.object(Config, 'RUNPOD_API_KEY', ''),
            patch.object(Config, 'RUNPOD_ASR_ENDPOINT_ID', ''),
        ):
            with self.assertRaises(MediaASRError):
                _selected_asr_backend()

    def test_ffmpeg_runtime_is_available(self):
        self.assertTrue(media_runtime_status()['ready'])

    def test_upload_preserves_raw_transcript_and_exact_claim(self):
        with tempfile.TemporaryDirectory() as temporary:
            runtime_dir = Path(temporary) / 'runtime'
            transcript_dir = Path(temporary) / 'transcripts'
            upload = FileStorage(
                stream=_one_second_wav(),
                filename='government-report.wav',
                content_type='audio/wav',
            )
            with (
                patch.object(Config, 'ASR_RUNTIME_DIR', str(runtime_dir)),
                patch.object(Config, 'ASR_TRANSCRIPT_DIR', str(transcript_dir)),
                patch(
                    'services.media_asr_service._engine_for_mode',
                    return_value=_FakeEngine(),
                ),
            ):
                result = transcribe_upload(upload, mode='dayax', user_id=7)

            self.assertEqual(
                result['claims'][0]['classification_text'],
                result['raw_transcript'],
            )
            stored = json.loads(
                (transcript_dir / f"{result['transcript_id']}.json").read_text(
                    encoding='utf-8'
                )
            )
            self.assertEqual(stored['raw_transcript'], result['raw_transcript'])
            self.assertEqual(stored['user_id'], 7)

    def test_upload_transcribes_only_selected_segment(self):
        with tempfile.TemporaryDirectory() as temporary:
            runtime_dir = Path(temporary) / 'runtime'
            transcript_dir = Path(temporary) / 'transcripts'
            cache_dir = Path(temporary) / 'cache'
            upload = FileStorage(
                stream=_one_second_wav(seconds=2),
                filename='long-government-report.wav',
                content_type='audio/wav',
            )
            with (
                patch.object(Config, 'ASR_BACKEND', 'local'),
                patch.object(Config, 'ASR_RESULT_CACHE_ENABLED', False),
                patch.object(Config, 'ASR_RUNTIME_DIR', str(runtime_dir)),
                patch.object(Config, 'ASR_TRANSCRIPT_DIR', str(transcript_dir)),
                patch.object(Config, 'ASR_RESULT_CACHE_DIR', str(cache_dir)),
                patch(
                    'services.media_asr_service._engine_for_mode',
                    return_value=_FakeEngine(),
                ),
            ):
                result = transcribe_upload(
                    upload,
                    mode='dayax',
                    user_id=7,
                    clip_start_seconds=0.25,
                    clip_end_seconds=1.5,
                )

        self.assertTrue(result['clip_applied'])
        self.assertAlmostEqual(result['source_duration_seconds'], 2.0, places=1)
        self.assertAlmostEqual(result['duration_seconds'], 1.25, places=1)
        self.assertAlmostEqual(result['clip_start_seconds'], 0.25, places=2)
        self.assertAlmostEqual(result['clip_end_seconds'], 1.5, places=2)

    def test_segment_bounds_reject_end_beyond_source(self):
        with self.assertRaises(MediaASRError):
            _validated_clip_bounds(20, clip_start_seconds=5, clip_end_seconds=25)

    def test_public_youtube_link_is_accepted(self):
        public_dns = [
            (2, 1, 6, '', ('142.250.191.238', 443)),
        ]
        with patch(
            'services.media_asr_service.socket.getaddrinfo',
            return_value=public_dns,
        ):
            value = _validate_public_media_url(
                'https://www.youtube.com/watch?v=abc123&utm_source=test#fragment'
            )
        self.assertEqual(
            value,
            'https://www.youtube.com/watch?v=abc123&utm_source=test',
        )

    def test_youtube_url_variants_share_one_media_identity(self):
        expected = 'youtube:D8ZRfRfSdZs'
        self.assertEqual(
            _media_url_identity('https://youtube.com/shorts/D8ZRfRfSdZs'),
            expected,
        )
        self.assertEqual(
            _media_url_identity(
                'https://youtube.com/shorts/D8ZRfRfSdZs?si=tracking-value'
            ),
            expected,
        )
        self.assertEqual(
            _media_url_identity('https://youtu.be/D8ZRfRfSdZs?t=10'),
            expected,
        )
        self.assertEqual(
            _media_url_identity('https://www.youtube.com/watch?v=D8ZRfRfSdZs'),
            expected,
        )

    def test_remote_youtube_url_skips_google_cloud_downloader(self):
        public_dns = [(2, 1, 6, '', ('142.250.191.238', 443))]
        transcript = 'Baarlamaanka ayaa maanta ansixiyay sharci cusub.'
        remote_output = {
            'transcript': transcript,
            'transcription_seconds': 2.5,
            'chunk_count': 1,
            'runtime': {
                'model_id': Config.ASR_MODEL_ID,
                'mode': 'dayax',
                'device': 'RunPod GPU',
                'load_seconds': 0.0,
                'backend': 'runpod',
            },
        }
        source_metadata = {
            'source_type': 'public_url',
            'source_url': 'https://youtu.be/D8ZRfRfSdZs',
            'source_title': 'Somali news',
            'source_uploader': 'Somali channel',
            'source_platform': 'YouTube',
            'download_seconds': 1.2,
        }
        with tempfile.TemporaryDirectory() as temporary:
            runtime_dir = Path(temporary) / 'runtime'
            transcript_dir = Path(temporary) / 'transcripts'
            cache_dir = Path(temporary) / 'cache'
            with (
                patch.object(Config, 'ASR_BACKEND', 'remote'),
                patch.object(Config, 'RUNPOD_API_KEY', 'secret'),
                patch.object(Config, 'RUNPOD_ASR_ENDPOINT_ID', 'endpoint'),
                patch.object(Config, 'ASR_RESULT_CACHE_ENABLED', False),
                patch.object(Config, 'ASR_RUNTIME_DIR', str(runtime_dir)),
                patch.object(Config, 'ASR_TRANSCRIPT_DIR', str(transcript_dir)),
                patch.object(Config, 'ASR_RESULT_CACHE_DIR', str(cache_dir)),
                patch(
                    'services.media_asr_service.socket.getaddrinfo',
                    return_value=public_dns,
                ),
                patch(
                    'services.media_asr_service._transcribe_youtube_url_with_runpod',
                    return_value=(
                        remote_output,
                        72.0,
                        0.5,
                        'youtube-D8ZRfRfSdZs.webm',
                        source_metadata,
                    ),
                ) as remote_transcribe,
                patch(
                    'services.media_asr_service._download_public_media_url'
                ) as local_download,
            ):
                result = transcribe_media_url(
                    'https://youtu.be/D8ZRfRfSdZs',
                    mode='dayax',
                    user_id=7,
                )

        remote_transcribe.assert_called_once()
        local_download.assert_not_called()
        self.assertEqual(result['raw_transcript'], transcript)
        self.assertEqual(result['asr_backend'], 'runpod')
        self.assertEqual(result['source_platform'], 'YouTube')
        self.assertTrue(_is_youtube_url(result['source_url']))

    def test_existing_public_url_transcript_skips_download_and_inference(self):
        public_dns = [(2, 1, 6, '', ('142.250.191.238', 443))]
        transcript = 'Golaha wasiirrada ayaa maanta yeeshay kulan siyaasadeed.'
        with tempfile.TemporaryDirectory() as temporary:
            transcript_dir = Path(temporary) / 'transcripts'
            transcript_dir.mkdir()
            (transcript_dir / 'previous.json').write_text(
                json.dumps({
                    'transcript_id': 'previous',
                    'source_url': 'https://youtu.be/example?si=old-tracking',
                    'model_id': Config.ASR_MODEL_ID,
                    'raw_transcript': transcript,
                    'duration_seconds': 15.0,
                    'chunk_count': 1,
                    'device': 'CPU',
                }),
                encoding='utf-8',
            )
            with (
                patch.object(Config, 'ASR_TRANSCRIPT_DIR', str(transcript_dir)),
                patch.object(Config, 'ASR_RESULT_CACHE_ENABLED', True),
                patch(
                    'services.media_asr_service.socket.getaddrinfo',
                    return_value=public_dns,
                ),
                patch(
                    'services.media_asr_service._download_public_media_url'
                ) as download,
            ):
                from services.media_asr_service import transcribe_media_url

                result = transcribe_media_url(
                    'https://youtu.be/example',
                    mode='dayax',
                    user_id=4,
                )
            download.assert_not_called()
            self.assertTrue(result['cache_hit'])
            self.assertEqual(result['asr_backend'], 'cache')
            self.assertEqual(result['raw_transcript'], transcript)

    def test_private_or_unsupported_media_link_is_rejected(self):
        with self.assertRaises(MediaASRError):
            _validate_public_media_url('http://127.0.0.1:5000/private.mp4')
        with self.assertRaises(MediaASRError):
            _validate_public_media_url('https://example.com/video.mp4')


if __name__ == '__main__':
    unittest.main()
