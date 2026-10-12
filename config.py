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
    BASE_DIR = BASE_DIR

    # Secret key for sessions and CSRF
    SECRET_KEY = os.getenv('SECRET_KEY', 'somali-misinfo-detection-2026')

    # MySQL database connection
    SQLALCHEMY_DATABASE_URI = os.getenv(
        'DATABASE_URL', 'mysql+pymysql://root:@localhost:3306/somali_misinfo'
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Primary-admin identity. Existing installations retain their password;
    # fresh installations must provide PRIMARY_ADMIN_PASSWORD in .env.
    PRIMARY_ADMIN_EMAIL = os.getenv('PRIMARY_ADMIN_EMAIL', 'admin@somali.com')
    PRIMARY_ADMIN_USERNAME = os.getenv('PRIMARY_ADMIN_USERNAME', 'Hussein')
    PRIMARY_ADMIN_PASSWORD = os.getenv('PRIMARY_ADMIN_PASSWORD', '')
    CONTACT_EMAIL = os.getenv('CONTACT_EMAIL', os.getenv('MAIL_USERNAME', ''))

    # Upload and model folders
    UPLOAD_FOLDER = os.path.join(BASE_DIR, 'uploads')
    MODELS_FOLDER = os.path.join(BASE_DIR, 'ml_models')
    # Media transcription accepts larger files than the CSV/Excel workflow.
    # Route-level validation still enforces the ASR-specific size and duration.
    ASR_MAX_UPLOAD_MB = int(os.getenv('ASR_MAX_UPLOAD_MB', '500'))
    ASR_MAX_UPLOAD_BYTES = ASR_MAX_UPLOAD_MB * 1024 * 1024
    MAX_CONTENT_LENGTH = ASR_MAX_UPLOAD_BYTES
    ASR_MAX_DURATION_SECONDS = int(os.getenv('ASR_MAX_DURATION_SECONDS', '3600'))
    ASR_FFMPEG_TIMEOUT_SECONDS = int(
        os.getenv('ASR_FFMPEG_TIMEOUT_SECONDS', '1800')
    )
    MEDIA_URL_SOCKET_TIMEOUT_SECONDS = int(
        os.getenv('MEDIA_URL_SOCKET_TIMEOUT_SECONDS', '30')
    )
    # Current yt-dlp releases use an external JavaScript runtime for YouTube.
    # A private Netscape-format cookie file is optional for platforms that
    # block unauthenticated cloud-server traffic. Never commit that file.
    YTDLP_DENO_PATH = os.getenv('YTDLP_DENO_PATH', '').strip()
    YTDLP_COOKIE_FILE = os.getenv('YTDLP_COOKIE_FILE', '').strip()
    # Public social-platform page retrieval is intentionally disabled by
    # default. Uploaded media remains the supported production workflow.
    ENABLE_PUBLIC_MEDIA_LINKS = os.getenv(
        'ENABLE_PUBLIC_MEDIA_LINKS', 'false'
    ).strip().casefold() in {'1', 'true', 'yes', 'on'}
    ASR_CHUNK_SECONDS = float(os.getenv('ASR_CHUNK_SECONDS', '28'))
    ASR_OVERLAP_SECONDS = float(os.getenv('ASR_OVERLAP_SECONDS', '1'))
    ASR_RUNTIME_DIR = os.path.join(BASE_DIR, 'uploads', 'asr_runtime')
    ASR_TRANSCRIPT_DIR = os.path.join(BASE_DIR, 'uploads', 'asr_transcripts')
    ASR_RESULT_CACHE_DIR = os.path.join(BASE_DIR, 'uploads', 'asr_result_cache')
    ASR_RESULT_CACHE_ENABLED = os.getenv(
        'ASR_RESULT_CACHE_ENABLED', 'true'
    ).strip().casefold() in {'1', 'true', 'yes', 'on'}
    ASR_URL_CACHE_TTL_SECONDS = int(
        os.getenv('ASR_URL_CACHE_TTL_SECONDS', str(7 * 24 * 60 * 60))
    )
    ASR_MODEL_ID = os.getenv(
        'ASR_MODEL_ID',
        'IbrahimDayax/xls-r-1b-somali-stt-asr',
    )
    # "auto" uses RunPod when its credentials are configured and otherwise
    # keeps the existing local CPU path available for development.
    ASR_BACKEND = os.getenv('ASR_BACKEND', 'auto').strip().casefold()
    RUNPOD_API_KEY = os.getenv('RUNPOD_API_KEY', '').strip()
    RUNPOD_ASR_ENDPOINT_ID = os.getenv('RUNPOD_ASR_ENDPOINT_ID', '').strip()
    RUNPOD_ASR_WAIT_MS = int(os.getenv('RUNPOD_ASR_WAIT_MS', '300000'))
    RUNPOD_ASR_REQUEST_TIMEOUT_SECONDS = int(
        os.getenv('RUNPOD_ASR_REQUEST_TIMEOUT_SECONDS', '330')
    )
    # Base64 expands audio by about one third; 13 MB stays below RunPod's
    # 20 MB synchronous-request limit after JSON encoding.
    RUNPOD_ASR_MAX_PAYLOAD_MB = int(os.getenv('RUNPOD_ASR_MAX_PAYLOAD_MB', '13'))
    ASR_MODELS = {
        # Dayax is the only supported Somali ASR model. The previous Fast ASR
        # checkpoint has intentionally been removed from the application.
        'dayax': ASR_MODEL_ID,
    }
    TRANSCRIPT_CLAIM_MAX_WORDS = int(
        os.getenv('TRANSCRIPT_CLAIM_MAX_WORDS', '110')
    )
    TRANSCRIPT_CLAIM_OVERLAP_WORDS = int(
        os.getenv('TRANSCRIPT_CLAIM_OVERLAP_WORDS', '20')
    )
    TRANSCRIPT_CONTEXT_WORDS = int(os.getenv('TRANSCRIPT_CONTEXT_WORDS', '30'))
    # Dayax normally returns one unpunctuated paragraph. Treat 60 words as one
    # paragraph-equivalent and extract a main claim only beyond three of them.
    TRANSCRIPT_CLAIM_EXTRACTION_MIN_WORDS = int(
        os.getenv('TRANSCRIPT_CLAIM_EXTRACTION_MIN_WORDS', '180')
    )

    # Gmail SMTP for sending OTP emails
    MAIL_SERVER = 'smtp.gmail.com'
    MAIL_PORT = 587
    MAIL_USE_TLS = True
    MAIL_USERNAME = os.getenv('MAIL_USERNAME', '')
    MAIL_PASSWORD = os.getenv('MAIL_PASSWORD', '')
    MAIL_DEFAULT_SENDER = os.getenv('MAIL_DEFAULT_SENDER', MAIL_USERNAME)
