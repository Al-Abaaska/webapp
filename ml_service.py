"""
ML Service — Loads all 8 models (4 traditional + 2 transformers + 2 Keras) and makes predictions.
"""
import os
import joblib

# Label mapping (matches notebook)
LABEL_MAP = {0: 'Reliable', 1: 'Misinformation', 2: 'Misleading'}

# All 8 models with rankings
MODEL_CONFIG = {
    'sombertb': {
        'name': 'SomBERTb',
        'type': 'transformer',
        'rank': 1,
        'f1': 97.99,
        'badge': 'Best',
        'color': '#10b981',
    },
    'somerta': {
        'name': 'SomBERTa',
        'type': 'transformer',
        'rank': 2,
        'f1': 96.39,
        'badge': '2nd',
        'color': '#3b82f6',
    },
    'svm': {
        'name': 'SVM',
        'type': 'traditional',
        'rank': 3,
        'f1': 94.36,
        'badge': '3rd',
        'color': '#f59e0b',
    },
    'logistic_regression': {
        'name': 'Logistic Regression',
        'type': 'traditional',
        'rank': 4,
        'f1': 94.15,
        'badge': '4th',
        'color': '#8b5cf6',
    },
    'random_forest': {
        'name': 'Random Forest',
        'type': 'traditional',
        'rank': 5,
        'f1': 92.56,
        'badge': '5th',
        'color': '#ef4444',
    },
    'naive_bayes': {
        'name': 'Naive Bayes',
        'type': 'traditional',
        'rank': 6,
        'f1': 91.78,
        'badge': '6th',
        'color': '#6b7280',
    },
    'cnn_bilstm': {
        'name': 'CNN-BiLSTM',
        'type': 'keras',
        'rank': 7,
        'f1': 0,
        'badge': '7th',
        'color': '#ec4899',
    },
    'bilstm': {
        'name': 'BiLSTM',
        'type': 'keras',
        'rank': 8,
        'f1': 0,
        'badge': '8th',
        'color': '#14b8a6',
    },
}

# Pretty names for display
MODEL_NAMES = {k: v['name'] for k, v in MODEL_CONFIG.items()}
MODEL_NAMES['ensemble'] = 'All Models - Ensemble'


def _load_traditional_model(model_name):
    """Load a .pkl model file from the models/ folder."""
    models_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'models')
    path = os.path.join(models_dir, f'{model_name}.pkl')
    if not os.path.exists(path):
        return None
    model = joblib.load(path)
    if hasattr(model, 'probability') and model.probability and not hasattr(model, '_effective_probability'):
        model._effective_probability = True
    return model


def _load_transformer_model(model_name):
    """Load a transformer model from the models/ folder."""
    try:
        from transformers import AutoTokenizer, AutoModelForSequenceClassification
        import torch
    except ImportError as e:
        print(f'[ML] transformers/torch import failed: {e}')
        return None, None

    models_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'models')
    model_path = os.path.join(models_dir, model_name)

    if not os.path.exists(model_path):
        print(f'[ML] Model path not found: {model_path}')
        return None, None

    try:
        print(f'[ML] Loading transformer: {model_path}')
        tokenizer = AutoTokenizer.from_pretrained(model_path)
        model = AutoModelForSequenceClassification.from_pretrained(model_path)
        model.eval()
        print(f'[ML] Loaded {model_name} OK')
        return model, tokenizer
    except Exception as e:
        print(f'[ML] Failed to load {model_name}: {e}')
        return None, None


def _load_keras_model(model_file):
    """Load a Keras .keras model from the models/ folder."""
    try:
        import tensorflow as tf
    except ImportError as e:
        print(f'[ML] tensorflow import failed: {e}')
        return None

    models_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'models')
    path = os.path.join(models_dir, model_file)

    if not os.path.exists(path):
        print(f'[ML] Keras model not found: {path}')
        return None

    try:
        print(f'[ML] Loading Keras model: {path}')
        model = tf.keras.models.load_model(path)
        print(f'[ML] Loaded {model_file} OK')
        return model
    except Exception as e:
        print(f'[ML] Failed to load {model_file}: {e}')
        return None


def _load_keras_tokenizer():
    """Load the shared Keras tokenizer."""
    models_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'models')
    path = os.path.join(models_dir, 'keras_tokenizer.pkl')
    if not os.path.exists(path):
        print(f'[ML] Keras tokenizer not found: {path}')
        return None
    try:
        tokenizer = joblib.load(path)
        print(f'[ML] Loaded keras_tokenizer OK')
        return tokenizer
    except Exception as e:
        print(f'[ML] Failed to load keras_tokenizer: {e}')
        return None


_instance = None

class MLService:
    """Loads all 8 ML models once and provides prediction methods."""

    _loaded = False

    def __new__(cls):
        global _instance
        if _instance is None:
            _instance = super().__new__(cls)
        return _instance

    def __init__(self):
        if MLService._loaded:
            return
        self.tfidf = _load_traditional_model('tfidf_vectorizer')
        self.models = {}
        self.transformers = {}
        self.keras_models = {}
        self.keras_tokenizer = None

        # Load traditional ML models
        for name in ['logistic_regression', 'naive_bayes', 'random_forest', 'svm']:
            model = _load_traditional_model(name)
            if model:
                self.models[name] = model

        # Load transformer models
        transformer_map = {'somerta': 'somerta_model', 'sombertb': 'sombertb_model'}
        for key, folder in transformer_map.items():
            model, tokenizer = _load_transformer_model(folder)
            if model and tokenizer:
                self.transformers[key] = {'model': model, 'tokenizer': tokenizer}

        # Load Keras models
        self.keras_tokenizer = _load_keras_tokenizer()
        keras_map = {'cnn_bilstm': 'cnn_bilstm_model.keras', 'bilstm': 'bilstm_model.keras'}
        for key, filename in keras_map.items():
            model = _load_keras_model(filename)
            if model:
                self.keras_models[key] = model

        MLService._loaded = True
        print(f'[ML] Service ready. Traditional: {list(self.models.keys())}, Transformers: {list(self.transformers.keys())}, Keras: {list(self.keras_models.keys())}')

    def predict(self, text, model_key='svm'):
        """Predict a single text. Returns (prediction_int, probabilities_list)."""
        # Transformer prediction
        if model_key in self.transformers:
            return self._predict_transformer(text, model_key)

        # Keras prediction
        if model_key in self.keras_models:
            return self._predict_keras(text, model_key)

        # Traditional ML prediction
        if self.tfidf is None or model_key not in self.models:
            return None, None

        from preprocessing import preprocess
        processed = preprocess(text)
        vectorized = self.tfidf.transform([processed])
        prediction = self.models[model_key].predict(vectorized)[0]

        try:
            proba = self.models[model_key].predict_proba(vectorized)[0]
        except Exception:
            proba = None

        return int(prediction), proba.tolist() if proba is not None else None

    def _predict_transformer(self, text, model_key):
        """Predict using transformer model."""
        try:
            import torch
        except ImportError:
            return None, None

        transformer = self.transformers[model_key]
        tokenizer = transformer['tokenizer']
        model = transformer['model']

        inputs = tokenizer(text, return_tensors='pt', truncation=True, padding=True, max_length=512)
        with torch.no_grad():
            outputs = model(**inputs)

        probs = torch.nn.functional.softmax(outputs.logits, dim=-1)
        prediction = torch.argmax(probs, dim=-1).item()
        proba = probs[0].tolist()

        return prediction, proba

    def _predict_keras(self, text, model_key):
        """Predict using Keras model."""
        if self.keras_tokenizer is None or model_key not in self.keras_models:
            return None, None

        try:
            import numpy as np
        except ImportError:
            return None, None

        model = self.keras_models[model_key]
        tokenizer = self.keras_tokenizer

        # Keras tokenizer expects strings
        seq = tokenizer.texts_to_sequences([text])
        # Pad to model's expected input length
        from tensorflow.keras.preprocessing.sequence import pad_sequences
        max_len = model.input_shape[1] if model.input_shape[1] else 200
        padded = pad_sequences(seq, maxlen=max_len, padding='post', truncating='post')

        pred_proba = model.predict(padded, verbose=0)[0]
        prediction = int(np.argmax(pred_proba))
        proba = pred_proba.tolist()

        return prediction, proba

    def build_result(self, prediction, proba, model_key):
        """Build a nice result dictionary from raw prediction data."""
        config = MODEL_CONFIG.get(model_key, {})
        label = LABEL_MAP.get(prediction, 'Unknown')
        confidence = round(max(proba) * 100, 2) if proba else 0
        probabilities = (
            {LABEL_MAP[i]: round(p * 100, 2) for i, p in enumerate(proba)}
            if proba else {}
        )
        return {
            'prediction': label,
            'confidence': confidence,
            'probabilities': probabilities,
            'name': config.get('name', model_key),
            'model': config.get('name', model_key),
            'model_key': model_key,
            'rank': config.get('rank', 99),
            'badge': config.get('badge', ''),
            'badge_color': config.get('color', '#6b7280'),
        }

    @property
    def available_models(self):
        """Return dict of models that loaded successfully."""
        all_available = {}
        all_available.update(self.models)
        all_available.update(self.transformers)
        all_available.update(self.keras_models)
        return all_available

    @property
    def available_model_count(self):
        return len(self.available_models)

    def get_model_list(self):
        """Return sorted list of all available models with info."""
        result = []
        for key, config in sorted(MODEL_CONFIG.items(), key=lambda x: x[1]['rank']):
            available = key in self.models or key in self.transformers or key in self.keras_models
            result.append({
                'key': key,
                'name': config['name'],
                'type': config['type'],
                'rank': config['rank'],
                'f1': config['f1'],
                'badge': config['badge'],
                'color': config['color'],
                'available': available,
            })
        return result
