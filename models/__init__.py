"""Database models package — re-exports all models and shared objects."""
from models.user import User
from models.prediction import Prediction, ClaimRecord
from models.evidence import (
    EvidenceAudit, EvidenceSource, FactCheckReview, EvidenceAssignmentEvent,
    EvidenceCaseDeletionLog,
)
from models.otp import OTP
from models.batch import BatchJob, BatchItem
from models.notification import Notification

# Shared extension objects
from models.user import db, login_manager, mail

__all__ = [
    'db', 'login_manager', 'mail',
    'User', 'Prediction', 'ClaimRecord', 'OTP', 'BatchJob', 'BatchItem',
    'EvidenceAudit', 'EvidenceSource', 'FactCheckReview', 'EvidenceAssignmentEvent',
    'EvidenceCaseDeletionLog',
    'Notification',
]
