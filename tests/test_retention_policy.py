import io
import os
import sys
import unittest
from pathlib import Path

import pandas as pd


os.environ['DATABASE_URL'] = 'sqlite:///:memory:'
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
WEBAPP_DIR = Path(__file__).resolve().parents[1]
if str(WEBAPP_DIR) not in sys.path:
    sys.path.insert(0, str(WEBAPP_DIR))

from app import app  # noqa: E402
from models import db, User, Prediction, BatchJob  # noqa: E402


class RetentionPolicyTests(unittest.TestCase):
    def setUp(self):
        app.config.update(TESTING=True)
        self.client = app.test_client()
        with app.app_context():
            db.drop_all()
            db.create_all()
            admin = User(
                username='Hussein', email='admin@somali.com', role='admin',
                email_verified=True,
            )
            admin.set_password('admin123')
            member = User(
                username='member', email='member@example.com', role='user',
                email_verified=True,
            )
            member.set_password('member123')
            inactive = User(
                username='inactive-member', email='inactive@example.com',
                role='user', email_verified=True, is_active=False,
            )
            inactive.set_password('inactive123')
            db.session.add_all([admin, member, inactive])
            db.session.flush()
            prediction = Prediction(
                user_id=member.id,
                text='=Somali political text retained safely',
                prediction='Reliable', confidence=91.0,
                model_used='ensemble',
            )
            batch = BatchJob(
                user_id=member.id, filename='retained.xlsx', total_rows=1,
                processed_rows=1, status='completed', model_used='ensemble',
            )
            db.session.add_all([prediction, batch])
            db.session.commit()
            self.admin_id = admin.id
            self.member_id = member.id
            self.prediction_id = prediction.id
            self.batch_id = batch.id

    def tearDown(self):
        with app.app_context():
            db.session.remove()
            db.drop_all()

    def _login_as(self, user_id):
        with self.client.session_transaction() as session:
            session['_user_id'] = str(user_id)
            session['_fresh'] = True

    def test_user_delete_endpoints_retain_prediction(self):
        self._login_as(self.member_id)
        self.client.post(f'/history/delete/{self.prediction_id}')
        self.client.post(
            '/history/delete-selected',
            data={'selected_ids': [str(self.prediction_id)]},
        )
        self.client.post('/history/delete-all')
        with app.app_context():
            self.assertIsNotNone(db.session.get(Prediction, self.prediction_id))
        history_html = self.client.get('/history').get_data(as_text=True)
        self.assertNotIn('Delete All', history_html)
        self.assertNotIn('Delete Selected', history_html)

    def test_batch_delete_endpoint_retains_report(self):
        self._login_as(self.member_id)
        self.client.post(f'/batch/job/{self.batch_id}/delete')
        with app.app_context():
            self.assertIsNotNone(db.session.get(BatchJob, self.batch_id))

    def test_primary_admin_exports_retained_dataset_and_filters_inactive_users(self):
        self._login_as(self.admin_id)
        inactive_html = self.client.get(
            '/admin/?user_status=inactive'
        ).get_data(as_text=True)
        self.assertIn('inactive-member', inactive_html)
        self.assertNotIn('member@example.com', inactive_html)

        response = self.client.get('/admin/export-dataset')
        self.assertEqual(response.status_code, 200)
        workbook = pd.read_excel(io.BytesIO(response.data))
        self.assertEqual(len(workbook), 1)
        self.assertEqual(workbook.loc[0, 'AI Prediction'], 'Reliable')
        self.assertTrue(str(workbook.loc[0, 'Somali Text']).startswith("'="))


if __name__ == '__main__':
    unittest.main()
