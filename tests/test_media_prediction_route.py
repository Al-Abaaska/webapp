import io
import os
import unittest
from unittest.mock import patch


os.environ['DATABASE_URL'] = 'sqlite:///:memory:'

from app import app  # noqa: E402
from models import User, db  # noqa: E402


class _FakeMLService:
    def get_model_list(self):
        return [
            {
                'key': 'svm',
                'name': 'SVM',
                'available': True,
            }
        ]


class MediaPredictionRouteTests(unittest.TestCase):
    def setUp(self):
        app.config.update(TESTING=True)
        self.client = app.test_client()
        with app.app_context():
            db.drop_all()
            db.create_all()
            user = User(
                username='media-user',
                email='media@example.com',
                role='user',
                email_verified=True,
            )
            user.set_password('password123')
            db.session.add(user)
            db.session.commit()
            user_id = user.id
        with self.client.session_transaction() as session:
            session['_user_id'] = str(user_id)
            session['_fresh'] = True

    def tearDown(self):
        with app.app_context():
            db.session.remove()
            db.drop_all()

    def test_prediction_page_has_separate_text_and_media_sections(self):
        with patch('controllers.predict.MLService', return_value=_FakeMLService()):
            response = self.client.get('/predict')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Text only', response.data)
        self.assertIn(b'Audio / Video', response.data)
        self.assertNotIn(b'Media link', response.data)
        self.assertIn(b'Transcribe and extract main claim', response.data)
        self.assertIn(b'Select the part to analyze', response.data)
        self.assertIn(b'clip_start_seconds', response.data)
        self.assertIn(b'var eventType', response.data)

    def test_media_endpoint_returns_transcript_claims(self):
        prepared = {
            'transcript_id': 'a' * 32,
            'raw_transcript': 'Baarlamaanka ayaa ansixiyay sharciga.',
            'claims': [{
                'exact_text': 'Baarlamaanka ayaa ansixiyay sharciga.',
                'classification_text': 'Baarlamaanka ayaa ansixiyay sharciga.',
                'verbatim': True,
            }],
        }
        with patch(
            'controllers.predict.transcribe_upload',
            return_value=prepared,
        ):
            response = self.client.post(
                '/api/transcribe-media',
                data={
                    'asr_mode': 'dayax',
                    'media_file': (io.BytesIO(b'test'), 'report.wav'),
                },
                content_type='multipart/form-data',
            )
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload['claims'][0]['verbatim'])
        self.assertEqual(
            payload['claims'][0]['classification_text'],
            payload['raw_transcript'],
        )

    def test_legacy_accurate_mode_maps_to_dayax(self):
        prepared = {
            'transcript_id': 'c' * 32,
            'raw_transcript': 'Golaha wasiirrada ayaa yeeshay kulan.',
            'claims': [],
        }
        with patch(
            'controllers.predict.transcribe_upload',
            return_value=prepared,
        ) as transcribe:
            response = self.client.post(
                '/api/transcribe-media',
                data={
                    'asr_mode': 'accurate',
                    'media_file': (io.BytesIO(b'test'), 'report.wav'),
                },
                content_type='multipart/form-data',
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(transcribe.call_args.kwargs['mode'], 'dayax')

    def test_media_endpoint_passes_selected_segment_to_asr_service(self):
        prepared = {
            'transcript_id': 'd' * 32,
            'raw_transcript': 'Golaha shacabka ayaa ansixiyay sharciga.',
            'claims': [],
        }
        with patch(
            'controllers.predict.transcribe_upload',
            return_value=prepared,
        ) as transcribe:
            response = self.client.post(
                '/api/transcribe-media',
                data={
                    'asr_mode': 'dayax',
                    'clip_start_seconds': '12.5',
                    'clip_end_seconds': '43.75',
                    'media_file': (io.BytesIO(b'test'), 'report.mp4'),
                },
                content_type='multipart/form-data',
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(transcribe.call_args.kwargs['clip_start_seconds'], 12.5)
        self.assertEqual(transcribe.call_args.kwargs['clip_end_seconds'], 43.75)

    def test_media_endpoint_rejects_invalid_segment(self):
        with patch('controllers.predict.transcribe_upload') as transcribe:
            response = self.client.post(
                '/api/transcribe-media',
                data={
                    'asr_mode': 'dayax',
                    'clip_start_seconds': '20',
                    'clip_end_seconds': '10',
                    'media_file': (io.BytesIO(b'test'), 'report.mp4'),
                },
                content_type='multipart/form-data',
            )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()['code'], 'invalid_media_segment')
        transcribe.assert_not_called()

    def test_media_endpoint_rejects_public_platform_url_when_disabled(self):
        with patch(
            'controllers.predict.transcribe_media_url',
        ) as transcribe:
            response = self.client.post(
                '/api/transcribe-media',
                data={
                    'asr_mode': 'dayax',
                    'media_url': 'https://youtu.be/example',
                },
            )
        self.assertEqual(response.status_code, 400)
        payload = response.get_json()
        self.assertEqual(payload['code'], 'media_links_disabled')
        self.assertIn('Upload a WAV', payload['error'])
        transcribe.assert_not_called()


if __name__ == '__main__':
    unittest.main()
