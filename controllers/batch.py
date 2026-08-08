import io
import os
import uuid
import threading
import tempfile
from datetime import datetime

import pandas as pd
from flask import Blueprint, render_template, request, flash, jsonify, send_file
from flask_login import login_required, current_user

from models import db, Prediction, BatchJob
from services.ml_service import MLService
from services.preprocessing import is_somali


batch_bp = Blueprint('batch', __name__)


@batch_bp.route('/batch', methods=['GET', 'POST'])
@login_required
def index():
    ml = MLService()
    columns = None
    results = None
    if request.method == 'POST':
        file = request.files.get('file')
        column = request.form.get('column', '').strip()
        model_key = request.form.get('model', 'svm')
        export_format = request.form.get('format', 'xlsx')

        if not file or not file.filename:
            flash('Please upload a file.', 'warning')
            return render_template('batch/index.html', columns=columns, results=results)

        filename = file.filename.lower()
        if not filename.endswith(('.csv', '.xlsx', '.xls')):
            flash('Please upload a CSV or Excel file.', 'warning')
            return render_template('batch/index.html', columns=columns, results=results)

        try:
            df = pd.read_csv(file) if filename.endswith('.csv') else pd.read_excel(file)
            columns = df.columns.tolist()
        except Exception as e:
            flash(f'Error reading file: {e}', 'danger')
            return render_template('batch/index.html', columns=columns, results=results)

        if not column or column not in df.columns:
            flash(f'Column "{column}" not found.', 'danger')
            return render_template('batch/index.html', columns=columns, results=results)

        if model_key not in ml.models and model_key not in ml.transformers:
            flash('Selected model not available.', 'danger')
            return render_template('batch/index.html', columns=columns, results=results)

        results = []
        for text in df[column].astype(str):
            if is_somali(str(text)):
                pred, proba = ml.predict(str(text), model_key)
                if pred is not None:
                    r = ml.build_result(pred, proba, model_key)
                    results.append({'text': str(text)[:200], 'prediction': r['prediction'], 'confidence': r['confidence']})
                else:
                    results.append({'text': str(text)[:200], 'prediction': 'Error', 'confidence': 0})
            else:
                results.append({'text': str(text)[:200], 'prediction': 'Not Somali', 'confidence': 0})

        db.session.add(BatchJob(user_id=current_user.id, filename=file.filename, total_rows=len(results), status='completed'))
        for r in results:
            db.session.add(Prediction(user_id=current_user.id, text=r['text'], prediction=r['prediction'], confidence=r['confidence'], model_used=model_key))
        db.session.commit()

        out_df = df.copy()
        out_df['Prediction'] = [r['prediction'] for r in results]
        out_df['Confidence'] = [r['confidence'] for r in results]
        output = io.BytesIO()
        ext = 'csv' if export_format == 'csv' else 'xlsx'
        if ext == 'csv':
            out_df.to_csv(output, index=False)
            mimetype = 'text/csv'
        else:
            out_df.to_excel(output, index=False, engine='openpyxl')
            mimetype = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        output.seek(0)
        flash(f'Batch complete! {len(results)} rows processed.', 'success')
        return send_file(output, as_attachment=True,
                         download_name=f'predictions_{uuid.uuid4().hex[:8]}.{ext}',
                         mimetype=mimetype)

    return render_template('batch/index.html', columns=columns, results=results, model_key=request.form.get('model', 'svm'), models=ml.get_model_list())


def _process_batch(job_id, file_path, column, model_key, export_format):
    """Background thread: process each row and update progress."""
    from app import app as flask_app
    with flask_app.app_context():
        job = BatchJob.query.get(job_id)
        if not job:
            return
        job.status = 'processing'
        db.session.commit()

        try:
            if file_path.endswith('.csv'):
                df = pd.read_csv(file_path)
            else:
                df = pd.read_excel(file_path)

            ml = MLService()
            results = []
            total = len(df)
            job.total_rows = total
            db.session.commit()

            for i, text in enumerate(df[column].astype(str)):
                if is_somali(str(text)):
                    pred, proba = ml.predict(str(text), model_key)
                    if pred is not None:
                        r = ml.build_result(pred, proba, model_key)
                        results.append({'text': str(text)[:200], 'prediction': r['prediction'], 'confidence': r['confidence']})
                    else:
                        results.append({'text': str(text)[:200], 'prediction': 'Error', 'confidence': 0})
                else:
                    results.append({'text': str(text)[:200], 'prediction': 'Not Somali', 'confidence': 0})

                job.processed_rows = i + 1
                if (i + 1) % 5 == 0 or i + 1 == total:
                    db.session.commit()

            for r in results:
                db.session.add(Prediction(user_id=job.user_id, text=r['text'], prediction=r['prediction'], confidence=r['confidence'], model_used=model_key))

            out_df = df.copy()
            out_df['Prediction'] = [r['prediction'] for r in results]
            out_df['Confidence'] = [r['confidence'] for r in results]
            ext = 'csv' if export_format == 'csv' else 'xlsx'
            result_path = os.path.join(tempfile.gettempdir(), f'batch_{job_id}_{uuid.uuid4().hex[:8]}.{ext}')
            if ext == 'csv':
                out_df.to_csv(result_path, index=False)
            else:
                out_df.to_excel(result_path, index=False, engine='openpyxl')

            job.result_path = result_path
            job.status = 'completed'
            db.session.commit()
        except Exception as e:
            job.status = 'error'
            db.session.commit()


@batch_bp.route('/batch/upload', methods=['POST'])
@login_required
def upload():
    file = request.files.get('file')
    column = request.form.get('column', '').strip()
    model_key = request.form.get('model', 'svm')
    export_format = request.form.get('format', 'xlsx')

    if not file or not file.filename:
        return jsonify({'error': 'No file provided'}), 400

    filename = file.filename.lower()
    if not filename.endswith(('.csv', '.xlsx', '.xls')):
        return jsonify({'error': 'Invalid file type'}), 400

    ml = MLService()
    if model_key not in ml.models and model_key not in ml.transformers:
        return jsonify({'error': 'Model not available'}), 400

    temp_dir = tempfile.gettempdir()
    file_path = os.path.join(temp_dir, f'upload_{uuid.uuid4().hex[:8]}_{file.filename}')
    file.save(file_path)

    try:
        if file_path.endswith('.csv'):
            df = pd.read_csv(file_path)
        else:
            df = pd.read_excel(file_path)
    except Exception as e:
        return jsonify({'error': f'Cannot read file: {e}'}), 400

    if not column or column not in df.columns:
        return jsonify({'error': f'Column "{column}" not found'}), 400

    job = BatchJob(user_id=current_user.id, filename=file.filename, total_rows=len(df), status='queued')
    db.session.add(job)
    db.session.commit()

    thread = threading.Thread(target=_process_batch, args=(job.id, file_path, column, model_key, export_format))
    thread.daemon = True
    thread.start()

    return jsonify({'job_id': job.id, 'total_rows': len(df)})


@batch_bp.route('/batch/status/<int:job_id>')
@login_required
def status(job_id):
    job = BatchJob.query.get_or_404(job_id)
    if job.user_id != current_user.id:
        return jsonify({'error': 'Unauthorized'}), 403
    return jsonify({
        'status': job.status,
        'total': job.total_rows,
        'processed': job.processed_rows,
        'progress': round((job.processed_rows / job.total_rows) * 100) if job.total_rows > 0 else 0,
    })


@batch_bp.route('/batch/download/<int:job_id>')
@login_required
def download(job_id):
    job = BatchJob.query.get_or_404(job_id)
    if job.user_id != current_user.id:
        return jsonify({'error': 'Unauthorized'}), 403
    if job.status != 'completed' or not job.result_path or not os.path.exists(job.result_path):
        return jsonify({'error': 'File not ready'}), 400

    ext = os.path.splitext(job.result_path)[1].lstrip('.')
    mimetype = 'text/csv' if ext == 'csv' else 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    return send_file(job.result_path, as_attachment=True,
                     download_name=f'predictions_{uuid.uuid4().hex[:8]}.{ext}',
                     mimetype=mimetype)
