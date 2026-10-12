"""Controllers package — registers all blueprints."""
from controllers.main import main_bp
from controllers.auth import auth_bp
from controllers.predict import predict_bp
from controllers.batch import batch_bp
from controllers.history import history_bp
from controllers.models_comparison import models_bp
from controllers.profile import profile_bp
from controllers.fact_checker import fact_checker_bp
from controllers.admin import admin_bp
from controllers.api import api_bp
from controllers.sources import sources_bp
from controllers.notifications import notifications_bp

ALL_BLUEPRINTS = [
    main_bp, auth_bp, predict_bp, batch_bp, history_bp,
    models_bp, profile_bp, fact_checker_bp, admin_bp, api_bp, sources_bp,
    notifications_bp,
]
