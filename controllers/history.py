import json
import io

import pandas as pd
from flask import Blueprint, render_template, request, redirect, url_for, flash, send_file
from flask_login import login_required, current_user
from sqlalchemy import or_

from models import db, Prediction, EvidenceAudit, EvidenceSource
from models.evidence import FACT_CHECK_REQUIRED_LABELS
from services.ml_service import MODEL_NAMES
from services.time_service import format_somalia_time


history_bp = Blueprint('history', __name__)


def _case_tracking_state(audit):
    """Return a user-facing workflow state derived from persisted case data."""
    review = audit.fact_check_review
    if audit.is_archived:
        return {
            'key': 'archived', 'label': 'Archived', 'progress': 40,
            'icon': 'fa-box-archive',
            'message': 'This investigation is paused and preserved in the audit trail.',
        }
    if audit.status == 'failed':
        return {
            'key': 'attention', 'label': 'Evidence Audit Failed', 'progress': 25,
            'icon': 'fa-triangle-exclamation',
            'message': (
                'The AI prediction is saved, but evidence retrieval did not finish. '
                'You can retry this audit without running the prediction again.'
            ),
        }
    if not audit.requires_fact_check:
        if audit.status == 'running':
            return {
                'key': 'evidence', 'label': 'Collecting Evidence', 'progress': 60,
                'icon': 'fa-magnifying-glass',
                'message': 'The Reliable prediction is saved while related public sources are collected.',
            }
        return {
            'key': 'completed', 'label': 'Completed — No Review Required',
            'progress': 100, 'icon': 'fa-circle-check',
            'message': (
                'The AI prediction is Reliable. Under the current workflow, only '
                'Misinformation and Misleading cases are sent to fact-checkers.'
            ),
        }
    if review or audit.review_status == 'completed':
        return {
            'key': 'completed', 'label': 'Decision Published', 'progress': 100,
            'icon': 'fa-circle-check',
            'message': 'The fact-checker has published the final human decision.',
        }
    if audit.review_status == 'under_review':
        return {
            'key': 'under_review', 'label': 'Under Review', 'progress': 80,
            'icon': 'fa-user-check',
            'message': 'The assigned fact-checker is reviewing the claim and evidence.',
        }
    if audit.review_status == 'pending' or audit.assigned_to_id:
        return {
            'key': 'assigned', 'label': 'Fact-Checker Assigned', 'progress': 65,
            'icon': 'fa-user-clock',
            'message': 'A fact-checker has been assigned and the review is waiting to begin.',
        }
    if audit.status == 'running':
        return {
            'key': 'evidence', 'label': 'Collecting Evidence', 'progress': 25,
            'icon': 'fa-magnifying-glass',
            'message': 'The system is collecting and comparing related public sources.',
        }
    return {
        'key': 'queued', 'label': 'Awaiting Assignment', 'progress': 45,
        'icon': 'fa-list-check',
        'message': 'The evidence audit is ready and waiting for fact-checker assignment.',
    }


def _case_timeline(audit):
    """Build the five visible tracking stages for one evidence case."""
    review = audit.fact_check_review
    evidence_done = audit.status == 'complete'
    if not audit.requires_fact_check:
        workflow_done = audit.status == 'complete'
        return [
            {
                'title': 'AI Prediction', 'description': 'Reliable classification saved',
                'time': audit.created_at, 'state': 'done',
                'icon': 'fa-wand-magic-sparkles',
            },
            {
                'title': 'Evidence Audit',
                'description': 'Related public sources collected' if evidence_done else (
                    'Evidence retrieval was incomplete' if audit.status == 'failed'
                    else 'Collecting related public sources'
                ),
                'time': audit.completed_at,
                'state': 'done' if evidence_done else (
                    'error' if audit.status == 'failed' else 'current'
                ),
                'icon': 'fa-magnifying-glass-chart',
            },
            {
                'title': 'Case Completed',
                'description': 'Fact-checker review is not required for Reliable predictions',
                'time': audit.completed_at if workflow_done else None,
                'state': 'done' if workflow_done else 'upcoming',
                'icon': 'fa-circle-check',
            },
        ]
    assigned = audit.assigned_to_id is not None or audit.review_status in (
        'pending', 'under_review', 'completed'
    )
    reviewing = audit.review_status in ('under_review', 'completed')
    completed = review is not None or audit.review_status == 'completed'
    stages = [
        {
            'title': 'AI Prediction', 'description': 'Classification saved',
            'time': audit.created_at, 'state': 'done', 'icon': 'fa-wand-magic-sparkles',
        },
        {
            'title': 'Evidence Audit',
            'description': 'Related public sources collected' if evidence_done else (
                'Evidence retrieval was incomplete' if audit.status == 'failed'
                else 'Collecting related public sources'
            ),
            'time': audit.completed_at if evidence_done else None,
            'state': 'done' if evidence_done else ('error' if audit.status == 'failed' else 'current'),
            'icon': 'fa-magnifying-glass-chart',
        },
        {
            'title': 'Review Queue',
            'description': 'Fact-checker assigned' if assigned else 'Waiting for assignment',
            'time': audit.assigned_at if assigned else None,
            'state': 'done' if assigned else ('current' if evidence_done else 'upcoming'),
            'icon': 'fa-list-check',
        },
        {
            'title': 'Human Review',
            'description': 'Human review completed' if completed else (
                'Evidence is being reviewed' if reviewing else 'Not started'
            ),
            'time': review.updated_at if completed else (audit.assigned_at if reviewing else None),
            'state': 'done' if completed else ('current' if reviewing else 'upcoming'),
            'icon': 'fa-user-check',
        },
        {
            'title': 'Final Decision',
            'description': review.decision if review else 'Not published',
            'time': review.updated_at if review else None,
            'state': 'done' if completed else 'upcoming',
            'icon': 'fa-circle-check',
        },
    ]
    if audit.is_archived:
        for stage in stages:
            if stage['state'] == 'current':
                stage['state'] = 'paused'
    return stages


def _case_last_updated(audit):
    timestamps = [audit.created_at, audit.completed_at, audit.assigned_at, audit.archived_at]
    if audit.fact_check_review:
        timestamps.append(audit.fact_check_review.updated_at)
    timestamps.extend(event.created_at for event in audit.assignment_events)
    return max(value for value in timestamps if value is not None)


@history_bp.route('/history')
@login_required
def index():
    uid = current_user.id
    page = request.args.get('page', 1, type=int)
    model_filter = request.args.get('model', '')
    label_filter = request.args.get('label', '')
    search = request.args.get('search', '')

    query = Prediction.query.filter_by(user_id=uid)
    if model_filter:
        query = query.filter(Prediction.model_used == model_filter)
    if label_filter:
        query = query.filter(Prediction.prediction == label_filter)
    if search:
        query = query.filter(Prediction.text.contains(search))

    predictions = query.order_by(Prediction.created_at.desc()).paginate(page=page, per_page=15, error_out=False)
    total = query.count()
    return render_template('history/index.html', predictions=predictions, total=total,
                           page=page, model_filter=model_filter, label_filter=label_filter,
                           search=search, MODEL_NAMES=MODEL_NAMES)


@history_bp.route('/my-cases')
@login_required
def cases():
    page = request.args.get('page', 1, type=int)
    status_filter = request.args.get('status', 'all')
    label_filter = request.args.get('label', '')
    search = request.args.get('search', '').strip()
    valid_statuses = {
        'all', 'queued', 'assigned', 'under_review', 'completed', 'attention'
    }
    if status_filter not in valid_statuses:
        status_filter = 'all'

    base_query = EvidenceAudit.query.filter_by(user_id=current_user.id)
    query = base_query
    if status_filter == 'queued':
        query = query.filter(
            EvidenceAudit.status == 'complete',
            EvidenceAudit.review_status == 'unassigned',
            EvidenceAudit.predicted_label.in_(FACT_CHECK_REQUIRED_LABELS),
            EvidenceAudit.is_archived.is_(False),
        )
    elif status_filter == 'assigned':
        query = query.filter(
            EvidenceAudit.review_status == 'pending',
            EvidenceAudit.predicted_label.in_(FACT_CHECK_REQUIRED_LABELS),
            EvidenceAudit.is_archived.is_(False),
        )
    elif status_filter == 'under_review':
        query = query.filter(
            EvidenceAudit.review_status == 'under_review',
            EvidenceAudit.predicted_label.in_(FACT_CHECK_REQUIRED_LABELS),
            EvidenceAudit.is_archived.is_(False),
        )
    elif status_filter == 'completed':
        query = query.filter(
            EvidenceAudit.status == 'complete',
            EvidenceAudit.review_status.in_(['completed', 'not_required'])
        )
    elif status_filter == 'attention':
        query = query.filter(
            or_(EvidenceAudit.status == 'failed', EvidenceAudit.is_archived.is_(True))
        )

    if label_filter:
        query = query.filter(EvidenceAudit.predicted_label == label_filter)
    if search:
        query = query.filter(EvidenceAudit.original_text.contains(search))

    audits = query.order_by(EvidenceAudit.created_at.desc()).paginate(
        page=page, per_page=9, error_out=False
    )
    case_views = {
        audit.id: {
            'state': _case_tracking_state(audit),
            'last_updated': _case_last_updated(audit),
        }
        for audit in audits.items
    }
    summary = {
        'total': base_query.count(),
        'completed': base_query.filter(
            EvidenceAudit.status == 'complete',
            EvidenceAudit.review_status.in_(['completed', 'not_required'])
        ).count(),
        'in_review': base_query.filter(
            EvidenceAudit.review_status.in_(['pending', 'under_review']),
            EvidenceAudit.predicted_label.in_(FACT_CHECK_REQUIRED_LABELS),
            EvidenceAudit.is_archived.is_(False),
        ).count(),
        'queued': base_query.filter(
            EvidenceAudit.status == 'complete',
            EvidenceAudit.review_status == 'unassigned',
            EvidenceAudit.predicted_label.in_(FACT_CHECK_REQUIRED_LABELS),
            EvidenceAudit.is_archived.is_(False),
        ).count(),
    }
    return render_template(
        'history/cases.html', audits=audits, case_views=case_views,
        summary=summary, status_filter=status_filter,
        label_filter=label_filter, search=search,
    )


@history_bp.route('/history/evidence/<int:audit_id>')
@login_required
def evidence_detail(audit_id):
    audit = EvidenceAudit.query.filter_by(
        id=audit_id, user_id=current_user.id
    ).first_or_404()
    sources = EvidenceSource.query.filter_by(audit_id=audit.id).order_by(
        EvidenceSource.similarity_score.desc()
    ).all()
    queries = json.loads(audit.queries_json or '[]')
    return render_template(
        'history/evidence_detail.html', audit=audit, sources=sources, queries=queries,
        tracking_state=_case_tracking_state(audit),
        timeline=_case_timeline(audit),
    )


@history_bp.route('/history/delete/<int:pred_id>', methods=['POST'])
@login_required
def delete_prediction(pred_id):
    pred = Prediction.query.get_or_404(pred_id)
    if pred.user_id != current_user.id:
        flash('Unauthorized.', 'danger')
        return redirect(url_for('history.index'))
    flash('This action is unavailable.', 'warning')
    return redirect(url_for('history.index', page=request.args.get('page', 1),
                       model=request.args.get('model', ''), label=request.args.get('label', ''),
                       search=request.args.get('search', '')))


@history_bp.route('/history/delete-selected', methods=['POST'])
@login_required
def delete_selected():
    flash('This action is unavailable.', 'warning')
    return redirect(url_for('history.index', page=request.args.get('page', 1),
                       model=request.args.get('model', ''), label=request.args.get('label', ''),
                       search=request.args.get('search', '')))


@history_bp.route('/history/delete-all', methods=['POST'])
@login_required
def delete_all():
    flash('This action is unavailable.', 'warning')
    return redirect(url_for('history.index'))


@history_bp.route('/history/export')
@login_required
def export():
    predictions = Prediction.query.filter_by(user_id=current_user.id).order_by(Prediction.created_at.desc()).all()
    data = [{
        'ID': p.id, 'Text': p.text, 'Prediction': p.prediction,
        'Confidence': f'{p.confidence:.2f}%', 'Model': MODEL_NAMES.get(p.model_used, p.model_used),
        'Date': format_somalia_time(p.created_at),
    } for p in predictions]
    df = pd.DataFrame(data) if data else pd.DataFrame(columns=['ID', 'Text', 'Prediction', 'Confidence', 'Model', 'Date'])
    output = io.BytesIO()
    df.to_excel(output, index=False, engine='openpyxl')
    output.seek(0)
    return send_file(output, as_attachment=True, download_name='history_export.xlsx',
                     mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
