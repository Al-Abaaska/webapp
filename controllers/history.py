import json
import io

import pandas as pd
from flask import Blueprint, render_template, request, redirect, url_for, flash, send_file
from flask_login import login_required, current_user

from models import db, Prediction, EvidenceAudit, EvidenceSource
from services.ml_service import MODEL_NAMES


history_bp = Blueprint('history', __name__)


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
        'history/evidence_detail.html', audit=audit, sources=sources, queries=queries
    )


@history_bp.route('/history/delete/<int:pred_id>', methods=['POST'])
@login_required
def delete_prediction(pred_id):
    pred = Prediction.query.get_or_404(pred_id)
    if pred.user_id != current_user.id:
        flash('Unauthorized.', 'danger')
        return redirect(url_for('history.index'))
    db.session.delete(pred)
    db.session.commit()
    flash('Prediction deleted.', 'success')
    return redirect(url_for('history.index', page=request.args.get('page', 1),
                       model=request.args.get('model', ''), label=request.args.get('label', ''),
                       search=request.args.get('search', '')))


@history_bp.route('/history/delete-selected', methods=['POST'])
@login_required
def delete_selected():
    ids = request.form.getlist('selected_ids')
    if not ids:
        flash('No predictions selected.', 'warning')
        return redirect(url_for('history.index'))
    Prediction.query.filter(Prediction.id.in_(ids), Prediction.user_id == current_user.id).delete(synchronize_session=False)
    db.session.commit()
    flash(f'{len(ids)} prediction(s) deleted.', 'success')
    return redirect(url_for('history.index', page=request.args.get('page', 1),
                       model=request.args.get('model', ''), label=request.args.get('label', ''),
                       search=request.args.get('search', '')))


@history_bp.route('/history/delete-all', methods=['POST'])
@login_required
def delete_all():
    count = Prediction.query.filter_by(user_id=current_user.id).delete()
    db.session.commit()
    flash(f'All {count} predictions deleted.', 'success')
    return redirect(url_for('history.index'))


@history_bp.route('/history/export')
@login_required
def export():
    predictions = Prediction.query.filter_by(user_id=current_user.id).order_by(Prediction.created_at.desc()).all()
    data = [{
        'ID': p.id, 'Text': p.text, 'Prediction': p.prediction,
        'Confidence': f'{p.confidence:.2f}%', 'Model': MODEL_NAMES.get(p.model_used, p.model_used),
        'Date': p.created_at.strftime('%Y-%m-%d %H:%M'),
    } for p in predictions]
    df = pd.DataFrame(data) if data else pd.DataFrame(columns=['ID', 'Text', 'Prediction', 'Confidence', 'Model', 'Date'])
    output = io.BytesIO()
    df.to_excel(output, index=False, engine='openpyxl')
    output.seek(0)
    return send_file(output, as_attachment=True, download_name='history_export.xlsx',
                     mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
