import json

from flask import Blueprint, render_template, redirect, url_for, current_app
from flask_login import login_required, current_user

from models import db, Prediction, BatchJob
from services.ml_service import MLService, MODEL_NAMES


main_bp = Blueprint('main', __name__)


@main_bp.route('/')
def index():
    if current_user.is_authenticated:
        return redirect(url_for('main.dashboard'))
    return render_template(
        'landing/index.html',
        contact_email=current_app.config.get('CONTACT_EMAIL', ''),
    )


@main_bp.route('/dashboard')
@login_required
def dashboard():
    if current_user.is_admin:
        return redirect(url_for('admin.index'))
    if current_user.can_fact_check:
        return redirect(url_for('fact_checker.dashboard'))
    ml = MLService()
    uid = current_user.id

    total_predictions = Prediction.query.filter_by(user_id=uid).count()
    reliable = Prediction.query.filter_by(user_id=uid, prediction='Reliable').count()
    misinfo = Prediction.query.filter_by(user_id=uid, prediction='Misinformation').count()
    misleading = Prediction.query.filter_by(user_id=uid, prediction='Misleading').count()
    batch_count = BatchJob.query.filter_by(user_id=uid).count()
    recent = Prediction.query.filter_by(user_id=uid).order_by(Prediction.created_at.desc()).limit(5).all()

    model_usage = db.session.query(
        Prediction.model_used, db.func.count(Prediction.id)
    ).filter_by(user_id=uid).group_by(Prediction.model_used).all()

    return render_template('dashboard/index.html',
        total_predictions=total_predictions,
        models_available=ml.available_model_count,
        batch_count=batch_count,
        reliable=reliable, misinfo=misinfo, misleading=misleading,
        recent=recent,
        model_labels=json.dumps([MODEL_NAMES.get(m[0], m[0]) for m in model_usage]),
        model_values=json.dumps([m[1] for m in model_usage]),
        label_labels=json.dumps(['Reliable', 'Misinformation', 'Misleading']),
        label_values=json.dumps([reliable, misinfo, misleading]),
    )
