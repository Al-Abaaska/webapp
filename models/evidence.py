from datetime import datetime
from models.user import db


FACT_CHECK_REQUIRED_LABELS = ('Misinformation', 'Misleading')


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
    prediction_confidence = db.Column(db.Float, nullable=True)
    model_used = db.Column(db.String(50), nullable=False)
    queries_json = db.Column(db.Text, default='[]')
    status = db.Column(db.String(20), default='running')
    error_message = db.Column(db.Text, default='')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    completed_at = db.Column(db.DateTime, nullable=True)
    assigned_to_id = db.Column(
        db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'), nullable=True
    )
    assigned_by_id = db.Column(
        db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'), nullable=True
    )
    assigned_at = db.Column(db.DateTime, nullable=True)
    review_status = db.Column(db.String(20), default='unassigned', nullable=False)
    is_archived = db.Column(db.Boolean, default=False, nullable=False)
    archived_by_id = db.Column(
        db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'), nullable=True
    )
    archived_at = db.Column(db.DateTime, nullable=True)
    archive_reason = db.Column(db.Text, default='')

    user = db.relationship('User', foreign_keys=[user_id], backref='evidence_audits')
    assignee = db.relationship(
        'User', foreign_keys=[assigned_to_id],
        backref=db.backref('assigned_evidence_audits', lazy=True),
    )
    assigner = db.relationship(
        'User', foreign_keys=[assigned_by_id],
        backref=db.backref('assigned_by_evidence_audits', lazy=True),
    )
    archived_by = db.relationship(
        'User', foreign_keys=[archived_by_id],
        backref=db.backref('archived_evidence_audits', lazy=True),
    )
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

    @property
    def confidence(self):
        """Stable confidence snapshot, with a legacy relationship fallback."""
        if self.prediction_confidence is not None:
            return float(self.prediction_confidence)
        if self.prediction_record is not None:
            return float(self.prediction_record.confidence)
        return None

    @property
    def requires_fact_check(self):
        """Risk-bearing labels enter review; preserve published legacy reviews."""
        return (
            self.predicted_label in FACT_CHECK_REQUIRED_LABELS
            or self.fact_check_review is not None
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


class EvidenceAssignmentEvent(db.Model):
    """Immutable assignment/status event for the review-case audit trail."""
    __tablename__ = 'evidence_assignment_events'

    id = db.Column(db.Integer, primary_key=True)
    audit_id = db.Column(
        db.Integer,
        db.ForeignKey('evidence_audits.id', ondelete='CASCADE'),
        nullable=False,
    )
    actor_id = db.Column(
        db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'), nullable=True
    )
    from_assignee_id = db.Column(
        db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'), nullable=True
    )
    to_assignee_id = db.Column(
        db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'), nullable=True
    )
    action = db.Column(db.String(30), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    audit = db.relationship(
        'EvidenceAudit',
        backref=db.backref(
            'assignment_events', lazy=True, cascade='all, delete-orphan',
            order_by='EvidenceAssignmentEvent.created_at',
        ),
    )
    actor = db.relationship('User', foreign_keys=[actor_id])
    from_assignee = db.relationship('User', foreign_keys=[from_assignee_id])
    to_assignee = db.relationship('User', foreign_keys=[to_assignee_id])


class EvidenceCaseDeletionLog(db.Model):
    """Minimal immutable record retained after an unreviewed case is deleted."""
    __tablename__ = 'evidence_case_deletion_logs'

    id = db.Column(db.Integer, primary_key=True)
    audit_id_snapshot = db.Column(db.Integer, nullable=False, index=True)
    case_reference = db.Column(db.String(30), nullable=False)
    deleted_by_id = db.Column(
        db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'), nullable=True
    )
    reason = db.Column(db.Text, nullable=False)
    claim_text = db.Column(db.Text, nullable=False)
    predicted_label = db.Column(db.String(20), nullable=False)
    review_status = db.Column(db.String(20), nullable=False)
    was_reviewed = db.Column(db.Boolean, default=False, nullable=False)
    deleted_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    deleted_by = db.relationship('User', foreign_keys=[deleted_by_id])
