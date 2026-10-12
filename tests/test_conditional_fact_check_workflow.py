import os
import sys
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch


os.environ['DATABASE_URL'] = 'sqlite:///:memory:'
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
WEBAPP_DIR = Path(__file__).resolve().parents[1]
if str(WEBAPP_DIR) not in sys.path:
    sys.path.insert(0, str(WEBAPP_DIR))

from app import app  # noqa: E402
from models import db, User, Prediction, EvidenceAudit, EvidenceSource  # noqa: E402
from services.evidence_storage import MAX_EVIDENCE_SNIPPET_CHARS  # noqa: E402


class ConditionalFactCheckWorkflowTests(unittest.TestCase):
    def setUp(self):
        app.config.update(TESTING=True)
        self.client = app.test_client()
        with app.app_context():
            db.drop_all()
            db.create_all()
            self.user = User(
                username='owner', email='owner@example.com', role='user',
                email_verified=True,
            )
            self.admin = User(
                username='admin', email='admin@somali.com', role='admin',
                email_verified=True,
            )
            self.checker = User(
                username='checker', email='checker@example.com',
                role='fact_checker', email_verified=True,
            )
            for account in (self.user, self.admin, self.checker):
                account.set_password('password123')
            db.session.add_all([self.user, self.admin, self.checker])
            db.session.commit()
            self.user_id = self.user.id
            self.admin_id = self.admin.id
            self.checker_id = self.checker.id

    def tearDown(self):
        with app.app_context():
            db.session.remove()
            db.drop_all()

    def _login_as(self, user_id):
        with self.client.session_transaction() as session:
            session['_user_id'] = str(user_id)
            session['_fresh'] = True

    def _prediction(self, label):
        with app.app_context():
            prediction = Prediction(
                user_id=self.user_id,
                text=f'{label} political claim',
                prediction=label,
                confidence=91.0,
                model_used='ensemble',
            )
            db.session.add(prediction)
            db.session.commit()
            return prediction.id

    @patch('controllers.predict.retrieve_evidence', return_value=([], {}))
    @patch('controllers.predict.generate_search_queries', return_value=['claim query'])
    @patch('controllers.predict.extract_main_claim', return_value='main political claim')
    def test_reliable_audit_completes_without_review_queue(
        self, _extract, _queries, _evidence,
    ):
        self._login_as(self.user_id)
        prediction_id = self._prediction('Reliable')
        response = self.client.post(
            '/api/fact-check',
            json={
                'text': 'Reliable political claim',
                'prediction': 'Reliable',
                'model_key': 'ensemble',
                'prediction_id': prediction_id,
                'analysis': 'Automated result.',
            },
            buffered=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn('"review_required": false', response.get_data(as_text=True))
        with app.app_context():
            audit = EvidenceAudit.query.filter_by(prediction_id=prediction_id).one()
            self.assertEqual(audit.status, 'complete')
            self.assertEqual(audit.review_status, 'not_required')
            self.assertFalse(audit.requires_fact_check)
            audit_id = audit.id

        tracker = self.client.get(
            f'/history/evidence/{audit_id}'
        ).get_data(as_text=True)
        self.assertIn('Completed Prediction Case', tracker)
        self.assertIn('No human review required', tracker)
        self.assertNotIn('Review Queue', tracker)

    @patch('controllers.predict.retrieve_evidence', return_value=([], {}))
    @patch('controllers.predict.generate_search_queries', return_value=['claim query'])
    @patch('controllers.predict.extract_main_claim', return_value='main political claim')
    def test_misinformation_audit_remains_assignable(
        self, _extract, _queries, _evidence,
    ):
        self._login_as(self.user_id)
        prediction_id = self._prediction('Misinformation')
        response = self.client.post(
            '/api/fact-check',
            json={
                'text': 'Misinformation political claim',
                'prediction': 'Misinformation',
                'model_key': 'ensemble',
                'prediction_id': prediction_id,
                'analysis': 'Automated result.',
            },
            buffered=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn('"review_required": true', response.get_data(as_text=True))
        with app.app_context():
            audit = EvidenceAudit.query.filter_by(prediction_id=prediction_id).one()
            self.assertEqual(audit.review_status, 'unassigned')
            self.assertTrue(audit.requires_fact_check)

    @patch('controllers.predict.generate_search_queries', return_value=['claim query'])
    @patch('controllers.predict.extract_main_claim', return_value='main political claim')
    def test_oversized_provider_snippet_is_bounded_and_audit_completes(
        self, _extract, _queries,
    ):
        oversized = 'x' * 100_000
        evidence = [{
            'title': 'Related report',
            'url': 'https://example.com/report',
            'source': 'Example',
            'platform': 'Website',
            'provider': 'Exa',
            'snippet': oversized,
            'similarity_score': 92,
            'stance': 'Repeats',
        }]
        self._login_as(self.user_id)
        prediction_id = self._prediction('Reliable')
        with patch('controllers.predict.retrieve_evidence', return_value=(evidence, {'Exa': 1})):
            response = self.client.post(
                '/api/fact-check',
                json={
                    'text': 'Reliable political claim',
                    'prediction': 'Reliable',
                    'model_key': 'ensemble',
                    'prediction_id': prediction_id,
                    'analysis': 'Automated result.',
                },
                buffered=True,
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn('event: done', response.get_data(as_text=True))
        with app.app_context():
            audit = EvidenceAudit.query.filter_by(
                prediction_id=prediction_id
            ).one()
            source = EvidenceSource.query.filter_by(audit_id=audit.id).one()
            self.assertEqual(audit.status, 'complete')
            self.assertEqual(audit.error_message, '')
            self.assertEqual(len(source.snippet), MAX_EVIDENCE_SNIPPET_CHARS)

    @patch('controllers.predict.generate_search_queries', return_value=['retry query'])
    @patch('controllers.predict.extract_main_claim', return_value='retried main claim')
    def test_owner_can_retry_failed_audit_without_new_case(
        self, _extract, _queries,
    ):
        with app.app_context():
            prediction = Prediction(
                user_id=self.user_id,
                text='Claim whose evidence failed',
                prediction='Reliable',
                confidence=83.0,
                model_used='ensemble',
            )
            audit = EvidenceAudit(
                user_id=self.user_id,
                prediction_record=prediction,
                original_text='Claim whose evidence failed',
                predicted_label='Reliable',
                prediction_confidence=83.0,
                model_used='ensemble',
                status='failed',
                review_status='not_required',
                error_message='Provider response could not be stored.',
            )
            db.session.add_all([prediction, audit])
            db.session.commit()
            audit_id = audit.id

        retry_evidence = [{
            'title': 'Retry result',
            'url': 'https://example.com/retry',
            'source': 'Example',
            'snippet': 'Evidence retrieved after retry.',
            'similarity_score': 85,
        }]
        self._login_as(self.user_id)
        tracker = self.client.get(
            f'/history/evidence/{audit_id}'
        ).get_data(as_text=True)
        self.assertIn('Retry Audit', tracker)
        with patch('controllers.predict.retrieve_evidence', return_value=(retry_evidence, {'Exa': 1})):
            response = self.client.post(
                '/api/fact-check',
                json={'retry_audit_id': audit_id},
                buffered=True,
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn('event: done', response.get_data(as_text=True))
        with app.app_context():
            audit = db.session.get(EvidenceAudit, audit_id)
            self.assertEqual(audit.status, 'complete')
            self.assertEqual(audit.error_message, '')
            self.assertEqual(audit.main_claim, 'retried main claim')
            self.assertEqual(EvidenceAudit.query.count(), 1)
            self.assertEqual(EvidenceSource.query.filter_by(audit_id=audit_id).count(), 1)

    def test_legacy_reliable_case_is_admin_managed_but_not_fact_checker_work(self):
        with app.app_context():
            prediction = Prediction(
                user_id=self.user_id, text='Legacy reliable claim',
                prediction='Reliable', confidence=95.0, model_used='ensemble',
            )
            audit = EvidenceAudit(
                user_id=self.user_id, prediction_record=prediction,
                original_text='Legacy reliable claim', main_claim='Legacy reliable claim',
                predicted_label='Reliable', prediction_confidence=95.0,
                model_used='ensemble', status='complete',
                completed_at=datetime.utcnow(), review_status='unassigned',
            )
            db.session.add_all([prediction, audit])
            db.session.commit()
            audit_id = audit.id

        self._login_as(self.admin_id)
        admin_page = self.client.get('/admin/').get_data(as_text=True)
        self.assertIn(f'FC-{audit_id}', admin_page)
        self.assertIn('No fact-checker required', admin_page)

        self._login_as(self.checker_id)
        checker_page = self.client.get(
            '/fact-checker/?status=unassigned'
        ).get_data(as_text=True)
        self.assertNotIn(f'FC-{audit_id}', checker_page)


if __name__ == '__main__':
    unittest.main()
