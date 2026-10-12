"""Source Intelligence Profile routes."""

from flask import Blueprint, abort, render_template, url_for
from flask_login import current_user, login_required

from models import EvidenceSource
from services.source_intelligence_service import build_source_profile, can_view_audit


sources_bp = Blueprint('sources', __name__, url_prefix='/sources')


def _audit_detail_url(audit):
    if current_user.is_admin:
        return url_for('fact_checker.review_detail', audit_id=audit.id)
    if current_user.can_fact_check and audit.assigned_to_id in (None, current_user.id):
        return url_for('fact_checker.review_detail', audit_id=audit.id)
    return url_for('history.evidence_detail', audit_id=audit.id)


@sources_bp.route('/<int:source_id>')
@login_required
def profile(source_id):
    seed_source = EvidenceSource.query.get_or_404(source_id)
    if not can_view_audit(current_user, seed_source.audit):
        abort(403)

    profile_data = build_source_profile(seed_source, current_user)
    for claim in profile_data['recent_claims']:
        claim['detail_url'] = _audit_detail_url(claim['audit'])

    return render_template(
        'history/source_profile.html',
        source=seed_source,
        profile=profile_data,
        back_url=_audit_detail_url(seed_source.audit),
    )
