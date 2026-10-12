import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


os.environ['DATABASE_URL'] = 'sqlite:///:memory:'
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
WEBAPP_DIR = Path(__file__).resolve().parents[1]
if str(WEBAPP_DIR) not in sys.path:
    sys.path.insert(0, str(WEBAPP_DIR))

from app import app  # noqa: E402
from models import db, User, Notification  # noqa: E402


class FactCheckerApplicationTests(unittest.TestCase):
    def setUp(self):
        self.upload_directory = tempfile.TemporaryDirectory()
        app.config.update(
            TESTING=True,
            UPLOAD_FOLDER=self.upload_directory.name,
            PRIMARY_ADMIN_EMAIL='admin@somali.com',
            PRIMARY_ADMIN_USERNAME='Hussein',
        )
        self.client = app.test_client()
        with app.app_context():
            db.drop_all()
            db.create_all()
            self.admin = User(
                username='Hussein', email='admin@somali.com', role='admin',
                email_verified=True,
            )
            self.admin.set_password('test-password')
            db.session.add(self.admin)
            db.session.commit()
            self.admin_id = self.admin.id

    def tearDown(self):
        with app.app_context():
            db.session.remove()
            db.drop_all()
        self.upload_directory.cleanup()

    def _login_as_admin(self):
        with self.client.session_transaction() as session:
            session['_user_id'] = str(self.admin_id)
            session['_fresh'] = True

    @patch('controllers.admin.send_fact_checker_application_email')
    @patch('controllers.auth.send_otp_email')
    @patch('controllers.auth.create_otp_record', return_value='123456')
    def test_application_upload_and_admin_approval(
        self, _create_otp, _send_otp, send_decision_email
    ):
        response = self.client.post(
            '/register',
            data={
                'account_type': 'fact_checker',
                'username': 'applicant',
                'email': 'applicant@example.com',
                'password': 'secret12',
                'confirm_password': 'secret12',
                'organization': 'Somali Newsroom',
                'professional_id': 'PRESS-204',
                'id_document': (io.BytesIO(b'%PDF-1.4\nidentity'), 'identity.pdf'),
                'certificates': [
                    (io.BytesIO(b'%PDF-1.4\ncertificate'), 'journalism.pdf'),
                ],
            },
            content_type='multipart/form-data',
        )
        self.assertEqual(response.status_code, 302)
        with app.app_context():
            applicant = User.query.filter_by(username='applicant').one()
            applicant_id = applicant.id
            self.assertEqual(applicant.role, 'user')
            self.assertEqual(applicant.fact_checker_application_status, 'pending')
            self.assertEqual(applicant.fact_checker_organization, 'Somali Newsroom')
            self.assertTrue(os.path.isfile(os.path.join(
                app.config['UPLOAD_FOLDER'], applicant.fact_checker_id_document
            )))
            self.assertEqual(len(applicant.fact_checker_certificates), 1)

        self._login_as_admin()
        dashboard = self.client.get('/admin/')
        self.assertIn('Somali Newsroom', dashboard.get_data(as_text=True))
        response = self.client.post(
            f'/admin/fact-checker-application/{applicant_id}/approve',
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        with app.app_context():
            applicant = db.session.get(User, applicant_id)
            self.assertEqual(applicant.role, 'fact_checker')
            self.assertEqual(applicant.fact_checker_application_status, 'approved')
            notification = Notification.query.filter_by(
                user_id=applicant_id,
                notification_type='fact_checker_application',
            ).one()
            self.assertIn('approved', notification.title.lower())
        dashboard = self.client.get('/admin/').get_data(as_text=True)
        self.assertIn('Fact-Checker Credentials', dashboard)
        self.assertIn('View ID', dashboard)
        self.assertIn('View Certificate 1', dashboard)
        self.assertIn('Approved', dashboard)
        inline_document = self.client.get(
            f'/admin/fact-checker-application/{applicant_id}/document/identity'
        )
        self.assertEqual(inline_document.status_code, 200)
        self.assertTrue(
            inline_document.headers['Content-Disposition'].startswith('inline;')
        )
        downloaded_document = self.client.get(
            f'/admin/fact-checker-application/{applicant_id}/document/identity/download'
        )
        self.assertEqual(downloaded_document.status_code, 200)
        self.assertTrue(
            downloaded_document.headers['Content-Disposition'].startswith('attachment;')
        )
        send_decision_email.assert_called_once_with(
            'applicant@example.com',
            'applicant',
            approved=True,
            administrator_note='',
        )

    def test_primary_reports_and_active_inactive_controls(self):
        with app.app_context():
            user = User(
                username='member', email='member@example.com', role='user',
                email_verified=True,
            )
            user.set_password('member12')
            db.session.add(user)
            db.session.commit()
            user_id = user.id
        self._login_as_admin()
        response = self.client.get('/admin/reports')
        self.assertEqual(response.status_code, 200)
        self.assertIn('User Report', response.get_data(as_text=True))

        self.client.post(
            f'/admin/set_user_status/{user_id}', data={'status': 'inactive'}
        )
        with app.app_context():
            self.assertFalse(db.session.get(User, user_id).is_active)
        dashboard = self.client.get('/admin/').get_data(as_text=True)
        self.assertIn('Set Active', dashboard)
        self.assertNotIn('Delete User', dashboard)

    def test_inactive_user_is_logged_out_on_the_next_request(self):
        with app.app_context():
            user = User(
                username='active-session', email='session@example.com',
                role='user', email_verified=True,
            )
            user.set_password('member12')
            db.session.add(user)
            db.session.commit()
            user_id = user.id
        with self.client.session_transaction() as session:
            session['_user_id'] = str(user_id)
            session['_fresh'] = True
        self.assertEqual(self.client.get('/profile').status_code, 200)
        with app.app_context():
            user = db.session.get(User, user_id)
            user.is_active = False
            db.session.commit()
        response = self.client.get('/profile', follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login', response.headers['Location'])
        with self.client.session_transaction() as session:
            self.assertNotIn('_user_id', session)

    @patch('controllers.admin.send_fact_checker_application_email')
    def test_rejection_sends_email_and_keeps_normal_user_access(
        self, send_decision_email
    ):
        with app.app_context():
            applicant = User(
                username='rejected-applicant',
                email='rejected@example.com',
                role='user',
                email_verified=True,
                fact_checker_application_status='pending',
                fact_checker_organization='Independent Newsroom',
                fact_checker_professional_id='PRESS-009',
            )
            applicant.set_password('member12')
            db.session.add(applicant)
            db.session.commit()
            applicant_id = applicant.id
        self._login_as_admin()
        response = self.client.post(
            f'/admin/fact-checker-application/{applicant_id}/reject',
            data={'note': 'Certificate could not be verified.'},
        )
        self.assertEqual(response.status_code, 302)
        with app.app_context():
            applicant = db.session.get(User, applicant_id)
            self.assertEqual(applicant.role, 'user')
            self.assertEqual(applicant.fact_checker_application_status, 'rejected')
        send_decision_email.assert_called_once_with(
            'rejected@example.com',
            'rejected-applicant',
            approved=False,
            administrator_note='Certificate could not be verified.',
        )

    def test_landing_page_has_verified_team_names_and_contact_sections(self):
        response = self.client.get('/')
        html = response.get_data(as_text=True)
        self.assertIn('About Us', html)
        self.assertIn('Contact Us', html)
        self.assertIn('Abas Hussein Ali', html)
        self.assertIn('Yacquub Maxamad Axmad', html)
        self.assertIn('Abdiqani Maxamad Aweys', html)
        self.assertIn('Sabriin Cabdiaziz Cali', html)


if __name__ == '__main__':
    unittest.main()
