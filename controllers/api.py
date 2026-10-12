from flask import Blueprint, request, jsonify

from services.preprocessing import is_somali
from services.ml_service import MLService
from services.ensemble_service import run_ensemble
from services.gemini_service import is_political


api_bp = Blueprint('api', __name__, url_prefix='/api')


@api_bp.route('/demo-predict', methods=['POST'])
def demo_predict():
    data = request.get_json(silent=True)
    if not data or not data.get('text'):
        return jsonify({'error': 'No text provided'}), 400

    text = data['text'].strip()
    model_key = data.get('model', 'ensemble')

    if not is_somali(text):
        return jsonify({'error': 'Text must be in Somali.'}), 400

    political_scope = is_political(text)
    if political_scope is None:
        return jsonify({
            'error': 'Political-scope checking is temporarily unavailable. Please try again shortly.',
            'scope_check_unavailable': True,
        }), 503
    if political_scope is False:
        return jsonify({
            'prediction': 'Not Political',
            'confidence': 0,
            'model': model_key,
            'badge_color': '#64748b',
            'analysis': 'Qoraalkani ma khuseeyo siyaasad ama maamul dowladeed. Nidaamkani wuxuu falanqeeyaa oo keliya xogta siyaasadeed.',
            'text': text,
            'model_key': model_key,
            'not_political': True,
        })

    ml = MLService()
    if model_key == 'ensemble':
        result = run_ensemble(ml, text)
        if result is None:
            return jsonify({'error': 'The ensemble requires at least two available models.'}), 503
        result['confidence'] = result['average_confidence']
        result['model'] = 'All Models - Ensemble'
        result['model_key'] = 'ensemble'
        result['ensemble'] = True
    else:
        if model_key not in ml.available_models:
            return jsonify({'error': 'The selected model is not available.'}), 503
        prediction, proba = ml.predict(text, model_key)
        if prediction is None:
            return jsonify({'error': 'Prediction failed'}), 500
        result = ml.build_result(prediction, proba, model_key)
        result['ensemble'] = False

    # Keep the public landing demo responsive. Full source retrieval, claim-history
    # matching, and fact-checker submission remain available after authentication.
    result['evidence_deferred'] = True

    return jsonify(result)
