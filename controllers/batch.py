import json
import os
import threading
import uuid
from datetime import datetime

from flask import (
    Blueprint, current_app, flash, jsonify, redirect, render_template, request,
    send_file, url_for,
)
from flask_login import current_user, login_required
from werkzeug.utils import secure_filename

from models import (
    db, BatchItem, BatchJob, ClaimRecord, EvidenceAudit, EvidenceSource, Prediction,
)
from models.evidence import FACT_CHECK_REQUIRED_LABELS
from services.batch_service import (
    ALLOWED_EXTENSIONS, build_summary, clean_cell, detect_columns,
    preview_rows, read_batch_file, risk_label, validate_frame,
)
from services.claim_service import (
    claim_fingerprint, normalize_claim,
)
from services.gemini_service import is_political
from services.ensemble_service import run_ensemble
from services.ml_service import MLService
from services.preprocessing import is_somali
from services.evidence_storage import (
    build_evidence_source, normalize_evidence_item,
)


batch_bp = Blueprint('batch', __name__)


def _owned_job(job_id):
    job = BatchJob.query.get_or_404(job_id)
    if job.user_id != current_user.id and not current_user.is_admin:
        return None
    return job


def _job_json(value):
    try:
        return json.loads(value or '{}')
    except (TypeError, ValueError):
        return {}


def _safe_upload_file(path):
    """Return a verified file path only when it is inside UPLOAD_FOLDER."""
    if not path:
        return None
    upload_root = os.path.realpath(current_app.config['UPLOAD_FOLDER'])
    resolved = os.path.realpath(path)
    try:
        inside_uploads = os.path.commonpath([upload_root, resolved]) == upload_root
    except ValueError:
        inside_uploads = False
    return resolved if inside_uploads and os.path.isfile(resolved) else None


def _available_model(ml, model_key):
    return model_key == 'ensemble' or model_key in ml.available_models


def _predict_text(ml, text, model_key):
    if model_key == 'ensemble':
        result = run_ensemble(ml, text)
        if not result:
            return None, 0
        return result['prediction'], result['average_confidence']
    prediction, probabilities = ml.predict(text, model_key)
    if prediction is None:
        return None, 0
    result = ml.build_result(prediction, probabilities, model_key)
    return result['prediction'], result['confidence']


@batch_bp.route('/batch')
@login_required
def index():
    ml = MLService()
    recent_jobs = BatchJob.query.filter_by(user_id=current_user.id).order_by(
        BatchJob.created_at.desc()
    ).limit(8).all()
    return render_template(
        'batch/index.html', models=ml.get_model_list(), recent_jobs=recent_jobs
    )


@batch_bp.route('/batch/preview', methods=['POST'])
@login_required
def preview():
    uploaded = request.files.get('file')
    if not uploaded or not uploaded.filename:
        return jsonify({'error': 'Choose a CSV or Excel file.'}), 400
    extension = os.path.splitext(uploaded.filename)[1].casefold()
    if extension not in ALLOWED_EXTENSIONS:
        return jsonify({'error': 'Only .csv, .xlsx, and .xls files are supported.'}), 400

    safe_name = secure_filename(uploaded.filename) or f'batch{extension}'
    stored_name = f'batch_{uuid.uuid4().hex}_{safe_name}'
    input_path = os.path.join(current_app.config['UPLOAD_FOLDER'], stored_name)
    uploaded.save(input_path)
    try:
        frame = read_batch_file(input_path)
    except Exception as exc:
        try:
            os.remove(input_path)
        except OSError:
            pass
        return jsonify({'error': f'Could not read this file: {exc}'}), 400
    if not len(frame.columns):
        return jsonify({'error': 'The uploaded file has no columns.'}), 400

    detected = detect_columns(frame.columns.tolist())
    validation = validate_frame(frame, detected.get('text'))
    job = BatchJob(
        user_id=current_user.id,
        filename=uploaded.filename,
        input_path=input_path,
        total_rows=len(frame),
        status='previewed',
        text_column=detected.get('text') or '',
        source_url_column=detected.get('source_url') or '',
        source_name_column=detected.get('source_name') or '',
        date_column=detected.get('date') or '',
        validation_json=json.dumps(validation),
    )
    db.session.add(job)
    db.session.commit()
    return jsonify({
        'job_id': job.id,
        'filename': job.filename,
        'columns': [str(column) for column in frame.columns],
        'detected': detected,
        'validation': validation,
        'preview': preview_rows(frame),
    })


@batch_bp.route('/batch/validate/<int:job_id>', methods=['POST'])
@login_required
def validate(job_id):
    job = _owned_job(job_id)
    if job is None:
        return jsonify({'error': 'Unauthorized'}), 403
    data = request.get_json() or {}
    try:
        frame = read_batch_file(job.input_path)
    except Exception as exc:
        return jsonify({'error': f'Could not read the uploaded file: {exc}'}), 400
    text_column = clean_cell(data.get('text_column'))
    if text_column not in frame.columns:
        return jsonify({'error': 'Select the column containing Somali text.'}), 400
    job.text_column = text_column
    validation = validate_frame(frame, text_column)
    job.validation_json = json.dumps(validation)
    db.session.commit()
    return jsonify({'validation': validation, 'preview': preview_rows(frame)})


@batch_bp.route('/batch/start/<int:job_id>', methods=['POST'])
@login_required
def start(job_id):
    job = _owned_job(job_id)
    if job is None:
        return jsonify({'error': 'Unauthorized'}), 403
    if job.status not in {'previewed', 'error'}:
        return jsonify({'error': 'This batch has already been started.'}), 400
    data = request.get_json() or {}
    model_key = clean_cell(data.get('model')) or 'svm'
    ml = MLService()
    if not _available_model(ml, model_key):
        return jsonify({'error': 'The selected model is unavailable.'}), 400

    try:
        frame = read_batch_file(job.input_path)
    except Exception as exc:
        return jsonify({'error': f'Could not read the uploaded file: {exc}'}), 400
    text_column = clean_cell(data.get('text_column'))
    if text_column not in frame.columns:
        return jsonify({'error': 'Select a valid Somali text column.'}), 400
    job.text_column = text_column
    job.model_used = model_key
    job.duplicate_detection = False
    job.high_risk_only = False
    job.validation_json = json.dumps(validate_frame(frame, text_column))
    job.status = 'queued'
    job.processed_rows = 0
    job.error_message = ''
    BatchItem.query.filter_by(job_id=job.id).delete()
    db.session.commit()

    thread = threading.Thread(target=_process_batch, args=(job.id,), daemon=True)
    thread.start()
    return jsonify({
        'job_id': job.id,
        'total_rows': job.total_rows,
        'results_url': url_for('batch.results', job_id=job.id),
    })


def _process_batch(job_id):
    from app import app as flask_app
    with flask_app.app_context():
        job = db.session.get(BatchJob, job_id)
        if not job:
            return
        job.status = 'processing'
        db.session.commit()
        try:
            frame = read_batch_file(job.input_path)
            ml = MLService()
            prediction_cache = {}
            political_scope_cache = {}

            for position, (_, row) in enumerate(frame.iterrows()):
                text_value = clean_cell(row.get(job.text_column))
                source_url = clean_cell(row.get(job.source_url_column)) if job.source_url_column else ''
                source_name = clean_cell(row.get(job.source_name_column)) if job.source_name_column else ''
                source_date = clean_cell(row.get(job.date_column)) if job.date_column else ''
                label, confidence, error = 'Pending', 0, ''
                prediction_record = None

                if not text_value or len(text_value) < 5:
                    label, error = 'Invalid', 'Empty or too short to analyze.'
                elif not is_somali(text_value):
                    label, error = 'Not Somali', 'Somali language validation failed.'
                else:
                    fingerprint = claim_fingerprint(text_value)
                    if fingerprint in political_scope_cache:
                        political_scope = political_scope_cache[fingerprint]
                    else:
                        political_scope = is_political(text_value)
                        political_scope_cache[fingerprint] = political_scope

                    if political_scope is None:
                        label = 'Scope Unavailable'
                        error = 'Political-scope validation was unavailable.'
                    elif political_scope is False:
                        label = 'Not Political'
                        error = 'This row is outside the system political scope.'
                    elif fingerprint in prediction_cache:
                        label, confidence = prediction_cache[fingerprint]
                    else:
                        try:
                            label, confidence = _predict_text(ml, text_value, job.model_used)
                            if label is None:
                                label, error = 'Error', 'Model did not return a prediction.'
                            else:
                                prediction_cache[fingerprint] = (label, confidence)
                        except Exception as exc:
                            label, error = 'Error', str(exc)[:500]

                    if label in {'Reliable', 'Misinformation', 'Misleading'}:
                        prediction_record = Prediction(
                            user_id=job.user_id,
                            text=text_value,
                            prediction=label,
                            confidence=confidence,
                            model_used=job.model_used,
                        )
                        db.session.add(prediction_record)
                        db.session.flush()

                item = BatchItem(
                    job_id=job.id,
                    row_number=position + 2,
                    text=text_value,
                    source_url=source_url,
                    source_name=source_name,
                    source_date=source_date,
                    prediction=label,
                    confidence=confidence,
                    risk=risk_label(label, confidence),
                    previous_match='None',
                    match_similarity=0,
                    matched_audit_id=None,
                    prediction_id=prediction_record.id if prediction_record else None,
                    error_message=error,
                )
                db.session.add(item)
                job.processed_rows = position + 1
                if job.processed_rows % 10 == 0:
                    db.session.commit()
            db.session.commit()

            items = BatchItem.query.filter_by(job_id=job.id).order_by(BatchItem.row_number).all()
            # Register exact claims for the user's claim history without
            # clustering or comparing rows inside the batch workspace.
            exact_groups = {}
            for item in items:
                if item.prediction not in {'Reliable', 'Misinformation', 'Misleading'}:
                    continue
                exact_groups.setdefault(claim_fingerprint(item.text), []).append(item)
            now = datetime.utcnow()
            for fingerprint, members in exact_groups.items():
                record = ClaimRecord.query.filter_by(fingerprint=fingerprint).first()
                if record is None:
                    first = members[0]
                    db.session.add(ClaimRecord(
                        fingerprint=fingerprint,
                        normalized_text=normalize_claim(first.text),
                        original_text=first.text,
                        first_prediction_id=first.prediction_id,
                        submission_count=len(members),
                        first_submitted_at=now,
                        last_submitted_at=now,
                    ))
                else:
                    record.submission_count += len(members)
                    record.last_submitted_at = now

            summary = build_summary(items)
            job.summary_json = json.dumps(summary)
            output_frame = frame.copy()
            output_frame['Prediction'] = [item.prediction for item in items]
            output_frame['Confidence'] = [item.confidence for item in items]
            output_frame['Risk'] = [item.risk for item in items]
            result_name = f'batch_results_{job.id}_{uuid.uuid4().hex[:8]}.xlsx'
            result_path = os.path.join(flask_app.config['UPLOAD_FOLDER'], result_name)
            output_frame.to_excel(result_path, index=False, engine='openpyxl')
            job.result_path = result_path
            job.status = 'completed'
            job.processed_rows = len(items)
            db.session.commit()
        except Exception as exc:
            db.session.rollback()
            job = db.session.get(BatchJob, job_id)
            if job:
                job.status = 'error'
                job.error_message = str(exc)[:1000]
                db.session.commit()


@batch_bp.route('/batch/status/<int:job_id>')
@login_required
def status(job_id):
    job = _owned_job(job_id)
    if job is None:
        return jsonify({'error': 'Unauthorized'}), 403
    return jsonify({
        'status': job.status,
        'total': job.total_rows,
        'processed': job.processed_rows,
        'progress': round(job.processed_rows / job.total_rows * 100) if job.total_rows else 0,
        'error': job.error_message,
        'results_url': url_for('batch.results', job_id=job.id),
    })


@batch_bp.route('/batch/job/<int:job_id>')
@login_required
def results(job_id):
    job = _owned_job(job_id)
    if job is None:
        return jsonify({'error': 'Unauthorized'}), 403
    if job.status not in {'completed', 'error'}:
        return render_template('batch/processing.html', job=job)

    query = BatchItem.query.filter_by(job_id=job.id)
    prediction_filter = request.args.get('prediction', '')
    risk_filter = request.args.get('risk', '')
    search = request.args.get('search', '').strip()
    if prediction_filter:
        query = query.filter(BatchItem.prediction == prediction_filter)
    if risk_filter:
        query = query.filter(BatchItem.risk == risk_filter)
    if search:
        query = query.filter(BatchItem.text.contains(search))
    page = request.args.get('page', 1, type=int)
    items = query.order_by(BatchItem.row_number).paginate(page=page, per_page=40, error_out=False)

    investigation_active = BatchItem.query.filter(
        BatchItem.job_id == job.id,
        BatchItem.investigation_status.in_(['queued', 'running']),
    ).count()

    return render_template(
        'batch/results.html', job=job, summary=_job_json(job.summary_json),
        validation=_job_json(job.validation_json), items=items,
        prediction_filter=prediction_filter, risk_filter=risk_filter,
        search=search,
        investigation_active=investigation_active,
    )


@batch_bp.route('/batch/download/<int:job_id>')
@login_required
def download(job_id):
    job = _owned_job(job_id)
    if job is None:
        return jsonify({'error': 'Unauthorized'}), 403
    if job.status != 'completed' or not job.result_path or not os.path.exists(job.result_path):
        return jsonify({'error': 'The export is not ready.'}), 400
    return send_file(
        job.result_path, as_attachment=True,
        download_name=f'batch_{job.id}_investigation_results.xlsx',
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )


@batch_bp.route('/batch/job/<int:job_id>/delete', methods=['POST'])
@login_required
def delete(job_id):
    job = _owned_job(job_id)
    if job is None:
        return jsonify({'error': 'Unauthorized'}), 403
    flash('This action is unavailable.', 'warning')
    return redirect(url_for('batch.results', job_id=job.id))


def _selected_items(job, data):
    query = BatchItem.query.filter_by(job_id=job.id).filter(
        BatchItem.prediction.in_(['Reliable', 'Misinformation', 'Misleading'])
    )
    raw_ids = data.get('item_ids') or []
    try:
        item_ids = [int(item_id) for item_id in raw_ids]
    except (TypeError, ValueError):
        item_ids = []
    query = (
        query.filter(BatchItem.id.in_(item_ids))
        if item_ids else query.filter(BatchItem.id == -1)
    )
    return query.order_by(BatchItem.row_number).all()


@batch_bp.route('/batch/job/<int:job_id>/investigate', methods=['POST'])
@login_required
def investigate(job_id):
    return _queue_investigations(job_id, send_to_review=False)


@batch_bp.route('/batch/job/<int:job_id>/send-review', methods=['POST'])
@login_required
def send_review(job_id):
    return _queue_investigations(job_id, send_to_review=True)


def _queue_investigations(job_id, send_to_review):
    job = _owned_job(job_id)
    if job is None:
        return jsonify({'error': 'Unauthorized'}), 403
    data = request.get_json() or {}
    items = _selected_items(job, data)
    if not items:
        return jsonify({'error': 'Select at least one eligible claim.'}), 400

    queued_ids, already_available, review_not_required = [], 0, 0
    for item in items:
        requires_review = item.prediction in FACT_CHECK_REQUIRED_LABELS
        if send_to_review and not requires_review:
            review_not_required += 1
        audit = item.investigation_audit
        if audit:
            if send_to_review and audit.status == 'complete':
                audit.review_status = (
                    'unassigned' if requires_review else 'not_required'
                )
            already_available += 1
            continue
        if item.previous_match == 'Exact' and item.matched_audit_id:
            item.investigation_audit_id = item.matched_audit_id
            item.investigation_status = 'previously_checked'
            already_available += 1
            continue
        item.investigation_status = 'queued'
        queued_ids.append(item.id)
    db.session.commit()
    if queued_ids:
        thread = threading.Thread(
            target=_investigate_batch_items,
            args=(queued_ids, send_to_review), daemon=True,
        )
        thread.start()
    return jsonify({
        'queued': len(queued_ids),
        'already_available': already_available,
        'review_not_required': review_not_required,
        'message': (
            'Eligible Misinformation and Misleading claims were queued for '
            'fact-check review. Reliable claims will complete without human review.'
            if send_to_review else
            'Selected claims were queued for Source Evidence Audit.'
        ),
    })


def _investigate_batch_items(item_ids, send_to_review):
    from app import app as flask_app
    from services.evidence_service import retrieve_evidence
    from services.gemini_service import extract_main_claim, generate_search_queries

    with flask_app.app_context():
        for item_id in item_ids:
            item = db.session.get(BatchItem, item_id)
            if not item:
                continue
            try:
                item.investigation_status = 'running'
                audit = EvidenceAudit(
                    user_id=item.job.user_id,
                    prediction_id=item.prediction_id,
                    prediction_confidence=item.confidence,
                    original_text=item.text,
                    predicted_label=item.prediction,
                    model_used=item.job.model_used,
                    ai_explanation='Created from the Batch Investigation Workspace.',
                    status='running',
                    review_status=(
                        'not_required'
                        if item.prediction not in FACT_CHECK_REQUIRED_LABELS else
                        ('unassigned' if send_to_review else 'batch_hold')
                    ),
                )
                db.session.add(audit)
                db.session.flush()
                item.investigation_audit_id = audit.id
                db.session.commit()

                main_claim = extract_main_claim(item.text) or item.text[:500]
                queries = generate_search_queries(item.text, main_claim)
                evidence, _ = retrieve_evidence(
                    item.text, main_claim, queries, predicted_label=item.prediction
                )
                evidence = [normalize_evidence_item(source) for source in evidence]
                audit.main_claim = main_claim
                audit.queries_json = json.dumps(queries, ensure_ascii=False)
                audit.status = 'complete'
                audit.error_message = ''
                audit.completed_at = datetime.utcnow()
                for source in evidence:
                    db.session.add(build_evidence_source(audit.id, source))
                record = ClaimRecord.query.filter_by(
                    fingerprint=claim_fingerprint(item.text)
                ).first()
                if record and record.investigation_audit_id is None:
                    record.investigation_audit_id = audit.id
                item.investigation_status = 'complete'
                db.session.commit()
            except Exception as exc:
                db.session.rollback()
                item = db.session.get(BatchItem, item_id)
                if item:
                    item.investigation_status = 'failed'
                    item.error_message = str(exc)[:1000]
                    if item.investigation_audit:
                        item.investigation_audit.status = 'failed'
                        item.investigation_audit.error_message = str(exc)[:1000]
                        item.investigation_audit.completed_at = datetime.utcnow()
                    db.session.commit()


@batch_bp.route('/batch/job/<int:job_id>/investigation-status')
@login_required
def investigation_status(job_id):
    job = _owned_job(job_id)
    if job is None:
        return jsonify({'error': 'Unauthorized'}), 403
    counts = dict(db.session.query(
        BatchItem.investigation_status, db.func.count(BatchItem.id)
    ).filter(BatchItem.job_id == job.id).group_by(BatchItem.investigation_status).all())
    active = counts.get('queued', 0) + counts.get('running', 0)
    return jsonify({'counts': counts, 'active': active})
