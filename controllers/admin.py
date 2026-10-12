import json
import io
import os
from datetime import datetime
from functools import wraps

import pandas as pd

from flask import (
    Blueprint, render_template, request, redirect, url_for, flash, jsonify,
    current_app, abort, send_file,
)
from flask_login import login_required, current_user

from models import (
    db, User, Prediction, BatchJob, EvidenceAudit,
    FactCheckReview, EvidenceAssignmentEvent, ClaimRecord,
    Notification,
)
from models.evidence import FACT_CHECK_REQUIRED_LABELS
from services.ml_service import MODEL_NAMES
from services.email_service import send_fact_checker_application_email


admin_bp = Blueprint('admin', __name__, url_prefix='/admin')


def _is_primary_admin():
    return (
        current_user.is_admin
        and current_user.email.casefold()
        == current_app.config['PRIMARY_ADMIN_EMAIL'].casefold()
    )


def _is_primary_user(user):
    return (
        user.is_admin
        and user.email.casefold()
        == current_app.config['PRIMARY_ADMIN_EMAIL'].casefold()
    )


def admin_required(f):
    @wraps(f)
    @login_required
    def decorated(*args, **kwargs):
        if not current_user.is_admin:
            flash('Admin access required.', 'danger')
            return redirect(url_for('main.dashboard'))
        return f(*args, **kwargs)
    return decorated


def primary_admin_required(f):
    @wraps(f)
    @login_required
    def decorated(*args, **kwargs):
        if not _is_primary_admin():
            flash('Primary administrator access required.', 'danger')
            return redirect(url_for('admin.index'))
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


def _admin_queue_return_url():
    """Return to the current, filtered admin queue after an assignment action."""
    target = request.form.get('return_url', '').strip()
    admin_path = url_for('admin.index')
    if target.startswith(admin_path) and not target.startswith('//'):
        return target
    return admin_path


@admin_bp.route('/')
@admin_required
def index():
    from datetime import timedelta
    from collections import Counter
    from sqlalchemy import case

    all_users = User.query.order_by(User.created_at.desc()).all()
    user_status = request.args.get('user_status', 'all').strip().lower()
    if user_status not in {'all', 'active', 'inactive'}:
        user_status = 'all'
    users = [
        user for user in all_users
        if user_status == 'all'
        or (user_status == 'active' and user.is_active)
        or (user_status == 'inactive' and not user.is_active)
    ]
    pending_fact_checker_applications = User.query.filter_by(
        fact_checker_application_status='pending'
    ).order_by(User.fact_checker_applied_at.asc()).all()
    fact_checker_applications = User.query.filter(
        User.fact_checker_application_status.in_([
            'pending', 'approved', 'rejected', 'revoked',
        ])
    ).order_by(User.fact_checker_applied_at.desc()).all()
    pred_page = request.args.get('pred_page', 1, type=int)
    admin_predictions = Prediction.query.order_by(Prediction.created_at.desc()).paginate(page=pred_page, per_page=15, error_out=False)

    # --- System Stats ---
    total_users = len(all_users)
    fact_checker_count = sum(1 for u in all_users if u.role == 'fact_checker')
    fact_checkers = [
        u for u in all_users if u.role == 'fact_checker' and u.is_active
    ]
    admin_count = sum(1 for u in all_users if u.role == 'admin')
    total_predictions = Prediction.query.count()
    total_batches = BatchJob.query.count()
    total_evidence_audits = EvidenceAudit.query.count()

    # --- Review Progress ---
    complete_audits = EvidenceAudit.query.filter(EvidenceAudit.status == 'complete')
    manageable_audits = EvidenceAudit.query.filter(
        EvidenceAudit.status.in_(['complete', 'failed'])
    )
    review_audits = complete_audits.filter(
        EvidenceAudit.predicted_label.in_(FACT_CHECK_REQUIRED_LABELS)
    )
    active_review_audits = review_audits.filter(
        EvidenceAudit.is_archived.is_(False)
    )
    pending_reviews = active_review_audits.filter(
        EvidenceAudit.review_status.in_(['unassigned', 'pending', 'under_review'])
    ).count()
    completed_reviews = review_audits.join(FactCheckReview).count()
    failed_audits = EvidenceAudit.query.filter(EvidenceAudit.status == 'failed').count()
    insufficient_evidence = (
        complete_audits.join(FactCheckReview)
        .filter(FactCheckReview.decision == 'Insufficient Evidence')
        .count()
    )
    unassigned_reviews = active_review_audits.filter(
        EvidenceAudit.assigned_to_id.is_(None),
        EvidenceAudit.review_status == 'unassigned',
    ).count()
    assigned_open_reviews = active_review_audits.filter(
        EvidenceAudit.assigned_to_id.isnot(None),
        EvidenceAudit.review_status.in_(['pending', 'under_review']),
    ).count()
    active_cases = manageable_audits.filter(
        EvidenceAudit.is_archived.is_(False)
    ).count()
    archived_cases = manageable_audits.filter(
        EvidenceAudit.is_archived.is_(True)
    ).count()
    case_scope = request.args.get('case_scope', 'active').strip().lower()
    if case_scope not in {'active', 'archived'}:
        case_scope = 'active'
    assignment_status = request.args.get('assignment_status', 'all').strip().lower()
    if assignment_status not in {
        'all', 'unassigned', 'assigned', 'pending', 'under_review',
        'not_required', 'completed', 'failed',
    }:
        assignment_status = 'all'
    case_label = request.args.get('case_label', 'all').strip().lower()
    if case_label not in {
        'all', 'reliable', 'misinformation', 'misleading',
    }:
        case_label = 'all'
    selected_assignee_id = request.args.get('assignee_id', type=int)
    queue_sort = request.args.get('queue_sort', 'priority').strip().lower()
    if queue_sort not in {
        'priority', 'spread_desc', 'spread_asc', 'newest', 'oldest',
        'confidence_desc', 'confidence_asc', 'assignment',
    }:
        queue_sort = 'priority'

    assignment_query = (
        manageable_audits
        .outerjoin(
            ClaimRecord,
            ClaimRecord.investigation_audit_id == EvidenceAudit.id,
        )
        .outerjoin(Prediction, EvidenceAudit.prediction_id == Prediction.id)
    )
    if case_scope == 'archived':
        assignment_query = assignment_query.filter(EvidenceAudit.is_archived.is_(True))
    else:
        assignment_query = assignment_query.filter(
            EvidenceAudit.is_archived.is_(False),
        )
    if case_label != 'all':
        assignment_query = assignment_query.filter(
            db.func.lower(EvidenceAudit.predicted_label) == case_label,
        )
    if assignment_status == 'unassigned':
        assignment_query = assignment_query.filter(
            EvidenceAudit.assigned_to_id.is_(None),
            EvidenceAudit.review_status == 'unassigned',
        )
    elif assignment_status == 'assigned':
        assignment_query = assignment_query.filter(
            EvidenceAudit.assigned_to_id.isnot(None),
            EvidenceAudit.review_status.in_(['pending', 'under_review']),
        )
    elif assignment_status in {'pending', 'under_review'}:
        assignment_query = assignment_query.filter(
            EvidenceAudit.review_status == assignment_status,
        )
    elif assignment_status in {'not_required', 'completed'}:
        assignment_query = assignment_query.filter(
            EvidenceAudit.review_status == assignment_status,
        )
    elif assignment_status == 'failed':
        assignment_query = assignment_query.filter(
            EvidenceAudit.status == 'failed',
        )

    if selected_assignee_id:
        assignment_query = assignment_query.filter(
            EvidenceAudit.assigned_to_id == selected_assignee_id,
        )

    spread_count = db.func.coalesce(ClaimRecord.submission_count, 1)
    confidence = db.func.coalesce(
        EvidenceAudit.prediction_confidence, Prediction.confidence, 0
    )
    if queue_sort == 'spread_desc':
        queue_order = (spread_count.desc(), EvidenceAudit.created_at.desc())
    elif queue_sort == 'spread_asc':
        queue_order = (spread_count.asc(), EvidenceAudit.created_at.desc())
    elif queue_sort == 'newest':
        queue_order = (EvidenceAudit.created_at.desc(),)
    elif queue_sort == 'oldest':
        queue_order = (EvidenceAudit.created_at.asc(),)
    elif queue_sort == 'confidence_desc':
        queue_order = (confidence.desc(), EvidenceAudit.created_at.desc())
    elif queue_sort == 'confidence_asc':
        queue_order = (confidence.asc(), EvidenceAudit.created_at.desc())
    elif queue_sort == 'assignment':
        queue_order = (
            case((EvidenceAudit.assigned_to_id.is_(None), 0), else_=1),
            EvidenceAudit.assigned_to_id.asc(),
            EvidenceAudit.created_at.desc(),
        )
    else:
        queue_order = (
            case(
                (EvidenceAudit.predicted_label == 'Misinformation', 0),
                (EvidenceAudit.predicted_label == 'Misleading', 1),
                else_=2,
            ),
            spread_count.desc(),
            EvidenceAudit.created_at.desc(),
        )
    case_page = request.args.get('case_page', 1, type=int)
    assignment_queue = assignment_query.order_by(*queue_order).paginate(
        page=case_page, per_page=20, error_out=False
    )

    # --- Prediction Distribution ---
    pred_dist_raw = db.session.query(
        Prediction.prediction, db.func.count(Prediction.id)
    ).group_by(Prediction.prediction).all()
    pred_distribution = {label: count for label, count in pred_dist_raw}

    # --- Model Usage ---
    raw_model_stats = db.session.query(
        Prediction.model_used, db.func.count(Prediction.id), db.func.avg(Prediction.confidence)
    ).group_by(Prediction.model_used).all()

    model_stats = []
    for key, count, avg_conf in raw_model_stats:
        avg_conf = avg_conf or 0
        most_pred = db.session.query(Prediction.prediction, db.func.count(Prediction.id))\
            .filter_by(model_used=key).group_by(Prediction.prediction)\
            .order_by(db.func.count(Prediction.id).desc()).first()
        model_stats.append({
            'name': MODEL_NAMES.get(key, key), 'key': key, 'count': count,
            'avg_confidence': round(avg_conf / 100, 4),
            'most_predicted': most_pred[0] if most_pred else 'N/A',
        })

    # --- AI vs Human Agreement ---
    agreement_stats = {'confirm': 0, 'change': 0}
    reviewed_audits = (
        EvidenceAudit.query.join(FactCheckReview)
        .order_by(FactCheckReview.updated_at.desc()).limit(50).all()
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

    # --- Recent Activity (last 24h) ---
    recent_users = User.query.filter(User.created_at >= datetime.utcnow() - timedelta(hours=24)).count()
    recent_predictions = Prediction.query.filter(Prediction.created_at >= datetime.utcnow() - timedelta(hours=24)).count()
    recent_audits = EvidenceAudit.query.filter(EvidenceAudit.created_at >= datetime.utcnow() - timedelta(hours=24)).count()

    # --- Recent Predictions ---
    recent_predictions_list = Prediction.query.order_by(Prediction.created_at.desc()).limit(5).all()

    return render_template('admin/index.html',
        users=users, admin_predictions=admin_predictions,
        total_users=total_users, fact_checker_count=fact_checker_count,
        admin_count=admin_count, total_predictions=total_predictions,
        total_batches=total_batches, total_evidence_audits=total_evidence_audits,
        pending_reviews=pending_reviews, completed_reviews=completed_reviews,
        failed_audits=failed_audits, insufficient_evidence=insufficient_evidence,
        pred_distribution=pred_distribution, model_stats=model_stats,
        model_usage_names=json.dumps([m['name'] for m in model_stats]),
        model_usage_counts=json.dumps([m['count'] for m in model_stats]),
        agreement_stats=agreement_stats, agreement_pct=agreement_pct,
        total_decisions=total_decisions,
        recent_users=recent_users, recent_predictions=recent_predictions,
        recent_audits=recent_audits, recent_predictions_list=recent_predictions_list,
        fact_checkers=fact_checkers, assignment_queue=assignment_queue,
        unassigned_reviews=unassigned_reviews,
        assigned_open_reviews=assigned_open_reviews,
        active_cases=active_cases, archived_cases=archived_cases,
        case_scope=case_scope,
        is_primary_admin=_is_primary_admin(),
        assignment_status=assignment_status,
        case_label=case_label,
        selected_assignee_id=selected_assignee_id,
        queue_sort=queue_sort,
        pending_fact_checker_applications=pending_fact_checker_applications,
        fact_checker_applications=fact_checker_applications,
        primary_admin_email=current_app.config['PRIMARY_ADMIN_EMAIL'],
        user_status=user_status,
        MODEL_NAMES=MODEL_NAMES)


@admin_bp.route('/audit/<int:audit_id>/assign', methods=['POST'])
@admin_required
def assign_audit(audit_id):
    audit = EvidenceAudit.query.get_or_404(audit_id)
    assignee_id = request.form.get('assignee_id', type=int)
    assignee = db.session.get(User, assignee_id) if assignee_id else None
    if audit.is_archived:
        flash('Restore the archived case before assigning it.', 'warning')
        return redirect(_admin_queue_return_url())
    if not audit.requires_fact_check:
        flash('Reliable predictions are completed without fact-checker assignment.', 'info')
        return redirect(_admin_queue_return_url())
    if audit.status != 'complete' or audit.review_status == 'completed':
        flash('Only open, completed evidence audits can be assigned.', 'warning')
        return redirect(_admin_queue_return_url())
    if not assignee or not assignee.is_fact_checker or not assignee.is_active:
        flash('Select an active fact-checker.', 'danger')
        return redirect(_admin_queue_return_url())
    previous_id = audit.assigned_to_id
    audit.assigned_to_id = assignee.id
    audit.assigned_by_id = current_user.id
    audit.assigned_at = datetime.utcnow()
    audit.review_status = 'pending'
    _record_assignment_event(
        audit, 'assigned' if previous_id is None else 'reassigned',
        previous_id, assignee.id,
    )
    db.session.commit()
    flash(f'Case #{audit.id} assigned to {assignee.username}.', 'success')
    return redirect(_admin_queue_return_url())


@admin_bp.route('/audit/<int:audit_id>/unassign', methods=['POST'])
@admin_required
def unassign_audit(audit_id):
    audit = EvidenceAudit.query.get_or_404(audit_id)
    if audit.is_archived:
        flash('Restore the archived case before changing its assignment.', 'warning')
        return redirect(_admin_queue_return_url())
    if audit.review_status == 'completed':
        flash('A completed case cannot be returned to the queue.', 'warning')
        return redirect(_admin_queue_return_url())
    previous_id = audit.assigned_to_id
    audit.assigned_to_id = None
    audit.assigned_by_id = current_user.id
    audit.assigned_at = None
    audit.review_status = 'unassigned'
    _record_assignment_event(audit, 'unassigned', previous_id, None)
    db.session.commit()
    flash(f'Case #{audit.id} returned to the unassigned queue.', 'success')
    return redirect(_admin_queue_return_url())


@admin_bp.route('/audit/<int:audit_id>/archive', methods=['POST'])
@admin_required
def archive_audit(audit_id):
    audit = EvidenceAudit.query.get_or_404(audit_id)
    reason = request.form.get('reason', '').strip()
    if audit.is_archived:
        flash(f'Case FC-{audit.id} is already archived.', 'info')
        return redirect(_admin_queue_return_url())
    if len(reason) < 10:
        flash('Provide an archive reason of at least 10 characters.', 'danger')
        return redirect(_admin_queue_return_url())
    audit.is_archived = True
    audit.archived_by_id = current_user.id
    audit.archived_at = datetime.utcnow()
    audit.archive_reason = reason
    _record_assignment_event(
        audit, 'archived', audit.assigned_to_id, audit.assigned_to_id
    )
    db.session.commit()
    flash(f'Case FC-{audit.id} archived. Its audit trail was preserved.', 'success')
    return redirect(_admin_queue_return_url())


@admin_bp.route('/audit/<int:audit_id>/restore', methods=['POST'])
@admin_required
def restore_audit(audit_id):
    audit = EvidenceAudit.query.get_or_404(audit_id)
    if not audit.is_archived:
        flash(f'Case FC-{audit.id} is already active.', 'info')
        return redirect(_admin_queue_return_url())
    audit.is_archived = False
    _record_assignment_event(
        audit, 'restored', audit.assigned_to_id, audit.assigned_to_id
    )
    db.session.commit()
    flash(f'Case FC-{audit.id} restored to active case management.', 'success')
    return redirect(_admin_queue_return_url())


def _resolve_fact_checker_document(user_id, kind):
    applicant = User.query.get_or_404(user_id)
    stored_path = ''
    download_name = ''
    if kind == 'identity':
        stored_path = applicant.fact_checker_id_document or ''
        download_name = f'{applicant.username}-identity{os.path.splitext(stored_path)[1]}'
    elif kind.startswith('certificate-'):
        try:
            certificate_index = int(kind.rsplit('-', 1)[1])
            certificate = applicant.fact_checker_certificates[certificate_index]
            if isinstance(certificate, dict):
                stored_path = certificate.get('path', '')
                download_name = certificate.get('name', '')
            else:
                stored_path = str(certificate)
                download_name = os.path.basename(stored_path)
        except (ValueError, IndexError, TypeError):
            abort(404)
    else:
        abort(404)

    upload_root = os.path.realpath(current_app.config['UPLOAD_FOLDER'])
    absolute_path = os.path.realpath(os.path.join(upload_root, stored_path))
    try:
        inside_upload_root = os.path.commonpath(
            [upload_root, absolute_path]
        ) == upload_root
    except ValueError:
        inside_upload_root = False
    if not stored_path or not inside_upload_root or not os.path.isfile(absolute_path):
        abort(404)
    return absolute_path, download_name or os.path.basename(absolute_path)


@admin_bp.route('/fact-checker-application/<int:user_id>/document/<kind>')
@admin_required
def fact_checker_application_document(user_id, kind):
    absolute_path, download_name = _resolve_fact_checker_document(user_id, kind)
    response = send_file(
        absolute_path,
        as_attachment=False,
        download_name=download_name,
        conditional=True,
    )
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Content-Security-Policy'] = "sandbox; default-src 'none'"
    return response


@admin_bp.route(
    '/fact-checker-application/<int:user_id>/document/<kind>/download'
)
@admin_required
def download_fact_checker_application_document(user_id, kind):
    absolute_path, download_name = _resolve_fact_checker_document(user_id, kind)
    return send_file(
        absolute_path,
        as_attachment=True,
        download_name=download_name,
        conditional=True,
    )


@admin_bp.route('/fact-checker-application/<int:user_id>/<decision>', methods=['POST'])
@admin_required
def review_fact_checker_application(user_id, decision):
    applicant = User.query.get_or_404(user_id)
    decision = decision.strip().lower()
    note = request.form.get('note', '').strip()
    if decision not in {'approve', 'reject'}:
        abort(404)
    if applicant.fact_checker_application_status != 'pending':
        flash('This fact-checker application has already been reviewed.', 'warning')
        return redirect(url_for('admin.index'))
    if decision == 'reject' and len(note) < 5:
        flash('Provide a short reason when rejecting an application.', 'danger')
        return redirect(url_for('admin.index'))

    applicant.fact_checker_reviewed_at = datetime.utcnow()
    applicant.fact_checker_reviewed_by_id = current_user.id
    applicant.fact_checker_application_note = note
    if decision == 'approve':
        applicant.role = 'fact_checker'
        applicant.fact_checker_application_status = 'approved'
        title = 'Fact-checker application approved'
        message = (
            'Your credentials were approved. You can now access the '
            'fact-checker workspace.'
        )
        flash(f'{applicant.username} approved as a fact-checker.', 'success')
    else:
        applicant.role = 'user'
        applicant.fact_checker_application_status = 'rejected'
        title = 'Fact-checker application reviewed'
        message = f'Your application was not approved. Administrator note: {note}'
        flash(f'{applicant.username}\'s fact-checker application was rejected.', 'info')

    notification = Notification.query.filter_by(
        user_id=applicant.id,
        audit_id=None,
        notification_type='fact_checker_application',
    ).first()
    if notification is None:
        notification = Notification(
            user_id=applicant.id,
            notification_type='fact_checker_application',
            title=title,
            message=message,
        )
        db.session.add(notification)
    else:
        notification.title = title
        notification.message = message
        notification.is_read = False
        notification.read_at = None
        notification.created_at = datetime.utcnow()
    db.session.commit()
    try:
        send_fact_checker_application_email(
            applicant.email,
            applicant.username,
            approved=(decision == 'approve'),
            administrator_note=note,
        )
    except Exception as exc:
        current_app.logger.exception(
            'Fact-checker decision email failed for user %s (%s)',
            applicant.id,
            type(exc).__name__,
        )
        flash(
            'The decision and in-app notification were saved, but the email '
            'could not be sent. Check the mail configuration.',
            'warning',
        )
    return redirect(url_for('admin.index'))


@admin_bp.route('/reports')
@primary_admin_required
def reports():
    users = User.query.filter_by(role='user').order_by(User.created_at.desc()).all()
    fact_checkers = User.query.filter_by(
        role='fact_checker'
    ).order_by(User.created_at.desc()).all()
    sub_admins = User.query.filter(
        User.role == 'admin',
        db.func.lower(User.email)
        != current_app.config['PRIMARY_ADMIN_EMAIL'].casefold(),
    ).order_by(User.created_at.desc()).all()

    user_reports = []
    for user in users:
        latest_prediction = Prediction.query.filter_by(user_id=user.id).order_by(
            Prediction.created_at.desc()
        ).first()
        latest_case = EvidenceAudit.query.filter_by(user_id=user.id).order_by(
            EvidenceAudit.created_at.desc()
        ).first()
        last_activity_candidates = [user.created_at]
        if latest_prediction:
            last_activity_candidates.append(latest_prediction.created_at)
        if latest_case:
            last_activity_candidates.append(latest_case.created_at)
        user_reports.append({
            'user': user,
            'predictions': Prediction.query.filter_by(user_id=user.id).count(),
            'batches': BatchJob.query.filter_by(user_id=user.id).count(),
            'cases': EvidenceAudit.query.filter_by(user_id=user.id).count(),
            'reviewed_cases': (
                EvidenceAudit.query.join(FactCheckReview)
                .filter(EvidenceAudit.user_id == user.id).count()
            ),
            'last_activity': max(last_activity_candidates),
        })

    fact_checker_reports = []
    for checker in fact_checkers:
        decision_rows = db.session.query(
            FactCheckReview.decision, db.func.count(FactCheckReview.id)
        ).filter(FactCheckReview.reviewer_id == checker.id).group_by(
            FactCheckReview.decision
        ).all()
        decision_counts = dict(decision_rows)
        latest_review = FactCheckReview.query.filter_by(
            reviewer_id=checker.id
        ).order_by(FactCheckReview.updated_at.desc()).first()
        fact_checker_reports.append({
            'user': checker,
            'assigned_open': EvidenceAudit.query.filter(
                EvidenceAudit.assigned_to_id == checker.id,
                EvidenceAudit.review_status.in_(['pending', 'under_review']),
                EvidenceAudit.is_archived.is_(False),
            ).count(),
            'completed': FactCheckReview.query.filter_by(
                reviewer_id=checker.id
            ).count(),
            'confirmed_reliable': decision_counts.get('Confirmed Reliable', 0),
            'confirmed_misinformation': decision_counts.get(
                'Confirmed Misinformation', 0
            ),
            'confirmed_misleading': decision_counts.get('Confirmed Misleading', 0),
            'insufficient': decision_counts.get('Insufficient Evidence', 0),
            'latest_review': latest_review.updated_at if latest_review else None,
        })

    sub_admin_reports = []
    for admin in sub_admins:
        sub_admin_reports.append({
            'user': admin,
            'assignment_actions': EvidenceAssignmentEvent.query.filter(
                EvidenceAssignmentEvent.actor_id == admin.id,
                EvidenceAssignmentEvent.action.in_([
                    'assigned', 'reassigned', 'unassigned',
                ]),
            ).count(),
            'archive_actions': EvidenceAssignmentEvent.query.filter(
                EvidenceAssignmentEvent.actor_id == admin.id,
                EvidenceAssignmentEvent.action.in_(['archived', 'restored']),
            ).count(),
            'currently_assigned_by': EvidenceAudit.query.filter_by(
                assigned_by_id=admin.id
            ).count(),
        })

    return render_template(
        'admin/reports.html',
        user_reports=user_reports,
        fact_checker_reports=fact_checker_reports,
        sub_admin_reports=sub_admin_reports,
        totals={
            'users': len(user_reports),
            'fact_checkers': len(fact_checker_reports),
            'sub_admins': len(sub_admin_reports),
            'completed_reviews': FactCheckReview.query.count(),
        },
    )


def _spreadsheet_text(value):
    """Prevent user-supplied text from becoming an Excel formula."""
    text = str(value or '')
    return "'" + text if text.startswith(('=', '+', '-', '@')) else text


@admin_bp.route('/export-dataset')
@primary_admin_required
def export_dataset():
    """Download the retained prediction corpus without account PII."""
    rows = []
    predictions = Prediction.query.order_by(Prediction.created_at.asc()).all()
    for prediction in predictions:
        latest_audit = (
            prediction.evidence_audits[-1]
            if prediction.evidence_audits else None
        )
        review = latest_audit.fact_check_review if latest_audit else None
        rows.append({
            'Record ID': prediction.id,
            'Somali Text': _spreadsheet_text(prediction.text),
            'AI Prediction': prediction.prediction,
            'Confidence (%)': round(float(prediction.confidence or 0), 2),
            'Model': MODEL_NAMES.get(
                prediction.model_used, prediction.model_used
            ),
            'Created At (UTC)': (
                prediction.created_at.isoformat(sep=' ')
                if prediction.created_at else ''
            ),
            'Account Status': (
                'Active'
                if prediction.user and prediction.user.is_active else 'Inactive'
            ),
            'Evidence Case': f'FC-{latest_audit.id}' if latest_audit else '',
            'Evidence Status': latest_audit.status if latest_audit else 'not_run',
            'Evidence Sources': len(latest_audit.sources) if latest_audit else 0,
            'Human Decision': review.decision if review else '',
            'Human Review Date (UTC)': (
                review.updated_at.isoformat(sep=' ')
                if review and review.updated_at else ''
            ),
        })
    columns = [
        'Record ID', 'Somali Text', 'AI Prediction', 'Confidence (%)',
        'Model', 'Created At (UTC)', 'Account Status', 'Evidence Case',
        'Evidence Status', 'Evidence Sources', 'Human Decision',
        'Human Review Date (UTC)',
    ]
    output = io.BytesIO()
    pd.DataFrame(rows, columns=columns).to_excel(
        output, index=False, engine='openpyxl'
    )
    output.seek(0)
    filename = f'pmd_prediction_dataset_{datetime.utcnow():%Y%m%d}.xlsx'
    return send_file(
        output,
        as_attachment=True,
        download_name=filename,
        mimetype=(
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        ),
    )


@admin_bp.route('/audit/<int:audit_id>/delete', methods=['POST'])
@admin_required
def delete_audit(audit_id):
    EvidenceAudit.query.get_or_404(audit_id)
    flash('This action is unavailable. Archive the case to remove it from active queues.', 'warning')
    return redirect(_admin_queue_return_url())


@admin_bp.route('/create_user', methods=['POST'])
@admin_required
def create_user():
    username = request.form.get('username', '').strip()
    email = request.form.get('email', '').strip()
    password = request.form.get('password', '')
    role = request.form.get('role', 'user')
    if role not in {'user', 'fact_checker', 'admin'}:
        flash('Invalid user role.', 'danger')
        return redirect(url_for('admin.index'))
    if role == 'fact_checker':
        flash(
            'Fact-checkers must register, submit credentials, and be approved '
            'through the application queue.',
            'danger',
        )
        return redirect(url_for('admin.index'))
    if role != 'user' and not _is_primary_admin():
        flash('Only the primary admin can assign privileged roles.', 'danger')
        return redirect(url_for('admin.index'))
    if len(username) < 3 or len(password) < 6:
        flash('Username must be 3+ chars, password 6+ chars.', 'danger')
        return redirect(url_for('admin.index'))
    if User.query.filter_by(username=username).first():
        flash('Username already taken.', 'danger')
        return redirect(url_for('admin.index'))
    if User.query.filter_by(email=email).first():
        flash('Email already registered.', 'danger')
        return redirect(url_for('admin.index'))
    user = User(
        username=username,
        email=email,
        role=role,
        email_verified=True,
        fact_checker_application_status=(
            'approved' if role == 'fact_checker' else 'not_requested'
        ),
        fact_checker_reviewed_at=(
            datetime.utcnow() if role == 'fact_checker' else None
        ),
        fact_checker_reviewed_by_id=(
            current_user.id if role == 'fact_checker' else None
        ),
    )
    user.set_password(password)
    db.session.add(user)
    db.session.commit()
    flash(f'User {username} created.', 'success')
    return redirect(url_for('admin.index'))


@admin_bp.route('/edit_user/<int:user_id>', methods=['POST'])
@admin_required
def edit_user(user_id):
    user = User.query.get_or_404(user_id)
    username = request.form.get('username', '').strip()
    email = request.form.get('email', '').strip()
    new_password = request.form.get('password', '').strip()
    if len(username) < 3:
        flash('Username must be at least 3 characters.', 'danger')
        return redirect(url_for('admin.index'))
    existing = User.query.filter_by(username=username).first()
    if existing and existing.id != user.id:
        flash('Username already taken.', 'danger')
        return redirect(url_for('admin.index'))
    existing = User.query.filter_by(email=email).first()
    if existing and existing.id != user.id:
        flash('Email already registered.', 'danger')
        return redirect(url_for('admin.index'))
    user.username = username
    user.email = email
    if new_password:
        if len(new_password) < 6:
            flash('Password must be at least 6 characters.', 'danger')
            return redirect(url_for('admin.index'))
        user.set_password(new_password)
    db.session.commit()
    flash(f'User {username} updated.', 'success')
    return redirect(url_for('admin.index'))


@admin_bp.route('/set_user_status/<int:user_id>', methods=['POST'])
@admin_required
def set_user_status(user_id):
    user = User.query.get_or_404(user_id)
    requested_status = request.form.get('status', '').strip().lower()
    if not requested_status:
        requested_status = 'inactive' if user.is_active else 'active'
    if requested_status not in {'active', 'inactive'}:
        flash('Select Active or Inactive.', 'danger')
        return redirect(url_for('admin.index'))
    if _is_primary_user(user) or user.id == current_user.id:
        flash('The primary administrator cannot be made inactive.', 'danger')
        return redirect(url_for('admin.index'))
    if user.is_admin and not _is_primary_admin():
        flash('Only the primary administrator can change a sub-admin status.', 'danger')
        return redirect(url_for('admin.index'))
    user.is_active = requested_status == 'active'
    if not user.is_active:
        for audit in EvidenceAudit.query.filter(
            EvidenceAudit.assigned_to_id == user.id,
            EvidenceAudit.review_status != 'completed',
        ).all():
            audit.assigned_to_id = None
            audit.assigned_at = None
            audit.review_status = 'unassigned'
            _record_assignment_event(audit, 'unassigned_inactive_user', user.id, None)
    db.session.commit()
    state = 'Active' if user.is_active else 'Inactive'
    flash(f'{user.username} is now {state}.', 'success')
    return redirect(url_for('admin.index'))


@admin_bp.route('/set_role/<int:user_id>', methods=['POST'])
@admin_required
def set_role(user_id):
    user = User.query.get_or_404(user_id)
    if not _is_primary_admin():
        flash('Only the primary admin can change roles.', 'danger')
        return redirect(url_for('admin.index'))
    if _is_primary_user(user):
        flash('Cannot change the primary admin role.', 'danger')
        return redirect(url_for('admin.index'))
    new_role = request.form.get('role', '').strip()
    if new_role not in {'user', 'fact_checker', 'admin'}:
        flash('Invalid user role.', 'danger')
        return redirect(url_for('admin.index'))
    if (
        new_role == 'fact_checker'
        and user.fact_checker_application_status != 'approved'
    ):
        flash(
            'Approve this user\'s submitted fact-checker credentials before '
            'granting the Fact-Checker role.',
            'danger',
        )
        return redirect(url_for('admin.index'))
    if user.is_fact_checker and new_role != 'fact_checker':
        for audit in EvidenceAudit.query.filter(
            EvidenceAudit.assigned_to_id == user.id,
            EvidenceAudit.review_status != 'completed',
        ).all():
            audit.assigned_to_id = None
            audit.assigned_at = None
            audit.review_status = 'unassigned'
            _record_assignment_event(audit, 'unassigned_role_change', user.id, None)
    user.role = new_role
    if new_role == 'fact_checker':
        user.fact_checker_application_status = 'approved'
        user.fact_checker_reviewed_at = datetime.utcnow()
        user.fact_checker_reviewed_by_id = current_user.id
    elif user.fact_checker_application_status == 'approved':
        user.fact_checker_application_status = 'revoked'
    db.session.commit()
    flash(f'User {user.username} role set to {user.role}.', 'success')
    return redirect(url_for('admin.index'))


@admin_bp.route('/delete_prediction/<int:pred_id>', methods=['POST'])
@admin_required
def delete_prediction(pred_id):
    Prediction.query.get_or_404(pred_id)
    flash('This action is unavailable.', 'warning')
    return redirect(url_for('admin.index'))
