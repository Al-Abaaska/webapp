from datetime import datetime
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin, LoginManager
from flask_mail import Mail
from werkzeug.security import generate_password_hash, check_password_hash

# ─── Shared objects ─────────────────────────────
db = SQLAlchemy()
login_manager = LoginManager()
mail = Mail()


# ─── Database Models ────────────────────────────

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


class Prediction(db.Model):
    """Stores each prediction result."""
    __tablename__ = 'predictions'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    text = db.Column(db.Text, nullable=False)
    prediction = db.Column(db.String(20), nullable=False)
    confidence = db.Column(db.Float, nullable=False)
    model_used = db.Column(db.String(50), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class OTP(db.Model):
    """One-time password codes for email verification and password reset."""
    __tablename__ = 'otps'

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), nullable=False)
    code = db.Column(db.String(6), nullable=False)
    purpose = db.Column(db.String(20), nullable=False)
    expires_at = db.Column(db.DateTime, nullable=False)
    used = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class BatchJob(db.Model):
    """Tracks batch prediction uploads."""
    __tablename__ = 'batch_jobs'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    filename = db.Column(db.String(255), nullable=False)
    total_rows = db.Column(db.Integer, default=0)
    processed_rows = db.Column(db.Integer, default=0)
    status = db.Column(db.String(20), default='pending')
    result_path = db.Column(db.String(500), default='')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class EvidenceAudit(db.Model):
    """One evidence-retrieval run associated with a prediction."""
    __tablename__ = 'evidence_audits'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False
    )
    prediction_id = db.Column(
        db.Integer, db.ForeignKey('predictions.id', ondelete='SET NULL'), nullable=True
    )
    original_text = db.Column(db.Text, nullable=False)
    main_claim = db.Column(db.Text, default='')
    ai_explanation = db.Column(db.Text, default='')
    predicted_label = db.Column(db.String(20), nullable=False)
    model_used = db.Column(db.String(50), nullable=False)
    queries_json = db.Column(db.Text, default='[]')
    status = db.Column(db.String(20), default='running')
    error_message = db.Column(db.Text, default='')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    completed_at = db.Column(db.DateTime, nullable=True)

    user = db.relationship('User', backref='evidence_audits')
    prediction_record = db.relationship(
        'Prediction',
        backref=db.backref('evidence_audits', lazy=True, order_by='EvidenceAudit.created_at'),
    )
    sources = db.relationship(
        'EvidenceSource',
        backref='audit',
        lazy=True,
        cascade='all, delete-orphan',
    )


class EvidenceSource(db.Model):
    """A web or social result retained for an evidence audit."""
    __tablename__ = 'evidence_sources'

    id = db.Column(db.Integer, primary_key=True)
    audit_id = db.Column(
        db.Integer, db.ForeignKey('evidence_audits.id', ondelete='CASCADE'), nullable=False
    )
    title = db.Column(db.Text, default='')
    url = db.Column(db.Text, nullable=False)
    source = db.Column(db.String(255), default='Web')
    platform = db.Column(db.String(50), default='Web')
    provider = db.Column(db.String(50), default='')
    snippet = db.Column(db.Text, default='')
    published_date = db.Column(db.String(50), default='')
    similarity_score = db.Column(db.Float, default=0)
    scoring_method = db.Column(db.String(50), default='lexical')
    stance = db.Column(db.String(20), default='Unclear')
    evidence_kind = db.Column(db.String(30), default='general')
    credibility = db.Column(db.String(100), default='')
    explanation = db.Column(db.Text, default='')
    retrieved_at = db.Column(db.DateTime, default=datetime.utcnow)


class FactCheckReview(db.Model):
    """A fact-checker's final decision for one source-evidence audit."""
    # Keep the legacy prediction-review table untouched. This table belongs to
    # the newer source-evidence audit workflow.
    __tablename__ = 'evidence_fact_check_reviews'

    id = db.Column(db.Integer, primary_key=True)
    audit_id = db.Column(
        db.Integer,
        db.ForeignKey('evidence_audits.id', ondelete='CASCADE'),
        nullable=False,
        unique=True,
    )
    reviewer_id = db.Column(
        db.Integer,
        db.ForeignKey('users.id', ondelete='SET NULL'),
        nullable=True,
    )
    decision = db.Column(db.String(40), nullable=False)
    explanation = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    audit = db.relationship(
        'EvidenceAudit',
        backref=db.backref(
            'fact_check_review',
            uselist=False,
            cascade='all, delete-orphan',
        ),
    )
    reviewer = db.relationship(
        'User',
        backref=db.backref('fact_check_reviews', lazy=True),
    )
