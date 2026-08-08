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
    is_blocked = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

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
        return self.role in {'fact_checker', 'admin'}
