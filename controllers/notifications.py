from datetime import datetime

from flask import Blueprint, abort, flash, redirect, render_template, url_for
from flask_login import current_user, login_required

from models import db, Notification


notifications_bp = Blueprint('notifications', __name__, url_prefix='/notifications')


@notifications_bp.route('/')
@login_required
def index():
    notifications = Notification.query.filter_by(
        user_id=current_user.id
    ).order_by(Notification.created_at.desc()).all()
    return render_template('notifications.html', notifications=notifications)


@notifications_bp.route('/<int:notification_id>/open', methods=['POST'])
@login_required
def open_notification(notification_id):
    notification = Notification.query.filter_by(
        id=notification_id, user_id=current_user.id
    ).first()
    if notification is None:
        abort(404)
    if not notification.is_read:
        notification.is_read = True
        notification.read_at = datetime.utcnow()
        db.session.commit()
    if notification.audit_id:
        return redirect(url_for('history.evidence_detail', audit_id=notification.audit_id))
    return redirect(url_for('notifications.index'))


@notifications_bp.route('/read-all', methods=['POST'])
@login_required
def read_all():
    updated = Notification.query.filter_by(
        user_id=current_user.id, is_read=False
    ).update({
        Notification.is_read: True,
        Notification.read_at: datetime.utcnow(),
    }, synchronize_session=False)
    db.session.commit()
    flash(f'{updated} notification(s) marked as read.', 'success')
    return redirect(url_for('notifications.index'))
