import json
from datetime import datetime
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin, LoginManager
from flask_mail import Mail
from werkzeug.security import generate_password_hash, check_password_hash

# ─── Shared objects ─────────────────────────────
db = SQLAlchemy()
login_manager = LoginManager()
mail = Mail()


class User(UserMixin, db.Model):
    """User account table."""
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    role = db.Column(db.String(20), default='user')
    email_verified = db.Column(db.Boolean, default=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    fact_checker_application_status = db.Column(
        db.String(20), default='not_requested', nullable=False
    )
    fact_checker_organization = db.Column(db.String(200), default='')
    fact_checker_professional_id = db.Column(db.String(120), default='')
    fact_checker_id_document = db.Column(db.String(500), default='')
    fact_checker_certificates_json = db.Column(db.Text, default='[]')
    fact_checker_application_note = db.Column(db.Text, default='')
    fact_checker_applied_at = db.Column(db.DateTime, nullable=True)
    fact_checker_reviewed_at = db.Column(db.DateTime, nullable=True)
    fact_checker_reviewed_by_id = db.Column(db.Integer, nullable=True)

    predictions = db.relationship('Prediction', backref='user', lazy=True)
    batch_jobs = db.relationship('BatchJob', backref='user', lazy=True)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    @property
    def is_admin(self):
        return self.role == 'admin'

    @property
    def is_fact_checker(self):
        return self.role == 'fact_checker'

    @property
    def can_fact_check(self):
        return self.is_fact_checker

    @property
    def account_status(self):
        return 'active' if self.is_active else 'inactive'

    @property
    def fact_checker_certificates(self):
        try:
            files = json.loads(self.fact_checker_certificates_json or '[]')
        except (TypeError, ValueError):
            return []
        return files if isinstance(files, list) else []
