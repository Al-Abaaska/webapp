from datetime import datetime
from models.user import db


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


class ClaimRecord(db.Model):
    """One exact normalized claim with its reusable investigation and spread count."""
    __tablename__ = 'claim_records'

    id = db.Column(db.Integer, primary_key=True)
    fingerprint = db.Column(db.String(64), unique=True, nullable=False, index=True)
    normalized_text = db.Column(db.Text, nullable=False)
    original_text = db.Column(db.Text, nullable=False)
    first_prediction_id = db.Column(
        db.Integer, db.ForeignKey('predictions.id', ondelete='SET NULL'), nullable=True
    )
    investigation_audit_id = db.Column(
        db.Integer, db.ForeignKey('evidence_audits.id', ondelete='SET NULL'),
        nullable=True, unique=True,
    )
    submission_count = db.Column(db.Integer, default=1, nullable=False)
    first_submitted_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    last_submitted_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    first_prediction = db.relationship(
        'Prediction', foreign_keys=[first_prediction_id],
        backref=db.backref('claim_record', uselist=False),
    )
    investigation = db.relationship(
        'EvidenceAudit', foreign_keys=[investigation_audit_id],
        backref=db.backref('spread_record', uselist=False),
    )
