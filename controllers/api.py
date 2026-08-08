from flask import Blueprint, request, jsonify

from services.preprocessing import is_somali
from services.ml_service import MLService
from services.gemini_service import is_political


api_bp = Blueprint('api', __name__, url_prefix='/api')


@api_bp.route('/demo-predict', methods=['POST'])
def demo_predict():
    data = request.get_json(silent=True)
    if not data or not data.get('text'):
        return jsonify({'error': 'No text provided'}), 400

    text = data['text'].strip()
    model_key = data.get('model', 'svm')

    if not is_somali(text):
        return jsonify({'error': 'Text must be in Somali.'}), 400

    political_scope = is_political(text)
    if political_scope is None:
        return jsonify({
            'error': 'The LLM political-scope check is unavailable. Configure CEREBRAS_API_KEY or GROQ_API_KEY and try again.',
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
    if model_key not in ml.models and model_key not in ml.transformers:
        return jsonify({'error': 'Model not available'}), 500

    prediction, proba = ml.predict(text, model_key)
    if prediction is None:
        return jsonify({'error': 'Prediction failed'}), 500

    result = ml.build_result(prediction, proba, model_key)

    try:
        from services.gemini_service import extract_main_claim, generate_search_queries, semantic_match, verify_same_event
        from search.source_search import search_somali_sources_with_fallback
        from search.facebook_search import search_facebook_with_fallback

        main_claim = extract_main_claim(text)
        search_queries = []

        if main_claim:
            result['main_claim'] = main_claim
            search_queries = generate_search_queries(text, main_claim)
            result['search_queries'] = search_queries

            if result['prediction'] == 'Reliable':
                reported_by = search_somali_sources_with_fallback(
                    text[:200],
                    text
                )
                print(f'[API FactCheck] Found {len(reported_by)} sources before verification')
                if reported_by:
                    reported_by = verify_same_event(main_claim, reported_by)
                    print(f'[API FactCheck] {len(reported_by)} sources after verification')

                fb_posts = search_facebook_with_fallback(
                    search_queries, text, max_posts=5
                )
                if fb_posts:
                    reported_by.extend(fb_posts)
                    print(f'[API FactCheck] Added {len(fb_posts)} Facebook posts')
                result['reported_by'] = reported_by
            else:
                # Use same search flow as Reliable
                reported_by = search_somali_sources_with_fallback(
                    text[:200],
                    text
                )
                fb_posts = search_facebook_with_fallback(
                    search_queries, text, max_posts=5
                )
                if fb_posts:
                    reported_by.extend(fb_posts)
                if reported_by:
                    reported_by = verify_same_event(main_claim, reported_by)
                result['reported_by'] = reported_by
    except Exception as e:
        print(f'[API FactCheck] Error: {e}')

    return jsonify(result)
