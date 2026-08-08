"""Database models package — re-exports all models and shared objects."""
from models.user import User
from models.prediction import Prediction, ClaimRecord
from models.evidence import (
    EvidenceAudit, EvidenceSource, FactCheckReview, EvidenceAssignmentEvent,
)
from models.otp import OTP
from models.batch import BatchJob

# Shared extension objects
from models.user import db, login_manager, mail

__all__ = [
    'db', 'login_manager', 'mail',
    'User', 'Prediction', 'ClaimRecord', 'OTP', 'BatchJob',
    'EvidenceAudit', 'EvidenceSource', 'FactCheckReview', 'EvidenceAssignmentEvent',
]
