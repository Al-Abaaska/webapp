import os
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path


os.environ['DATABASE_URL'] = 'sqlite:///:memory:'
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
WEBAPP_DIR = Path(__file__).resolve().parents[1]
if str(WEBAPP_DIR) not in sys.path:
    sys.path.insert(0, str(WEBAPP_DIR))

from app import app  # noqa: E402
from models import (  # noqa: E402
    db, User, Prediction, EvidenceAudit, ClaimRecord, FactCheckReview,
)


class AdminAssignmentQueueTests(unittest.TestCase):
    def setUp(self):
        app.config.update(TESTING=True)
        self.client = app.test_client()
        with app.app_context():
            db.drop_all()
            db.create_all()
            self.admin = User(
                username='admin', email='admin@somali.com', role='admin',
                email_verified=True,
            )
            self.admin.set_password('admin123')
            self.checker = User(
                username='reviewer', email='reviewer@example.com',
                role='fact_checker', email_verified=True,
            )
            self.checker.set_password('review123')
            self.submitter = User(
                username='submitter', email='user@example.com', role='user',
                email_verified=True,
            )
            self.submitter.set_password('user123')
            db.session.add_all([self.admin, self.checker, self.submitter])
            db.session.flush()

            self.unassigned = self._add_case(
                'Unassigned high-spread claim', 'Misinformation', 96.0,
                spread=9, created_at=datetime.utcnow() - timedelta(hours=2),
            )
            self.assigned = self._add_case(
                'Assigned recent claim', 'Misleading', 82.0,
                spread=2, created_at=datetime.utcnow() - timedelta(hours=1),
                assignee=self.checker,
            )
            self.reliable = self._add_case(
                'Reliable completed claim', 'Reliable', 91.5,
                spread=1, created_at=datetime.utcnow() - timedelta(minutes=30),
            )
            self.reliable.review_status = 'not_required'
            self.failed_reliable = self._add_case(
                'Reliable claim with failed evidence retrieval', 'Reliable', 88.0,
                spread=1, created_at=datetime.utcnow() - timedelta(minutes=15),
            )
            self.failed_reliable.status = 'failed'
            self.failed_reliable.review_status = 'not_required'
            self.failed_reliable.error_message = 'Evidence provider timed out.'
            db.session.commit()
            self.admin_id = self.admin.id
            self.checker_id = self.checker.id
            self.submitter_id = self.submitter.id
            self.unassigned_id = self.unassigned.id
            self.assigned_id = self.assigned.id
            self.reliable_id = self.reliable.id
            self.failed_reliable_id = self.failed_reliable.id

    def tearDown(self):
        with app.app_context():
            db.session.remove()
            db.drop_all()

    def _add_case(self, text, label, confidence, spread, created_at, assignee=None):
        prediction = Prediction(
            user=self.submitter, text=text, prediction=label,
            confidence=confidence, model_used='ensemble', created_at=created_at,
        )
        db.session.add(prediction)
        db.session.flush()
        audit = EvidenceAudit(
            user=self.submitter,
            prediction_record=prediction,
            original_text=text,
            main_claim=text,
            predicted_label=label,
            prediction_confidence=confidence,
            model_used='ensemble',
            status='complete',
            created_at=created_at,
            assigned_to_id=assignee.id if assignee else None,
            review_status='pending' if assignee else 'unassigned',
        )
        db.session.add(audit)
        db.session.flush()
        db.session.add(ClaimRecord(
            fingerprint=f'fingerprint-{audit.id}',
            normalized_text=text.lower(),
            original_text=text,
            first_prediction_id=prediction.id,
            investigation_audit_id=audit.id,
            submission_count=spread,
            first_submitted_at=created_at,
            last_submitted_at=created_at,
        ))
        return audit

    def _login_as(self, user_id):
        with self.client.session_transaction() as session:
            session['_user_id'] = str(user_id)
            session['_fresh'] = True

    def test_admin_has_assignment_filters_but_no_reviews_navigation(self):
        self._login_as(self.admin_id)
        response = self.client.get('/admin/')
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('Assignment status', html)
        self.assertIn('Assigned fact-checker', html)
        self.assertIn('Spread: highest first', html)
        self.assertNotIn('href="/fact-checker/"', html)

    def test_admin_can_filter_unassigned_and_assigned_cases(self):
        self._login_as(self.admin_id)
        unassigned_html = self.client.get(
            '/admin/?assignment_status=unassigned'
        ).get_data(as_text=True)
        self.assertIn(f'href="/fact-checker/audit/{self.unassigned_id}"', unassigned_html)
        self.assertNotIn(f'href="/fact-checker/audit/{self.assigned_id}"', unassigned_html)

        assigned_html = self.client.get(
            f'/admin/?assignment_status=assigned&assignee_id={self.checker_id}'
        ).get_data(as_text=True)
        self.assertIn(f'href="/fact-checker/audit/{self.assigned_id}"', assigned_html)
        self.assertNotIn(f'href="/fact-checker/audit/{self.unassigned_id}"', assigned_html)

    def test_spread_sort_and_admin_case_view_are_correct(self):
        self._login_as(self.admin_id)
        html = self.client.get('/admin/?queue_sort=spread_desc').get_data(as_text=True)
        self.assertLess(
            html.index('Unassigned high-spread claim'),
            html.index('Assigned recent claim'),
        )

        detail = self.client.get(f'/fact-checker/audit/{self.unassigned_id}')
        self.assertEqual(detail.status_code, 200)
        detail_html = detail.get_data(as_text=True)
        self.assertIn('Administrator view is read-only', detail_html)
        self.assertNotIn('Start Review', detail_html)
        self.assertNotIn('Save Decision', detail_html)

    def test_confidence_survives_prediction_history_deletion(self):
        with app.app_context():
            audit = db.session.get(EvidenceAudit, self.unassigned_id)
            db.session.delete(audit.prediction_record)
            db.session.commit()

        self._login_as(self.admin_id)
        html = self.client.get(
            '/admin/?assignment_status=unassigned'
        ).get_data(as_text=True)
        self.assertIn('96.0%', html)

    def test_admin_cannot_open_my_assigned_cases(self):
        self._login_as(self.admin_id)
        response = self.client.get('/fact-checker/', follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('Admin Dashboard', html)
        self.assertNotIn('My Assigned Cases', html)

    def test_archive_hides_case_from_active_queue_and_restore_returns_it(self):
        self._login_as(self.admin_id)
        response = self.client.post(
            f'/admin/audit/{self.unassigned_id}/archive',
            data={'reason': 'Duplicate case retained for audit history.'},
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        active_html = response.get_data(as_text=True)
        self.assertNotIn(f'href="/fact-checker/audit/{self.unassigned_id}"', active_html)

        archived_html = self.client.get(
            '/admin/?case_scope=archived'
        ).get_data(as_text=True)
        self.assertIn(f'href="/fact-checker/audit/{self.unassigned_id}"', archived_html)
        self.assertIn('Duplicate case retained for audit history.', archived_html)

        self._login_as(self.checker_id)
        fact_checker_view = self.client.get(
            f'/fact-checker/audit/{self.unassigned_id}', follow_redirects=True
        ).get_data(as_text=True)
        self.assertNotIn(f'Case FC-{self.unassigned_id}</h1>', fact_checker_view)

        self._login_as(self.admin_id)
        self.client.post(f'/admin/audit/{self.unassigned_id}/restore')
        with app.app_context():
            self.assertFalse(db.session.get(EvidenceAudit, self.unassigned_id).is_archived)

    def test_primary_admin_cannot_delete_archived_unreviewed_case(self):
        self._login_as(self.admin_id)
        self.client.post(
            f'/admin/audit/{self.unassigned_id}/archive',
            data={'reason': 'Invalid duplicate case created during testing.'},
        )
        response = self.client.post(
            f'/admin/audit/{self.unassigned_id}/delete',
            data={
                'reason': 'Invalid duplicate case created during testing.',
                'confirmation': f'FC-{self.unassigned_id}',
            },
            follow_redirects=True,
        )
        self.assertIn(
            'This action is unavailable',
            response.get_data(as_text=True),
        )
        with app.app_context():
            self.assertIsNotNone(
                db.session.get(EvidenceAudit, self.unassigned_id)
            )

    def test_reliable_case_can_be_archived_but_not_deleted(self):
        self._login_as(self.admin_id)
        active_html = self.client.get(
            '/admin/?case_label=reliable'
        ).get_data(as_text=True)
        self.assertIn('Reliable completed claim', active_html)
        self.assertIn('Completed — no review', active_html)
        self.assertIn('No fact-checker required', active_html)

        self.client.post(
            f'/admin/audit/{self.reliable_id}/archive',
            data={'reason': 'Reliable test case no longer needs retention.'},
        )
        archived_html = self.client.get(
            '/admin/?case_scope=archived&case_label=reliable'
        ).get_data(as_text=True)
        self.assertIn('Reliable completed claim', archived_html)
        self.assertIn('Restore', archived_html)
        self.assertNotIn('Permanently Delete', archived_html)

        self.client.post(
            f'/admin/audit/{self.reliable_id}/delete',
            data={
                'reason': 'Reliable test case no longer needs retention.',
                'confirmation': f'FC-{self.reliable_id}',
            },
        )
        with app.app_context():
            self.assertIsNotNone(db.session.get(EvidenceAudit, self.reliable_id))

    def test_failed_reliable_audit_is_visible_and_retained_after_archive(self):
        self._login_as(self.admin_id)
        active_html = self.client.get(
            '/admin/?case_label=reliable&assignment_status=failed'
        ).get_data(as_text=True)
        self.assertIn('Reliable claim with failed evidence retrieval', active_html)
        self.assertIn('Evidence audit failed', active_html)
        self.assertIn('No assignment — audit failed', active_html)

        self.client.post(
            f'/admin/audit/{self.failed_reliable_id}/archive',
            data={'reason': 'Failed audit is no longer needed by the administrator.'},
        )
        self.client.post(
            f'/admin/audit/{self.failed_reliable_id}/delete',
            data={
                'reason': 'Failed audit is no longer needed by the administrator.',
                'confirmation': f'FC-{self.failed_reliable_id}',
            },
        )
        with app.app_context():
            self.assertIsNotNone(
                db.session.get(EvidenceAudit, self.failed_reliable_id)
            )

    def test_reviewed_case_and_submitter_audit_trail_are_protected(self):
        with app.app_context():
            audit = db.session.get(EvidenceAudit, self.assigned_id)
            audit.review_status = 'completed'
            audit.is_archived = True
            db.session.add(FactCheckReview(
                audit=audit,
                reviewer_id=self.checker_id,
                decision='Confirmed Misleading',
                explanation='Independent evidence confirmed the final decision.',
            ))
            db.session.commit()

        self._login_as(self.admin_id)
        self.client.post(
            f'/admin/audit/{self.assigned_id}/delete',
            data={
                'reason': 'Attempt to remove a reviewed historical case.',
                'confirmation': f'FC-{self.assigned_id}',
            },
        )
        self.client.post(
            f'/admin/set_user_status/{self.submitter_id}',
            data={'status': 'inactive'},
        )
        with app.app_context():
            self.assertIsNotNone(db.session.get(EvidenceAudit, self.assigned_id))
            self.assertIsNotNone(db.session.get(User, self.submitter_id))


if __name__ == '__main__':
    unittest.main()
