from datetime import datetime

from models.user import db


class Notification(db.Model):
    """An in-app update delivered to one user about a tracked case."""
    __tablename__ = 'notifications'
    __table_args__ = (
        db.UniqueConstraint(
            'user_id', 'audit_id', 'notification_type',
            name='uq_notification_user_case_type',
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False,
        index=True,
    )
    audit_id = db.Column(
        db.Integer, db.ForeignKey('evidence_audits.id', ondelete='CASCADE'),
        nullable=True, index=True,
    )
    notification_type = db.Column(
        db.String(40), default='case_update', nullable=False,
    )
    title = db.Column(db.String(180), nullable=False)
    message = db.Column(db.Text, nullable=False)
    is_read = db.Column(db.Boolean, default=False, nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    read_at = db.Column(db.DateTime, nullable=True)

    user = db.relationship(
        'User', backref=db.backref(
            'notifications', lazy=True, cascade='all, delete-orphan'
        )
    )
    audit = db.relationship(
        'EvidenceAudit', backref=db.backref(
            'notifications', lazy=True, cascade='all, delete-orphan'
        )
    )
