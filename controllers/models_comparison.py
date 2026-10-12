from flask import Blueprint, flash, redirect, url_for
from flask_login import login_required


models_bp = Blueprint('models_bp', __name__)


@models_bp.route('/models', methods=['GET', 'POST'])
@login_required
def index():
    """Keep old bookmarks working while using the unified Ensemble workflow."""
    flash(
        'Model comparison is now included in All Models - Ensemble on the Predict page.',
        'info',
    )
    return redirect(url_for('predict.index'))
