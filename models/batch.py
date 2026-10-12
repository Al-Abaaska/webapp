from datetime import datetime
from models.user import db


class BatchJob(db.Model):
    """Tracks one staged batch-investigation workspace."""
    __tablename__ = 'batch_jobs'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    filename = db.Column(db.String(255), nullable=False)
    total_rows = db.Column(db.Integer, default=0)
    processed_rows = db.Column(db.Integer, default=0)
    status = db.Column(db.String(20), default='pending')
    result_path = db.Column(db.String(500), default='')
    input_path = db.Column(db.String(500), default='')
    text_column = db.Column(db.String(255), default='')
    source_url_column = db.Column(db.String(255), default='')
    source_name_column = db.Column(db.String(255), default='')
    date_column = db.Column(db.String(255), default='')
    model_used = db.Column(db.String(50), default='svm')
    # Retained for compatibility with existing databases; the simplified
    # workspace no longer exposes or runs either option.
    duplicate_detection = db.Column(db.Boolean, default=False, nullable=False)
    high_risk_only = db.Column(db.Boolean, default=False, nullable=False)
    validation_json = db.Column(db.Text, default='{}')
    summary_json = db.Column(db.Text, default='{}')
    error_message = db.Column(db.Text, default='')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    items = db.relationship(
        'BatchItem', backref='job', lazy=True, cascade='all, delete-orphan',
        order_by='BatchItem.row_number',
    )


class BatchItem(db.Model):
    """One uploaded row and its prediction/investigation state."""
    __tablename__ = 'batch_items'

    id = db.Column(db.Integer, primary_key=True)
    job_id = db.Column(
        db.Integer, db.ForeignKey('batch_jobs.id', ondelete='CASCADE'),
        nullable=False, index=True,
    )
    row_number = db.Column(db.Integer, nullable=False)
    text = db.Column(db.Text, default='')
    source_url = db.Column(db.Text, default='')
    source_name = db.Column(db.String(255), default='')
    source_date = db.Column(db.String(100), default='')
    prediction = db.Column(db.String(30), default='Pending')
    confidence = db.Column(db.Float, default=0)
    risk = db.Column(db.String(30), default='Uncertain')
    previous_match = db.Column(db.String(20), default='None')
    match_similarity = db.Column(db.Float, default=0)
    matched_audit_id = db.Column(
        db.Integer, db.ForeignKey('evidence_audits.id', ondelete='SET NULL'),
        nullable=True,
    )
    # Legacy field retained so existing installations do not need a destructive
    # schema migration. New batch processing does not assign claim clusters.
    cluster_id = db.Column(db.String(30), nullable=True, index=True)
    prediction_id = db.Column(
        db.Integer, db.ForeignKey('predictions.id', ondelete='SET NULL'),
        nullable=True,
    )
    investigation_audit_id = db.Column(
        db.Integer, db.ForeignKey('evidence_audits.id', ondelete='SET NULL'),
        nullable=True,
    )
    investigation_status = db.Column(db.String(20), default='not_started')
    error_message = db.Column(db.Text, default='')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    prediction_record = db.relationship('Prediction', foreign_keys=[prediction_id])
    matched_audit = db.relationship('EvidenceAudit', foreign_keys=[matched_audit_id])
    investigation_audit = db.relationship(
        'EvidenceAudit', foreign_keys=[investigation_audit_id]
    )
