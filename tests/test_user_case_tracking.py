import os
import sys
import unittest
from datetime import datetime
from pathlib import Path


os.environ['DATABASE_URL'] = 'sqlite:///:memory:'
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
WEBAPP_DIR = Path(__file__).resolve().parents[1]
if str(WEBAPP_DIR) not in sys.path:
    sys.path.insert(0, str(WEBAPP_DIR))

from app import app  # noqa: E402
from models import (  # noqa: E402
    db, User, Prediction, EvidenceAudit, FactCheckReview, Notification,
)


class UserCaseTrackingTests(unittest.TestCase):
    def setUp(self):
        app.config.update(TESTING=True)
        self.client = app.test_client()
        with app.app_context():
            db.drop_all()
            db.create_all()
            self.user = User(
                username='case-owner', email='owner@example.com', role='user',
                email_verified=True,
            )
            self.other = User(
                username='other-user', email='other@example.com', role='user',
                email_verified=True,
            )
            self.checker = User(
                username='reviewer', email='reviewer@example.com', role='fact_checker',
                email_verified=True,
            )
            for user in (self.user, self.other, self.checker):
                user.set_password('password123')
            db.session.add_all([self.user, self.other, self.checker])
            db.session.flush()

            self.queued = self._add_case(
                self.user, 'Claim waiting for assignment', 'Misleading', 'unassigned'
            )
            self.reviewing = self._add_case(
                self.user, 'Claim currently reviewed', 'Misinformation',
                'under_review', self.checker,
            )
            self.completed = self._add_case(
                self.user, 'Claim with final human decision', 'Reliable',
                'completed', self.checker,
            )
            db.session.add(FactCheckReview(
                audit=self.completed, reviewer=self.checker,
                decision='Confirmed Reliable',
                explanation='Two independent sources confirmed the reported event.',
            ))
            self.private_case = self._add_case(
                self.other, 'Another user private claim', 'Misinformation', 'unassigned'
            )
            db.session.commit()
            self.user_id = self.user.id
            self.checker_id = self.checker.id
            self.reviewing_id = self.reviewing.id
            self.completed_id = self.completed.id
            self.private_case_id = self.private_case.id

    def tearDown(self):
        with app.app_context():
            db.session.remove()
            db.drop_all()

    def _add_case(self, owner, text, label, review_status, checker=None):
        prediction = Prediction(
            user=owner, text=text, prediction=label,
            confidence=91.5, model_used='ensemble',
        )
        db.session.add(prediction)
        db.session.flush()
        audit = EvidenceAudit(
            user=owner, prediction_record=prediction,
            original_text=text, main_claim=text,
            predicted_label=label, prediction_confidence=91.5,
            model_used='ensemble', status='complete',
            completed_at=datetime.utcnow(),
            review_status=review_status,
            assigned_to_id=checker.id if checker else None,
            assigned_at=datetime.utcnow() if checker else None,
        )
        db.session.add(audit)
        db.session.flush()
        return audit

    def _login(self):
        with self.client.session_transaction() as session:
            session['_user_id'] = str(self.user_id)
            session['_fresh'] = True

    def _login_as(self, user_id):
        with self.client.session_transaction() as session:
            session['_user_id'] = str(user_id)
            session['_fresh'] = True

    def test_user_sees_only_their_cases_and_all_real_workflow_states(self):
        self._login()
        response = self.client.get('/my-cases')
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('Track every claim to its final decision', html)
        self.assertIn('Awaiting Assignment', html)
        self.assertIn('Under Review', html)
        self.assertIn('Decision Published', html)
        self.assertIn('Confirmed Reliable', html)
        self.assertNotIn('Another user private claim', html)

    def test_completed_filter_and_detail_timeline(self):
        self._login()
        filtered = self.client.get('/my-cases?status=completed').get_data(as_text=True)
        self.assertIn('Claim with final human decision', filtered)
        self.assertNotIn('Claim waiting for assignment', filtered)

        detail = self.client.get(
            f'/history/evidence/{self.completed_id}'
        ).get_data(as_text=True)
        self.assertIn('Live Fact-Check Tracker', detail)
        self.assertIn('STEP 5', detail)
        self.assertIn('Final Decision', detail)
        self.assertIn('Confirmed Reliable', detail)

    def test_user_cannot_open_another_users_tracker(self):
        self._login()
        response = self.client.get(f'/history/evidence/{self.private_case_id}')
        self.assertEqual(response.status_code, 404)

    def test_final_decision_creates_one_unread_notification_and_opens_tracker(self):
        self._login_as(self.checker_id)
        response = self.client.post(
            f'/fact-checker/audit/{self.reviewing_id}/decision',
            data={
                'decision': 'Confirmed Misinformation',
                'explanation': 'Independent evidence contradicts the central claim.',
            },
        )
        self.assertEqual(response.status_code, 302)
        with app.app_context():
            notifications = Notification.query.filter_by(
                audit_id=self.reviewing_id
            ).all()
            self.assertEqual(len(notifications), 1)
            self.assertFalse(notifications[0].is_read)
            notification_id = notifications[0].id

        self._login()
        page = self.client.get('/notifications/').get_data(as_text=True)
        self.assertIn('Final decision published', page)
        self.assertIn('Confirmed Misinformation', page)
        self.assertIn('1 unread', page)

        opened = self.client.post(
            f'/notifications/{notification_id}/open', follow_redirects=False
        )
        self.assertEqual(opened.status_code, 302)
        self.assertIn(f'/history/evidence/{self.reviewing_id}', opened.location)
        with app.app_context():
            self.assertTrue(db.session.get(Notification, notification_id).is_read)

        # Updating the same completed decision refreshes the existing row.
        self._login_as(self.checker_id)
        self.client.post(
            f'/fact-checker/audit/{self.reviewing_id}/decision',
            data={
                'decision': 'Insufficient Evidence',
                'explanation': 'The available sources do not establish a final conclusion.',
            },
        )
        with app.app_context():
            notifications = Notification.query.filter_by(
                audit_id=self.reviewing_id
            ).all()
            self.assertEqual(len(notifications), 1)
            self.assertFalse(notifications[0].is_read)
            self.assertIn('updated', notifications[0].title.lower())


if __name__ == '__main__':
    unittest.main()
