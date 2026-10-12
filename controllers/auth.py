import json
import os
import shutil
import uuid
from datetime import datetime

from flask import Blueprint, render_template, request, redirect, url_for, flash, current_app
from flask_login import login_user, logout_user, login_required, current_user
from werkzeug.utils import secure_filename

from models import db, User
from services.email_service import create_otp_record, verify_otp_code, send_otp_email


auth_bp = Blueprint('auth', __name__)

FACT_CHECKER_DOCUMENT_EXTENSIONS = {'pdf', 'png', 'jpg', 'jpeg'}
FACT_CHECKER_DOCUMENT_LIMIT = 8 * 1024 * 1024
FACT_CHECKER_CERTIFICATE_LIMIT = 5


def _validate_application_document(upload, label):
    if not upload or not upload.filename:
        return f'{label} is required.'
    extension = upload.filename.rsplit('.', 1)[-1].lower() if '.' in upload.filename else ''
    if extension not in FACT_CHECKER_DOCUMENT_EXTENSIONS:
        return f'{label} must be a PDF, PNG, JPG, or JPEG file.'
    upload.stream.seek(0, os.SEEK_END)
    file_size = upload.stream.tell()
    upload.stream.seek(0)
    if file_size > FACT_CHECKER_DOCUMENT_LIMIT:
        return f'{label} must be 8 MB or smaller.'
    file_header = upload.stream.read(8)
    upload.stream.seek(0)
    valid_signature = (
        (extension == 'pdf' and file_header.startswith(b'%PDF'))
        or (extension == 'png' and file_header.startswith(b'\x89PNG\r\n\x1a\n'))
        or (extension in {'jpg', 'jpeg'} and file_header.startswith(b'\xff\xd8\xff'))
    )
    if not valid_signature:
        return f'{label} content does not match its file extension.'
    return None


def _save_application_document(upload, user_id, category):
    original_name = secure_filename(upload.filename)
    stored_name = f'{category}-{uuid.uuid4().hex}-{original_name}'
    relative_path = os.path.join(
        'fact_checker_applications', str(user_id), stored_name
    )
    absolute_path = os.path.join(current_app.config['UPLOAD_FOLDER'], relative_path)
    os.makedirs(os.path.dirname(absolute_path), exist_ok=True)
    upload.save(absolute_path)
    return relative_path.replace('\\', '/'), original_name


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
            if not user.is_active:
                flash('Your account is inactive. Please contact an administrator.', 'danger')
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
        account_type = request.form.get('account_type', 'user').strip().lower()
        organization = request.form.get('organization', '').strip()
        professional_id = request.form.get('professional_id', '').strip()
        id_document = request.files.get('id_document')
        certificates = [
            item for item in request.files.getlist('certificates')
            if item and item.filename
        ]
        if account_type not in {'user', 'fact_checker'}:
            flash('Select a valid account type.', 'danger')
            return render_template('auth/register.html')
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
        if account_type == 'fact_checker':
            if len(organization) < 2:
                flash('Enter the organization or company where you work.', 'danger')
                return render_template('auth/register.html')
            if len(professional_id) < 2:
                flash('Enter your professional or employee ID.', 'danger')
                return render_template('auth/register.html')
            if len(certificates) > FACT_CHECKER_CERTIFICATE_LIMIT:
                flash('Upload no more than five certificate files.', 'danger')
                return render_template('auth/register.html')
            document_error = _validate_application_document(
                id_document, 'Identity or employment document'
            )
            if document_error:
                flash(document_error, 'danger')
                return render_template('auth/register.html')
            if not certificates:
                flash('Upload at least one professional certificate.', 'danger')
                return render_template('auth/register.html')
            for certificate in certificates:
                document_error = _validate_application_document(
                    certificate, 'Each certificate'
                )
                if document_error:
                    flash(document_error, 'danger')
                    return render_template('auth/register.html')

        user = User(
            username=username,
            email=email,
            role='user',
            fact_checker_application_status=(
                'pending' if account_type == 'fact_checker' else 'not_requested'
            ),
            fact_checker_organization=(organization if account_type == 'fact_checker' else ''),
            fact_checker_professional_id=(
                professional_id if account_type == 'fact_checker' else ''
            ),
            fact_checker_applied_at=(
                datetime.utcnow() if account_type == 'fact_checker' else None
            ),
        )
        user.set_password(password)
        application_directory = None
        try:
            db.session.add(user)
            db.session.flush()
            if account_type == 'fact_checker':
                application_directory = os.path.join(
                    current_app.config['UPLOAD_FOLDER'],
                    'fact_checker_applications',
                    str(user.id),
                )
                id_path, _ = _save_application_document(
                    id_document, user.id, 'identity'
                )
                saved_certificates = []
                for certificate in certificates:
                    certificate_path, original_name = _save_application_document(
                        certificate, user.id, 'certificate'
                    )
                    saved_certificates.append({
                        'path': certificate_path,
                        'name': original_name,
                    })
                user.fact_checker_id_document = id_path
                user.fact_checker_certificates_json = json.dumps(saved_certificates)
            db.session.commit()
        except Exception:
            db.session.rollback()
            if application_directory and os.path.isdir(application_directory):
                shutil.rmtree(application_directory, ignore_errors=True)
            current_app.logger.exception('Could not create registration account')
            flash('The account could not be created. Please try again.', 'danger')
            return render_template('auth/register.html')
        try:
            otp_code = create_otp_record(email, 'verify')
            send_otp_email(email, username, otp_code, 'verify')
            if account_type == 'fact_checker':
                flash(
                    'Account created and fact-checker application submitted. '
                    'Verify your email while an administrator reviews your documents.',
                    'success',
                )
            else:
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

@auth_bp.route('/logout', methods=['POST'])
@login_required
def logout():
    logout_user()
    flash('Logged out successfully.', 'info')
    return redirect(url_for('main.index'))
