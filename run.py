import os
import threading
from app import app

def preload_models():
    """Preload ML models synchronously on startup."""
    try:
        from services.ml_service import MLService
        ml = MLService()
        print(
            f'[Startup] Models preloaded ({ml.available_model_count}/8): '
            f'{list(ml.available_models.keys())}'
        )
    except Exception as e:
        print(f'[Startup] Model preload failed: {e}')

if __name__ == '__main__':
    os.makedirs('uploads', exist_ok=True)
    print('[Startup] Preloading ML models (this may take a moment)...')
    preload_models()
    app.run(debug=True, use_reloader=False, host='0.0.0.0', port=5000)
