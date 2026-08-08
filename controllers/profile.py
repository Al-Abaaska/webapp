from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user

from models import db, User, Prediction, BatchJob


profile_bp = Blueprint('profile', __name__)


@profile_bp.route('/profile', methods=['GET', 'POST'])
@login_required
def index():
    if request.method == 'POST':
        email = request.form.get('email', '').strip()
        current_password = request.form.get('current_password', '')
        new_password = request.form.get('new_password', '')
        if email and email != current_user.email:
            if User.query.filter_by(email=email).first():
                flash('Email already in use.', 'danger')
                return redirect(url_for('profile.index'))
            current_user.email = email
        if new_password:
            if not current_user.check_password(current_password):
                flash('Current password is incorrect.', 'danger')
                return redirect(url_for('profile.index'))
            if len(new_password) < 6:
                flash('New password must be at least 6 characters.', 'danger')
                return redirect(url_for('profile.index'))
            current_user.set_password(new_password)
        db.session.commit()
        flash('Profile updated.', 'success')
        return redirect(url_for('profile.index'))

    uid = current_user.id
    return render_template('profile/index.html',
        total_predictions=Prediction.query.filter_by(user_id=uid).count(),
        total_batches=BatchJob.query.filter_by(user_id=uid).count(),
        stats={
            'reliable': Prediction.query.filter_by(user_id=uid, prediction='Reliable').count(),
            'misinfo': Prediction.query.filter_by(user_id=uid, prediction='Misinformation').count(),
            'misleading': Prediction.query.filter_by(user_id=uid, prediction='Misleading').count(),
        })
