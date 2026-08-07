"""Deterministic exact and near-duplicate matching for submitted claims."""
import hashlib
import re
from difflib import SequenceMatcher


CLAIM_STOPWORDS = {
    'ayaa', 'waxaa', 'waxa', 'iyo', 'ama', 'oo', 'ah', 'ay', 'uu', 'u',
    'ka', 'ku', 'la', 'loo', 'lagu', 'in', 'iyo', 'ee', 'isla', 'si', 'aan',
    'the', 'a', 'an', 'of', 'to', 'for', 'on', 'with', 'from',
}


def normalize_claim(text):
    """Normalize harmless formatting differences without changing meaning."""
    value = str(text or '').casefold()
    value = re.sub(r'https?://\S+', ' ', value)
    value = re.sub(r'[^\w\s]', ' ', value, flags=re.UNICODE)
    return re.sub(r'\s+', ' ', value).strip()


def claim_fingerprint(text):
    return hashlib.sha256(normalize_claim(text).encode('utf-8')).hexdigest()


def _important_tokens(text):
    return [
        token for token in normalize_claim(text).split()
        if len(token) > 1 and token not in CLAIM_STOPWORDS
    ]


def claim_similarity(left, right):
    """Return 0-100 only for conservatively similar, non-identical claims."""
    left_norm = normalize_claim(left)
    right_norm = normalize_claim(right)
    if not left_norm or not right_norm or left_norm == right_norm:
        return 100.0 if left_norm == right_norm and left_norm else 0.0

    left_tokens = _important_tokens(left_norm)
    right_tokens = _important_tokens(right_norm)
    left_set, right_set = set(left_tokens), set(right_tokens)
    shared = left_set & right_set
    if len(shared) < 3:
        return 0.0

    union = left_set | right_set
    jaccard = len(shared) / max(len(union), 1)
    containment = len(shared) / max(min(len(left_set), len(right_set)), 1)
    sequence = SequenceMatcher(None, left_norm, right_norm).ratio()

    # Similar is only a warning; exact reuse is handled exclusively by SHA-256.
    if not (
        (jaccard >= 0.58 and sequence >= 0.62)
        or (containment >= 0.78 and sequence >= 0.68)
    ):
        return 0.0
    return round(max(jaccard, (sequence + containment) / 2) * 100, 1)


def find_similar_claim(text, records, threshold=70.0):
    best_record, best_score = None, 0.0
    for record in records:
        candidate = record.original_text or record.normalized_text
        score = claim_similarity(text, candidate)
        if score >= threshold and score > best_score:
            best_record, best_score = record, score
    return best_record, best_score
