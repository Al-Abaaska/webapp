"""Normalize third-party evidence before it reaches the database or browser."""

from models import EvidenceSource


MAX_EVIDENCE_SNIPPET_CHARS = 12_000


def _bounded_text(value, max_chars):
    text = str(value or '').replace('\x00', ' ')
    return text[:max_chars]


def normalize_evidence_item(item):
    """Return a safe copy while preserving provider-specific display fields."""
    normalized = dict(item or {})
    normalized['title'] = _bounded_text(normalized.get('title'), 2_000)
    normalized['url'] = _bounded_text(normalized.get('url'), 8_000)
    normalized['source'] = _bounded_text(normalized.get('source') or 'Web', 255)
    normalized['platform'] = _bounded_text(
        normalized.get('platform') or 'Web', 50
    )
    normalized['provider'] = _bounded_text(normalized.get('provider'), 50)
    normalized['snippet'] = _bounded_text(
        normalized.get('snippet'), MAX_EVIDENCE_SNIPPET_CHARS
    )
    normalized['date'] = _bounded_text(normalized.get('date'), 50)
    normalized['scoring_method'] = _bounded_text(
        normalized.get('scoring_method') or 'lexical_fallback', 50
    )
    normalized['stance'] = _bounded_text(
        normalized.get('stance') or 'Unclear', 20
    )
    normalized['evidence_kind'] = _bounded_text(
        normalized.get('evidence_kind') or 'general', 30
    )
    normalized['credibility'] = _bounded_text(
        normalized.get('credibility'), 100
    )
    normalized['explanation'] = _bounded_text(
        normalized.get('explanation'), 8_000
    )
    return normalized


def build_evidence_source(audit_id, item):
    """Build one database row from already untrusted provider output."""
    source = normalize_evidence_item(item)
    return EvidenceSource(
        audit_id=audit_id,
        title=source['title'],
        url=source['url'],
        source=source['source'],
        platform=source['platform'],
        provider=source['provider'],
        snippet=source['snippet'],
        published_date=source['date'],
        similarity_score=source.get('similarity_score', 0),
        scoring_method=source['scoring_method'],
        stance=source['stance'],
        evidence_kind=source['evidence_kind'],
        credibility=source['credibility'],
        explanation=source['explanation'],
    )
