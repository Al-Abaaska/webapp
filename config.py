"""
Configuration — Database, email, and app settings.
Edit this file to change passwords or database connection.
"""
import os
try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if load_dotenv:
    load_dotenv(os.path.join(BASE_DIR, '.env'))


class Config:
    # Secret key for sessions and CSRF
    SECRET_KEY = os.getenv('SECRET_KEY', 'somali-misinfo-detection-2026')

    # MySQL database connection
    SQLALCHEMY_DATABASE_URI = os.getenv(
        'DATABASE_URL', 'mysql+pymysql://root:@localhost:3306/somali_misinfo'
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Upload and model folders
    UPLOAD_FOLDER = os.path.join(BASE_DIR, 'uploads')
    MODELS_FOLDER = os.path.join(BASE_DIR, 'ml_models')
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # 16 MB max upload

    # Gmail SMTP for sending OTP emails
    MAIL_SERVER = 'smtp.gmail.com'
    MAIL_PORT = 587
    MAIL_USE_TLS = True
    MAIL_USERNAME = os.getenv('MAIL_USERNAME', '')
    MAIL_PASSWORD = os.getenv('MAIL_PASSWORD', '')
    MAIL_DEFAULT_SENDER = os.getenv('MAIL_DEFAULT_SENDER', MAIL_USERNAME)
