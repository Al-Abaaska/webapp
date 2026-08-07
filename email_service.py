import random
import smtplib
import time
from datetime import datetime, timedelta

from flask_mail import Message
from models import db, OTP, mail


def generate_otp():
    """Generate a random 6-digit code."""
    return ''.join(random.choices('0123456789', k=6))


def create_otp_record(email, purpose):
    """Create a new OTP code in the database. Returns the code string."""
    OTP.query.filter_by(email=email, purpose=purpose, used=False).update({'used': True})
    db.session.commit()

    otp_code = generate_otp()
    otp = OTP(
        email=email,
        code=otp_code,
        purpose=purpose,
        expires_at=datetime.utcnow() + timedelta(minutes=10),
    )
    db.session.add(otp)
    db.session.commit()
    return otp_code


def verify_otp_code(email, code, purpose):
    """Verify an OTP code. Returns (success: bool, message: str)."""
    otp = OTP.query.filter_by(
        email=email, code=code, purpose=purpose, used=False
    ).order_by(OTP.created_at.desc()).first()

    if not otp:
        return False, 'Invalid OTP code.'
    if otp.expires_at < datetime.utcnow():
        return False, 'OTP code has expired.'

    otp.used = True
    db.session.commit()
    return True, 'OTP verified.'


def send_otp_email(user_email, username, otp_code, purpose):
    """Send a styled HTML email with the OTP code."""
    if purpose == 'verify':
        title = 'Verify Your Email'
        heading = 'Email Verification'
        desc = f'Hi {username}! Use the OTP code below to verify your email address.'
        footer = 'This code expires in 10 minutes. If you did not create an account, ignore this email.'
    else:
        title = 'Reset Your Password'
        heading = 'Password Reset'
        desc = f'Hi {username}! Use the OTP code below to reset your password.'
        footer = 'This code expires in 10 minutes. If you did not request a reset, ignore this email.'

    html = f"""
    <div style="font-family:Arial,sans-serif;max-width:560px;margin:0 auto;background:#f8fafc;border-radius:16px;overflow:hidden;">
        <div style="background:linear-gradient(135deg,#6366f1,#4f46e5);padding:40px 30px;text-align:center;">
            <h1 style="color:#fff;font-size:24px;margin:0;">Somali Misinformation Detector</h1>
            <p style="color:rgba(255,255,255,0.8);margin:8px 0 0;">{heading}</p>
        </div>
        <div style="padding:40px 30px;">
            <p style="color:#64748b;font-size:15px;line-height:1.6;margin:0 0 24px;">{desc}</p>
            <div style="text-align:center;margin:30px 0;">
                <div style="display:inline-block;background:#0f172a;color:#fff;font-size:36px;font-weight:800;
                            letter-spacing:12px;padding:18px 36px;border-radius:12px;font-family:monospace;">
                    {otp_code}
                </div>
            </div>
            <p style="color:#94a3b8;font-size:13px;margin:20px 0 0;">{footer}</p>
        </div>
        <div style="background:#0f172a;padding:20px 30px;text-align:center;">
            <p style="color:rgba(255,255,255,0.5);font-size:12px;margin:0;">JUST University - Somali Misinformation Detection Project</p>
        </div>
    </div>
    """

    msg = Message(
        subject=f'{title} - Somali Misinformation Detector',
        recipients=[user_email],
        html=html,
    )
    retryable_errors = (
        smtplib.SMTPServerDisconnected,
        smtplib.SMTPConnectError,
        smtplib.SMTPDataError,
        TimeoutError,
        OSError,
    )
    try:
        mail.send(msg)
    except retryable_errors:
        # Gmail occasionally closes or times out a fresh SMTP connection.
        # Retry once with a new connection; permanent authentication and
        # recipient errors still fail immediately.
        time.sleep(1)
        mail.send(msg)
