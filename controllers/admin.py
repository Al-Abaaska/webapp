import json
from datetime import datetime
from functools import wraps

from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user

from models import (
    db, User, Prediction, BatchJob, EvidenceAudit, EvidenceSource,
    FactCheckReview, EvidenceAssignmentEvent, ClaimRecord,
)
from services.ml_service import MODEL_NAMES


admin_bp = Blueprint('admin', __name__, url_prefix='/admin')


def admin_required(f):
    @wraps(f)
    @login_required
    def decorated(*args, **kwargs):
        if not current_user.is_admin:
            flash('Admin access required.', 'danger')
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


@admin_bp.route('/')
@admin_required
def index():
    from datetime import timedelta
    from collections import Counter
    from sqlalchemy import case

    users = User.query.all()
    pred_page = request.args.get('pred_page', 1, type=int)
    admin_predictions = Prediction.query.order_by(Prediction.created_at.desc()).paginate(page=pred_page, per_page=15, error_out=False)

    # --- System Stats ---
    total_users = len(users)
    fact_checker_count = sum(1 for u in users if u.role == 'fact_checker')
    fact_checkers = [u for u in users if u.role == 'fact_checker' and not u.is_blocked]
    admin_count = sum(1 for u in users if u.role == 'admin')
    total_predictions = Prediction.query.count()
    total_batches = BatchJob.query.count()
    total_evidence_audits = EvidenceAudit.query.count()

    # --- Review Progress ---
    complete_audits = EvidenceAudit.query.filter(EvidenceAudit.status == 'complete')
    pending_reviews = complete_audits.outerjoin(FactCheckReview).filter(FactCheckReview.id.is_(None)).count()
    completed_reviews = complete_audits.join(FactCheckReview).count()
    failed_audits = EvidenceAudit.query.filter(EvidenceAudit.status == 'failed').count()
    insufficient_evidence = (
        complete_audits.join(FactCheckReview)
        .filter(FactCheckReview.decision == 'Insufficient Evidence')
        .count()
    )
    unassigned_reviews = complete_audits.filter(
        EvidenceAudit.assigned_to_id.is_(None),
        EvidenceAudit.review_status == 'unassigned',
    ).count()
    assigned_open_reviews = complete_audits.filter(
        EvidenceAudit.assigned_to_id.isnot(None),
        EvidenceAudit.review_status.in_(['pending', 'under_review']),
    ).count()
    assignment_queue = (
        complete_audits
        .outerjoin(
            ClaimRecord,
            ClaimRecord.investigation_audit_id == EvidenceAudit.id,
        )
        .filter(EvidenceAudit.review_status != 'completed')
        .order_by(
            case(
                (EvidenceAudit.predicted_label == 'Misinformation', 0),
                (EvidenceAudit.predicted_label == 'Misleading', 1),
                else_=2,
            ),
            db.func.coalesce(ClaimRecord.submission_count, 1).desc(),
            EvidenceAudit.created_at.desc(),
        )
        .limit(20)
        .all()
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

    # --- High-Risk Activity ---
    high_risk = (
        EvidenceAudit.query
        .filter(EvidenceAudit.predicted_label.in_(['Misinformation', 'Misleading']))
        .order_by(EvidenceAudit.created_at.desc())
        .limit(5)
        .all()
    )
    high_risk_data = []
    for audit in high_risk:
        sources = EvidenceSource.query.filter_by(audit_id=audit.id).all()
        conf = audit.prediction_record.confidence if audit.prediction_record else 0
        high_risk_data.append({
            'audit': audit,
            'confidence': conf,
            'source_count': len(sources),
        })

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
        total_decisions=total_decisions, high_risk_data=high_risk_data,
        recent_users=recent_users, recent_predictions=recent_predictions,
        recent_audits=recent_audits, recent_predictions_list=recent_predictions_list,
        fact_checkers=fact_checkers, assignment_queue=assignment_queue,
        unassigned_reviews=unassigned_reviews,
        assigned_open_reviews=assigned_open_reviews,
        MODEL_NAMES=MODEL_NAMES)


@admin_bp.route('/audit/<int:audit_id>/assign', methods=['POST'])
@admin_required
def assign_audit(audit_id):
    audit = EvidenceAudit.query.get_or_404(audit_id)
    assignee_id = request.form.get('assignee_id', type=int)
    assignee = db.session.get(User, assignee_id) if assignee_id else None
    if audit.status != 'complete' or audit.review_status == 'completed':
        flash('Only open, completed evidence audits can be assigned.', 'warning')
        return redirect(url_for('admin.index'))
    if not assignee or not assignee.is_fact_checker or assignee.is_blocked:
        flash('Select an active fact-checker.', 'danger')
        return redirect(url_for('admin.index'))
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
    return redirect(url_for('admin.index'))


@admin_bp.route('/audit/<int:audit_id>/unassign', methods=['POST'])
@admin_required
def unassign_audit(audit_id):
    audit = EvidenceAudit.query.get_or_404(audit_id)
    if audit.review_status == 'completed':
        flash('A completed case cannot be returned to the queue.', 'warning')
        return redirect(url_for('admin.index'))
    previous_id = audit.assigned_to_id
    audit.assigned_to_id = None
    audit.assigned_by_id = current_user.id
    audit.assigned_at = None
    audit.review_status = 'unassigned'
    _record_assignment_event(audit, 'unassigned', previous_id, None)
    db.session.commit()
    flash(f'Case #{audit.id} returned to the unassigned queue.', 'success')
    return redirect(url_for('admin.index'))


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
    if role != 'user' and current_user.email != 'admin@somali.com':
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
    user = User(username=username, email=email, role=role, email_verified=True)
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


@admin_bp.route('/block_user/<int:user_id>', methods=['POST'])
@admin_required
def block_user(user_id):
    user = User.query.get_or_404(user_id)
    if user.is_admin:
        flash('Cannot block admin.', 'danger')
        return redirect(url_for('admin.index'))
    user.is_blocked = not user.is_blocked
    if user.is_blocked:
        for audit in EvidenceAudit.query.filter(
            EvidenceAudit.assigned_to_id == user.id,
            EvidenceAudit.review_status != 'completed',
        ).all():
            audit.assigned_to_id = None
            audit.assigned_at = None
            audit.review_status = 'unassigned'
            _record_assignment_event(audit, 'unassigned_blocked_user', user.id, None)
    db.session.commit()
    state = 'blocked' if user.is_blocked else 'unblocked'
    flash(f'User {user.username} {state}.', 'success')
    return redirect(url_for('admin.index'))


@admin_bp.route('/set_role/<int:user_id>', methods=['POST'])
@admin_required
def set_role(user_id):
    user = User.query.get_or_404(user_id)
    if current_user.email != 'admin@somali.com':
        flash('Only the primary admin can change roles.', 'danger')
        return redirect(url_for('admin.index'))
    if user.email == 'admin@somali.com':
        flash('Cannot change the primary admin role.', 'danger')
        return redirect(url_for('admin.index'))
    new_role = request.form.get('role', '').strip()
    if new_role not in {'user', 'fact_checker', 'admin'}:
        flash('Invalid user role.', 'danger')
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
    db.session.commit()
    flash(f'User {user.username} role set to {user.role}.', 'success')
    return redirect(url_for('admin.index'))


@admin_bp.route('/delete_user/<int:user_id>', methods=['POST'])
@admin_required
def delete_user(user_id):
    user = User.query.get_or_404(user_id)
    if user.is_admin:
        flash('Cannot delete admin.', 'danger')
        return redirect(url_for('admin.index'))
    FactCheckReview.query.filter_by(reviewer_id=user.id).update(
        {'reviewer_id': None}, synchronize_session=False
    )
    EvidenceAudit.query.filter_by(assigned_to_id=user.id).update({
        'assigned_to_id': None,
        'assigned_at': None,
        'review_status': 'unassigned',
    }, synchronize_session=False)
    EvidenceAudit.query.filter_by(assigned_by_id=user.id).update(
        {'assigned_by_id': None}, synchronize_session=False
    )
    for audit in EvidenceAudit.query.filter_by(user_id=user.id).all():
        db.session.delete(audit)
    Prediction.query.filter_by(user_id=user.id).delete()
    BatchJob.query.filter_by(user_id=user.id).delete()
    db.session.delete(user)
    db.session.commit()
    flash(f'User {user.username} deleted.', 'success')
    return redirect(url_for('admin.index'))


@admin_bp.route('/delete_prediction/<int:pred_id>', methods=['POST'])
@admin_required
def delete_prediction(pred_id):
    pred = Prediction.query.get_or_404(pred_id)
    db.session.delete(pred)
    db.session.commit()
    flash('Prediction deleted.', 'success')
    return redirect(url_for('admin.index'))
