"""Aggregate repeated evidence-source appearances without treating AI output as fact."""

import re
from collections import OrderedDict
from urllib.parse import parse_qs, urlparse

from models import EvidenceSource


AI_LABELS = ('Reliable', 'Misinformation', 'Misleading')
HUMAN_DECISIONS = (
    'Confirmed Reliable',
    'Confirmed Misinformation',
    'Confirmed Misleading',
    'Insufficient Evidence',
)

_GENERIC_SOURCE_NAMES = {
    '', 'web', 'website', 'general web', 'news', 'source', 'unknown',
    'youtube', 'facebook', 'twitter', 'x', 'google', 'bing',
}


def _clean_text(value):
    return re.sub(r'\s+', ' ', (value or '').strip())


def _normalized_text(value):
    return _clean_text(value).casefold()


def canonical_platform(value):
    normalized = _normalized_text(value)
    if normalized in {'youtube', 'you tube'}:
        return 'YouTube'
    if normalized in {'facebook', 'fb'}:
        return 'Facebook'
    if normalized in {'twitter', 'x', 'x/twitter'}:
        return 'X'
    if normalized in {'telegram'}:
        return 'Telegram'
    if normalized in {'web', 'website', 'general web', 'site', ''}:
        return 'Website'
    return _clean_text(value) or 'Website'


def _url_parts(url):
    raw = _clean_text(url)
    if raw and '://' not in raw:
        raw = 'https://' + raw
    try:
        parsed = urlparse(raw)
    except ValueError:
        return '', '', {}
    host = (parsed.hostname or '').casefold()
    if host.startswith('www.'):
        host = host[4:]
    return host, parsed.path or '', parse_qs(parsed.query or '')


def _social_locator(source):
    """Return a stable page/channel/video locator when the source name is generic."""
    host, path, query = _url_parts(source.url)
    segments = [part for part in path.split('/') if part]
    platform = canonical_platform(source.platform)

    if platform == 'YouTube':
        if 'v' in query and query['v']:
            return f'video:{query["v"][0]}'
        if segments and segments[0] in {'channel', 'c', 'user', '@'} and len(segments) > 1:
            return f'channel:{segments[1].casefold()}'
        if segments and segments[0].startswith('@'):
            return f'channel:{segments[0].casefold()}'
    if platform == 'Facebook' and segments:
        ignored = {'watch', 'reel', 'share', 'story.php', 'permalink.php', 'photo.php'}
        if segments[0].casefold() not in ignored:
            return f'page:{segments[0].casefold()}'
    if platform in {'X', 'Telegram'} and segments:
        return f'account:{segments[0].casefold()}'
    return f'url:{host}{path.casefold()}' if host else f'row:{source.id}'


def source_identity_key(source):
    """Build a repeatable identity key from platform, publisher name, and URL."""
    platform = canonical_platform(source.platform)
    normalized_name = _normalized_text(source.source)
    host, _, _ = _url_parts(source.url)

    if normalized_name not in _GENERIC_SOURCE_NAMES:
        identity = f'name:{normalized_name}'
    elif platform == 'Website' and host:
        identity = f'domain:{host}'
    elif platform in {'YouTube', 'Facebook', 'X', 'Telegram'}:
        identity = _social_locator(source)
    elif host:
        identity = f'domain:{host}'
    else:
        identity = f'row:{source.id}'
    return platform.casefold(), identity


def can_view_audit(user, audit):
    """Apply the same case visibility rules used by history and fact-check review pages."""
    if user.is_admin:
        return True
    if audit.user_id == user.id:
        return True
    if user.can_fact_check:
        return not audit.is_archived and audit.assigned_to_id in (None, user.id)
    return False


def _display_identity(source):
    platform = canonical_platform(source.platform)
    name = _clean_text(source.source)
    normalized_name = _normalized_text(name)
    host, path, _ = _url_parts(source.url)
    segments = [part for part in path.split('/') if part]

    if normalized_name in _GENERIC_SOURCE_NAMES:
        social_name = ''
        if platform == 'Facebook' and segments:
            ignored = {'watch', 'reel', 'share', 'story.php', 'permalink.php', 'photo.php'}
            if segments[0].casefold() not in ignored:
                social_name = segments[0]
        elif platform == 'YouTube' and segments:
            if segments[0].startswith('@'):
                social_name = segments[0]
            elif segments[0] in {'channel', 'c', 'user'} and len(segments) > 1:
                social_name = segments[1]
        elif platform in {'X', 'Telegram'} and segments:
            social_name = segments[0]
        name = social_name or host or name or 'Unknown Source'

    if platform == 'YouTube':
        label = 'Channel'
        value = name
    elif platform == 'Facebook':
        label = 'Page'
        value = name
    elif platform in {'X', 'Telegram'}:
        label = 'Account'
        value = name
    else:
        label = 'Domain'
        value = host or name

    homepage = f'https://{host}' if platform == 'Website' and host else source.url
    return name, platform, label, value, homepage


def build_source_profile(seed_source, user):
    """Build source-level AI and human histories for evidence cases visible to the user."""
    identity_key = source_identity_key(seed_source)
    appearances = [
        source for source in EvidenceSource.query.order_by(EvidenceSource.retrieved_at.desc()).all()
        if source_identity_key(source) == identity_key and can_view_audit(user, source.audit)
    ]

    source_name, platform, locator_label, locator_value, homepage = _display_identity(seed_source)
    ai_counts = OrderedDict((label, 0) for label in AI_LABELS)
    human_counts = OrderedDict((decision, 0) for decision in HUMAN_DECISIONS)

    audit_appearances = {}
    for source in appearances:
        current = audit_appearances.get(source.audit_id)
        if current is None or (source.retrieved_at or source.audit.created_at) > (
            current.retrieved_at or current.audit.created_at
        ):
            audit_appearances[source.audit_id] = source

    recent_claims = []
    for source in audit_appearances.values():
        audit = source.audit
        if audit.predicted_label in ai_counts:
            ai_counts[audit.predicted_label] += 1
        review = audit.fact_check_review
        if review and review.decision in human_counts:
            human_counts[review.decision] += 1
        recent_claims.append({
            'audit': audit,
            'claim': audit.main_claim or audit.original_text,
            'ai_prediction': audit.predicted_label,
            'confidence': audit.confidence,
            'human_verdict': review.decision if review else None,
            'date': audit.created_at,
        })

    recent_claims.sort(key=lambda item: item['date'], reverse=True)
    seen_values = [
        source.retrieved_at or source.audit.created_at for source in appearances
        if source.retrieved_at or source.audit.created_at
    ]

    return {
        'source_name': source_name,
        'platform': platform,
        'locator_label': locator_label,
        'locator_value': locator_value,
        'homepage': homepage,
        'first_seen': min(seen_values) if seen_values else None,
        'last_seen': max(seen_values) if seen_values else None,
        'total_claims': len(audit_appearances),
        'total_appearances': len(appearances),
        'human_reviewed_claims': sum(human_counts.values()),
        'ai_counts': ai_counts,
        'human_counts': human_counts,
        'recent_claims': recent_claims[:25],
    }
