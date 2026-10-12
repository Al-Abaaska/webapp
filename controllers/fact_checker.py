import json
from datetime import datetime
from functools import wraps

from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user

from models import (
    db, Prediction, EvidenceAudit, EvidenceSource,
    FactCheckReview, EvidenceAssignmentEvent, ClaimRecord, Notification,
)
from models.evidence import FACT_CHECK_REQUIRED_LABELS

FACT_CHECK_DECISIONS = (
    'Confirmed Reliable',
    'Confirmed Misinformation',
    'Confirmed Misleading',
    'Insufficient Evidence',
)


fact_checker_bp = Blueprint('fact_checker', __name__, url_prefix='/fact-checker')


def fact_checker_required(f):
    @wraps(f)
    @login_required
    def decorated(*args, **kwargs):
        if not current_user.is_fact_checker:
            flash('Fact-checker access required.', 'danger')
            return redirect(url_for('main.dashboard'))
        return f(*args, **kwargs)
    return decorated


def reviewer_or_admin_required(f):
    """Allow fact-checkers to review and administrators to inspect a case."""
    @wraps(f)
    @login_required
    def decorated(*args, **kwargs):
        if not (current_user.is_fact_checker or current_user.is_admin):
            flash('Fact-check case access required.', 'danger')
            return redirect(url_for('main.dashboard'))
        return f(*args, **kwargs)
    return decorated


def _record_assignment_event(audit, action, from_assignee_id=None, to_assignee_id=None):
    db.session.add(EvidenceAssignmentEvent(
        audit=audit,
        actor_id=current_user.id,
        from_assignee_id=from_assignee_id,
        to_assignee_id=to_assignee_id,
        action=action,
    ))


def _can_manage_review(audit):
    return (
        audit.requires_fact_check
        and current_user.is_fact_checker
        and audit.assigned_to_id == current_user.id
    )


def _case_risk_rank(audit):
    confidence = audit.confidence or 0
    if audit.predicted_label == 'Misinformation':
        return 'critical' if confidence >= 90 else ('high' if confidence >= 75 else 'medium')
    if audit.predicted_label == 'Misleading':
        return 'high' if confidence >= 80 else ('medium' if confidence >= 60 else 'uncertain')
    return 'low' if confidence >= 80 else 'uncertain'


@fact_checker_bp.route('/dashboard')
@fact_checker_required
def dashboard():
    """Fact-checker dashboard — priority queue, stats, evidence alerts."""
    from sqlalchemy import func, case

    # --- Counts ---
    complete_audits = EvidenceAudit.query.filter(
        EvidenceAudit.status == 'complete',
        EvidenceAudit.predicted_label.in_(FACT_CHECK_REQUIRED_LABELS),
        EvidenceAudit.assigned_to_id == current_user.id,
        EvidenceAudit.is_archived.is_(False),
    )

    pending_count = complete_audits.filter(EvidenceAudit.review_status == 'pending').count()
    reviewed_count = (
        EvidenceAudit.query.join(FactCheckReview)
        .filter(FactCheckReview.reviewer_id == current_user.id)
        .count()
    )
    total_count = complete_audits.count()
    unassigned_count = EvidenceAudit.query.filter(
        EvidenceAudit.status == 'complete',
        EvidenceAudit.predicted_label.in_(FACT_CHECK_REQUIRED_LABELS),
        EvidenceAudit.assigned_to_id.is_(None),
        EvidenceAudit.review_status == 'unassigned',
        EvidenceAudit.is_archived.is_(False),
    ).count()

    open_assigned = complete_audits.filter(
        EvidenceAudit.review_status.in_(['pending', 'under_review'])
    ).all()
    high_risk_count = sum(
        1 for audit in open_assigned
        if _case_risk_rank(audit) in ('critical', 'high')
    )

    under_review_count = complete_audits.filter(
        EvidenceAudit.review_status == 'under_review'
    ).count()

    # --- Priority Queue: pending audits sorted by risk then date ---
    priority_audits = (
        EvidenceAudit.query
        .outerjoin(
            ClaimRecord,
            ClaimRecord.investigation_audit_id == EvidenceAudit.id,
        )
        .filter(
            EvidenceAudit.status == 'complete',
            EvidenceAudit.predicted_label.in_(FACT_CHECK_REQUIRED_LABELS),
            EvidenceAudit.assigned_to_id == current_user.id,
            EvidenceAudit.review_status.in_(['pending', 'under_review']),
            EvidenceAudit.is_archived.is_(False),
        )
        .order_by(
            case(
                (EvidenceAudit.predicted_label == 'Misinformation', 0),
                (EvidenceAudit.predicted_label == 'Misleading', 1),
                else_=2,
            ),
            db.func.coalesce(ClaimRecord.submission_count, 1).desc(),
            EvidenceAudit.created_at.desc(),
        )
        .limit(10)
        .all()
    )

    # Enrich priority audits with source stats
    priority_data = []
    for audit in priority_audits:
        sources = EvidenceSource.query.filter_by(audit_id=audit.id).all()
        trusted = sum(1 for s in sources if s.evidence_kind == 'trusted')
        repeats = sum(1 for s in sources if s.stance == 'Repeats')
        supports = sum(1 for s in sources if s.stance == 'Supports')
        unclear = len(sources) - repeats - supports
        avg_sim = (sum(s.similarity_score for s in sources) / len(sources)) if sources else 0

        # Evidence alert flags
        alerts = []
        if len(sources) == 0:
            alerts.append('no_evidence')
        elif trusted == 0:
            alerts.append('no_trusted')
        if repeats > supports and repeats > 0:
            alerts.append('only_repeats')
        if avg_sim < 40 and len(sources) > 0:
            alerts.append('weak_match')

        confidence = audit.confidence or 0
        risk_level = _case_risk_rank(audit)

        priority_data.append({
            'audit': audit,
            'source_count': len(sources),
            'trusted_count': trusted,
            'repeats_count': repeats,
            'supports_count': supports,
            'unclear_count': unclear,
            'avg_similarity': round(avg_sim, 1),
            'confidence': confidence,
            'risk_level': risk_level,
            'alerts': alerts,
        })

    # --- Evidence Alerts: pending audits with issues ---
    alert_audits = (
        EvidenceAudit.query
        .filter(
            EvidenceAudit.status == 'complete',
            EvidenceAudit.predicted_label.in_(FACT_CHECK_REQUIRED_LABELS),
            EvidenceAudit.assigned_to_id == current_user.id,
            EvidenceAudit.review_status.in_(['pending', 'under_review']),
            EvidenceAudit.is_archived.is_(False),
        )
        .order_by(EvidenceAudit.created_at.desc())
        .limit(20)
        .all()
    )

    evidence_alerts = []
    for audit in alert_audits:
        sources = EvidenceSource.query.filter_by(audit_id=audit.id).all()
        trusted = sum(1 for s in sources if s.evidence_kind == 'trusted')
        repeats = sum(1 for s in sources if s.stance == 'Repeats')
        supports = sum(1 for s in sources if s.stance == 'Supports')

        alert_type = None
        alert_msg = ''
        if len(sources) == 0:
            alert_type = 'critical'
            alert_msg = 'No evidence sources retrieved'
        elif trusted == 0:
            alert_type = 'warning'
            alert_msg = 'No trusted Somali source confirmation'
        elif repeats > supports and repeats >= 2:
            alert_type = 'info'
            alert_msg = f'Content only repeated {repeats} times, not independently verified'

        if alert_type:
            evidence_alerts.append({
                'audit': audit,
                'alert_type': alert_type,
                'alert_msg': alert_msg,
                'source_count': len(sources),
            })

    # --- AI vs Human Agreement ---
    agreement_stats = {'confirm': 0, 'change': 0}
    reviewed_audits = (
        EvidenceAudit.query
        .join(FactCheckReview)
        .filter(
            FactCheckReview.reviewer_id == current_user.id,
            EvidenceAudit.is_archived.is_(False),
        )
        .order_by(FactCheckReview.updated_at.desc())
        .limit(50)
        .all()
    )
    for audit in reviewed_audits:
        review = audit.fact_check_review
        if not review:
            continue
        ai_label = audit.predicted_label
        decision = review.decision
        if (ai_label == 'Reliable' and decision == 'Confirmed Reliable') or \
           (ai_label == 'Misinformation' and decision == 'Confirmed Misinformation') or \
           (ai_label == 'Misleading' and decision == 'Confirmed Misleading'):
            agreement_stats['confirm'] += 1
        else:
            agreement_stats['change'] += 1

    total_decisions = agreement_stats['confirm'] + agreement_stats['change']
    agreement_pct = round((agreement_stats['confirm'] / total_decisions * 100) if total_decisions > 0 else 0, 1)

    # --- Recent Decisions ---
    recent_decisions = (
        EvidenceAudit.query
        .join(FactCheckReview)
        .filter(
            FactCheckReview.reviewer_id == current_user.id,
            EvidenceAudit.is_archived.is_(False),
        )
        .order_by(FactCheckReview.updated_at.desc())
        .limit(5)
        .all()
    )

    # --- Next case for "Review Next" button ---
    next_case = (
        EvidenceAudit.query
        .filter(
            EvidenceAudit.status == 'complete',
            EvidenceAudit.predicted_label.in_(FACT_CHECK_REQUIRED_LABELS),
            EvidenceAudit.assigned_to_id == current_user.id,
            EvidenceAudit.review_status.in_(['pending', 'under_review']),
            EvidenceAudit.is_archived.is_(False),
        )
        .order_by(
            case(
                (EvidenceAudit.predicted_label == 'Misinformation', 0),
                (EvidenceAudit.predicted_label == 'Misleading', 1),
                else_=2,
            ),
            EvidenceAudit.created_at.desc(),
        )
        .first()
    )

    return render_template(
        'fact_checker/dashboard.html',
        pending_count=pending_count,
        reviewed_count=reviewed_count,
        total_count=total_count,
        high_risk_count=high_risk_count,
        under_review_count=under_review_count,
        priority_data=priority_data,
        evidence_alerts=evidence_alerts[:8],
        agreement_stats=agreement_stats,
        agreement_pct=agreement_pct,
        total_decisions=total_decisions,
        recent_decisions=recent_decisions,
        next_case=next_case,
        unassigned_count=unassigned_count,
    )


@fact_checker_bp.route('/')
@fact_checker_required
def index():
    from sqlalchemy import and_, or_
    page = request.args.get('page', 1, type=int)
    status = request.args.get('status', 'pending')
    query = EvidenceAudit.query.filter(
        EvidenceAudit.status == 'complete',
        EvidenceAudit.predicted_label.in_(FACT_CHECK_REQUIRED_LABELS),
        EvidenceAudit.is_archived.is_(False),
    )

    if status == 'under_review':
        query = query.filter(
            EvidenceAudit.assigned_to_id == current_user.id,
            EvidenceAudit.review_status == 'under_review',
        )
    elif status == 'completed':
        query = query.join(FactCheckReview).filter(
            FactCheckReview.reviewer_id == current_user.id
        )
    elif status == 'high_risk':
        stored_confidence = db.func.coalesce(
            EvidenceAudit.prediction_confidence, Prediction.confidence, 0
        )
        query = query.outerjoin(
            Prediction, EvidenceAudit.prediction_id == Prediction.id
        ).filter(
            EvidenceAudit.assigned_to_id == current_user.id,
            EvidenceAudit.review_status.in_(['pending', 'under_review']),
            or_(
                and_(
                    EvidenceAudit.predicted_label == 'Misinformation',
                    stored_confidence >= 75,
                ),
                and_(
                    EvidenceAudit.predicted_label == 'Misleading',
                    stored_confidence >= 80,
                ),
            ),
        )
    elif status == 'unassigned':
        query = query.filter(
            EvidenceAudit.assigned_to_id.is_(None),
            EvidenceAudit.review_status == 'unassigned',
        )
    else:
        status = 'pending'
        query = query.filter(
            EvidenceAudit.assigned_to_id == current_user.id,
            EvidenceAudit.review_status == 'pending',
        )

    audits = query.order_by(EvidenceAudit.created_at.desc()).paginate(
        page=page, per_page=15, error_out=False
    )
    return render_template(
        'fact_checker/index.html', audits=audits, status_filter=status
    )


@fact_checker_bp.route('/audit/<int:audit_id>')
@reviewer_or_admin_required
def review_detail(audit_id):
    audit = EvidenceAudit.query.get_or_404(audit_id)
    if current_user.is_fact_checker and not audit.requires_fact_check:
        flash('Reliable predictions do not enter the fact-checker workflow.', 'info')
        return redirect(url_for('fact_checker.index'))
    if audit.is_archived and not current_user.is_admin:
        flash('This case has been archived by an administrator.', 'info')
        return redirect(url_for('fact_checker.index'))
    if current_user.is_fact_checker and audit.assigned_to_id not in (None, current_user.id):
        flash('This case is assigned to another fact-checker.', 'danger')
        return redirect(url_for('fact_checker.index'))
    sources = EvidenceSource.query.filter_by(audit_id=audit.id).order_by(
        EvidenceSource.similarity_score.desc()
    ).all()
    try:
        queries = json.loads(audit.queries_json or '[]')
    except (TypeError, ValueError):
        queries = []
    return render_template(
        'fact_checker/review.html',
        audit=audit,
        sources=sources,
        queries=queries,
        decisions=FACT_CHECK_DECISIONS,
        can_manage=_can_manage_review(audit),
        assignment_events=audit.assignment_events,
    )


@fact_checker_bp.route('/audit/<int:audit_id>/claim', methods=['POST'])
@fact_checker_required
def claim_case(audit_id):
    if current_user.is_admin:
        flash('Admins assign cases from the Admin Dashboard.', 'info')
        return redirect(url_for('admin.index'))
    now = datetime.utcnow()
    updated = EvidenceAudit.query.filter(
        EvidenceAudit.id == audit_id,
        EvidenceAudit.status == 'complete',
        EvidenceAudit.predicted_label.in_(FACT_CHECK_REQUIRED_LABELS),
        EvidenceAudit.assigned_to_id.is_(None),
        EvidenceAudit.review_status == 'unassigned',
        EvidenceAudit.is_archived.is_(False),
    ).update({
        EvidenceAudit.assigned_to_id: current_user.id,
        EvidenceAudit.assigned_at: now,
        EvidenceAudit.review_status: 'pending',
    }, synchronize_session=False)
    if updated != 1:
        db.session.rollback()
        flash('This case has already been claimed or is unavailable.', 'warning')
        return redirect(url_for('fact_checker.index', status='unassigned'))
    audit = db.session.get(EvidenceAudit, audit_id)
    _record_assignment_event(audit, 'claimed', None, current_user.id)
    db.session.commit()
    flash(f'Case #{audit.id} is now assigned to you.', 'success')
    return redirect(url_for('fact_checker.review_detail', audit_id=audit.id))


@fact_checker_bp.route('/audit/<int:audit_id>/start', methods=['POST'])
@fact_checker_required
def start_review(audit_id):
    audit = EvidenceAudit.query.get_or_404(audit_id)
    if not audit.requires_fact_check:
        flash('Reliable predictions do not require fact-checker review.', 'info')
        return redirect(url_for('fact_checker.index'))
    if audit.is_archived:
        flash('Archived cases cannot be reviewed until an administrator restores them.', 'warning')
        return redirect(url_for('fact_checker.index'))
    if not _can_manage_review(audit):
        flash('Only the assigned fact-checker can start this review.', 'danger')
        return redirect(url_for('fact_checker.index'))
    if audit.review_status == 'pending':
        audit.review_status = 'under_review'
        _record_assignment_event(
            audit, 'review_started', audit.assigned_to_id, audit.assigned_to_id
        )
        db.session.commit()
    return redirect(url_for('fact_checker.review_detail', audit_id=audit.id))


@fact_checker_bp.route('/audit/<int:audit_id>/decision', methods=['POST'])
@fact_checker_required
def submit_decision(audit_id):
    audit = EvidenceAudit.query.get_or_404(audit_id)
    if not audit.requires_fact_check:
        flash('Reliable predictions do not require fact-checker review.', 'info')
        return redirect(url_for('fact_checker.index'))
    if audit.is_archived:
        flash('Archived cases cannot receive a decision until an administrator restores them.', 'warning')
        return redirect(url_for('fact_checker.index'))
    if audit.status != 'complete':
        flash('This evidence audit is not complete yet.', 'warning')
        return redirect(url_for('fact_checker.review_detail', audit_id=audit.id))
    if not _can_manage_review(audit):
        flash('Only the assigned fact-checker or an admin can decide this case.', 'danger')
        return redirect(url_for('fact_checker.index'))
    if audit.review_status not in ('under_review', 'completed'):
        flash('Start the assigned review before submitting a decision.', 'warning')
        return redirect(url_for('fact_checker.review_detail', audit_id=audit.id))

    was_completed = audit.review_status == 'completed'
    decision = request.form.get('decision', '').strip()
    explanation = request.form.get('explanation', '').strip()
    if decision not in FACT_CHECK_DECISIONS:
        flash('Select a valid fact-checker decision.', 'danger')
        return redirect(url_for('fact_checker.review_detail', audit_id=audit.id))
    if len(explanation) < 10:
        flash('Provide a clear explanation of at least 10 characters.', 'danger')
        return redirect(url_for('fact_checker.review_detail', audit_id=audit.id))

    review = audit.fact_check_review
    if review is None:
        review = FactCheckReview(audit=audit)
        db.session.add(review)
    review.reviewer = current_user
    review.decision = decision
    review.explanation = explanation
    review.updated_at = datetime.utcnow()
    audit.review_status = 'completed'
    if audit.assigned_to_id is None:
        audit.assigned_to_id = current_user.id
        audit.assigned_at = datetime.utcnow()
    _record_assignment_event(
        audit, 'decision_updated' if was_completed else 'review_completed',
        audit.assigned_to_id, audit.assigned_to_id,
    )
    notification = Notification.query.filter_by(
        user_id=audit.user_id,
        audit_id=audit.id,
        notification_type='final_decision',
    ).first()
    if notification is None:
        notification = Notification(
            user_id=audit.user_id,
            audit_id=audit.id,
            notification_type='final_decision',
            title=f'Final decision published for FC-{audit.id}',
            message=(
                f'Fact-checker decision: {decision}. Open the tracker to read '
                'the explanation and reviewed evidence.'
            ),
        )
        db.session.add(notification)
    else:
        # Reuse the original notification if a completed decision is corrected.
        notification.title = f'Final decision updated for FC-{audit.id}'
        notification.message = (
            f'Updated fact-checker decision: {decision}. Open the tracker to '
            'read the latest explanation and reviewed evidence.'
        )
        notification.is_read = False
        notification.read_at = None
        notification.created_at = datetime.utcnow()
    db.session.commit()
    flash('Fact-checker decision saved.', 'success')
    return redirect(url_for('fact_checker.review_detail', audit_id=audit.id))
