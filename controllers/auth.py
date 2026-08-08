from flask import Blueprint, render_template, request, redirect, url_for, flash, current_app
from flask_login import login_user, logout_user, login_required, current_user

from models import db, User
from services.email_service import create_otp_record, verify_otp_code, send_otp_email


auth_bp = Blueprint('auth', __name__)


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('main.dashboard'))
    if request.method == 'POST':
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '')
        remember = request.form.get('remember', False)
        user = User.query.filter_by(email=email).first()
        if user and user.check_password(password):
            if user.is_blocked:
                flash('Your account has been blocked. Please contact support.', 'danger')
                return render_template('auth/login.html')
            if not user.email_verified and not user.is_admin:
                flash('Please verify your email first.', 'warning')
                return redirect(url_for('auth.verify_otp', email=user.email))
            login_user(user, remember=bool(remember))
            flash(f'Welcome back, {user.username}!', 'success')
            return redirect(url_for('main.dashboard'))
        flash('Invalid email or password.', 'danger')
    return render_template('auth/login.html')


@auth_bp.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('main.dashboard'))
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '')
        confirm = request.form.get('confirm_password', '')
        if len(username) < 3:
            flash('Username must be at least 3 characters.', 'danger')
            return render_template('auth/register.html')
        if len(password) < 6:
            flash('Password must be at least 6 characters.', 'danger')
            return render_template('auth/register.html')
        if password != confirm:
            flash('Passwords do not match.', 'danger')
            return render_template('auth/register.html')
        if User.query.filter_by(username=username).first():
            flash('Username already taken.', 'danger')
            return render_template('auth/register.html')
        if User.query.filter_by(email=email).first():
            flash('Email already registered.', 'danger')
            return render_template('auth/register.html')
        user = User(username=username, email=email, role='user')
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        try:
            otp_code = create_otp_record(email, 'verify')
            send_otp_email(email, username, otp_code, 'verify')
            flash('Account created! Check your email for the OTP code.', 'success')
        except Exception as exc:
            current_app.logger.exception(
                'OTP email failed during registration (%s)', type(exc).__name__
            )
            flash('Account created but email could not be sent. Contact admin.', 'warning')
        return redirect(url_for('auth.verify_otp', email=email))
    return render_template('auth/register.html')


@auth_bp.route('/verify-otp', methods=['GET', 'POST'])
def verify_otp():
    email = request.args.get('email', '') if request.method == 'GET' else request.form.get('email', '').strip()
    if not email:
        flash('Email address is required.', 'danger')
        return redirect(url_for('auth.login'))
    if request.method == 'POST':
        code = request.form.get('otp', '').strip()
        success, msg = verify_otp_code(email, code, 'verify')
        if success:
            user = User.query.filter_by(email=email).first()
            if user:
                user.email_verified = True
                db.session.commit()
            flash('Email verified! You can now log in.', 'success')
            return redirect(url_for('auth.login'))
        flash(msg, 'danger')
    return render_template('auth/verify_otp.html', email=email)


@auth_bp.route('/resend-otp', methods=['POST'])
def resend_otp():
    email = request.form.get('email', '').strip()
    purpose = request.form.get('purpose', 'verify').strip()
    if not email:
        flash('Email is required.', 'danger')
        return redirect(url_for('auth.login'))
    user = User.query.filter_by(email=email).first()
    if not user:
        flash('No account found with that email.', 'danger')
        return redirect(url_for('auth.login'))
    if purpose == 'verify' and user.email_verified:
        flash('Email already verified. Please log in.', 'info')
        return redirect(url_for('auth.login'))
    try:
        otp_code = create_otp_record(email, purpose)
        send_otp_email(email, user.username, otp_code, purpose)
        flash('OTP code sent! Check your inbox.', 'success')
    except Exception as exc:
        current_app.logger.exception(
            'OTP email failed during resend (%s)', type(exc).__name__
        )
        flash('Email service is temporarily unavailable. Please try again.', 'danger')
    if purpose == 'verify':
        return redirect(url_for('auth.verify_otp', email=email))
    return redirect(url_for('auth.reset_password', email=email))


@auth_bp.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        email = request.form.get('email', '').strip()
        user = User.query.filter_by(email=email).first()
        if not user:
            flash('No account found with that email.', 'danger')
            return render_template('auth/forgot_password.html')
        try:
            otp_code = create_otp_record(email, 'reset')
            send_otp_email(email, user.username, otp_code, 'reset')
            flash('OTP code sent! Check your inbox.', 'success')
        except Exception as exc:
            current_app.logger.exception(
                'OTP email failed during password reset (%s)', type(exc).__name__
            )
            flash('Email service is temporarily unavailable. Please try again.', 'danger')
        return redirect(url_for('auth.reset_password', email=email))
    return render_template('auth/forgot_password.html')


@auth_bp.route('/reset-password', methods=['GET', 'POST'])
def reset_password():
    email = request.args.get('email', '') if request.method == 'GET' else request.form.get('email', '').strip()
    if not email:
        flash('Email is required.', 'danger')
        return redirect(url_for('auth.forgot_password'))
    if request.method == 'POST':
        code = request.form.get('otp', '').strip()
        new_password = request.form.get('new_password', '')
        confirm_password = request.form.get('confirm_password', '')
        if not new_password or len(new_password) < 6:
            flash('Password must be at least 6 characters.', 'danger')
            return render_template('auth/reset_password.html', email=email)
        if new_password != confirm_password:
            flash('Passwords do not match.', 'danger')
            return render_template('auth/reset_password.html', email=email)
        success, msg = verify_otp_code(email, code, 'reset')
        if not success:
            flash(msg, 'danger')
            return render_template('auth/reset_password.html', email=email)
        user = User.query.filter_by(email=email).first()
        if not user:
            flash('User not found.', 'danger')
            return redirect(url_for('auth.login'))
        user.set_password(new_password)
        db.session.commit()
        flash('Password reset successful!', 'success')
        return redirect(url_for('auth.login'))
    return render_template('auth/reset_password.html', email=email)

@auth_bp.route('/logout')
@login_required
def logout():
    logout_user()
    flash('Logged out successfully.', 'info')
    return redirect(url_for('main.index'))
