import json
import os
from datetime import datetime

from flask import (
    Blueprint, render_template, request, redirect,
    url_for, flash, jsonify, Response, stream_with_context,
)
from flask_login import login_required, current_user
from sqlalchemy.exc import IntegrityError

from models import db, Prediction, EvidenceAudit, EvidenceSource, ClaimRecord
from services.ml_service import MLService
from services.preprocessing import is_somali
from services.gemini_service import (
    extract_main_claim, generate_search_queries, semantic_match,
    verify_same_event, is_political,
)
from search.source_search import search_somali_sources_with_fallback, search_web_everywhere_with_fallback
from search.facebook_search import search_facebook_with_fallback
from services.claim_service import claim_fingerprint, find_similar_claim, normalize_claim
from services.ensemble_service import run_ensemble
from services.evidence_service import retrieve_evidence


predict_bp = Blueprint('predict', __name__)


def _previous_claim_payload(record, match_type, similarity=None):
    prediction = record.first_prediction
    audit = record.investigation
    review = audit.fact_check_review if audit else None
    return {
        'match_type': match_type,
        'similarity': similarity,
        'claim_text': audit.main_claim if audit and audit.main_claim else record.original_text,
        'prediction': prediction.prediction if prediction else (
            audit.predicted_label if audit else 'Pending'
        ),
        'confidence': prediction.confidence if prediction else None,
        'fact_checker_verdict': review.decision if review else 'Awaiting fact-checker review',
        'reviewed_at': review.updated_at.isoformat() if review else None,
        'evidence_sources': len(audit.sources) if audit else 0,
        'submission_count': record.submission_count,
        'investigation_status': audit.status if audit else 'pending',
        'review_status': audit.review_status if audit else 'unassigned',
        'investigation_url': (
            url_for('predict.previous_investigation', audit_id=audit.id)
            if audit else None
        ),
    }


def _increment_claim_spread(record):
    ClaimRecord.query.filter(ClaimRecord.id == record.id).update({
        ClaimRecord.submission_count: ClaimRecord.submission_count + 1,
        ClaimRecord.last_submitted_at: datetime.utcnow(),
    }, synchronize_session=False)
    db.session.commit()
    refreshed = db.session.get(ClaimRecord, record.id)
    db.session.refresh(refreshed)
    return refreshed


@predict_bp.route('/predict', methods=['GET', 'POST'])
@login_required
def index():
    ml = MLService()
    result = None
    model_key = 'sombertb'
    text_value = ''
    main_claim = None
    similar_content = []
    search_queries = []
    reported_by = []
    if request.method == 'POST':
        text_value = request.form.get('text', '').strip()
        model_key = request.form.get('model', 'sombertb')
        if not text_value:
            flash('Please enter some text.', 'warning')
        elif not is_somali(text_value):
            flash('Text must be in Somali.', 'warning')
        else:
            political_scope = is_political(text_value)
            if political_scope is None:
                flash('The LLM political-scope check is unavailable. Please try again later.', 'danger')
            elif political_scope is False:
                flash('Qoraalkani ma khuseeyo siyaasad ama maamul dowladeed.', 'warning')
            else:
                prediction, proba = ml.predict(text_value, model_key)
                if prediction is not None:
                    result = ml.build_result(prediction, proba, model_key)
                    db.session.add(Prediction(
                        user_id=current_user.id, text=text_value[:1000],
                        prediction=result['prediction'], confidence=result['confidence'],
                        model_used=model_key,
                    ))
                    db.session.commit()
                else:
                    flash('Selected model is not available.', 'danger')
    return render_template('predict/index.html', result=result, model_key=model_key,
                           text_value=text_value, models=ml.get_model_list(),
                           main_claim=main_claim, similar_content=similar_content,
                           search_queries=search_queries, reported_by=reported_by)


@predict_bp.route('/investigation/<int:audit_id>')
@login_required
def previous_investigation(audit_id):
    audit = EvidenceAudit.query.get_or_404(audit_id)
    if audit.status not in ('complete', 'failed'):
        flash('This previous investigation is still being prepared.', 'info')
        return redirect(url_for('predict.index'))
    sources = EvidenceSource.query.filter_by(audit_id=audit.id).order_by(
        EvidenceSource.similarity_score.desc()
    ).all()
    try:
        queries = json.loads(audit.queries_json or '[]')
    except (TypeError, ValueError):
        queries = []
    return render_template(
        'history/evidence_detail.html', audit=audit, sources=sources,
        queries=queries, previous_view=True,
    )


@predict_bp.route('/api/predict', methods=['POST'])
@login_required
def api_predict():
    data = request.get_json() or {}
    text = (data.get('text') or '').strip()
    model_key = data.get('model', 'sombertb')
    prediction_mode = data.get('mode', 'ensemble')
    if prediction_mode not in ('single', 'ensemble'):
        return jsonify({'error': 'Select a valid prediction mode.'}), 400

    if not text:
        return jsonify({'error': 'Please enter some text.'}), 400
    if not is_somali(text):
        return jsonify({'error': 'Text must be in Somali.'}), 400

    fingerprint = claim_fingerprint(text)
    exact_record = ClaimRecord.query.filter_by(fingerprint=fingerprint).first()
    if exact_record:
        exact_record = _increment_claim_spread(exact_record)
        return jsonify({
            'duplicate_match': 'exact',
            'reuse_previous': True,
            'text': text,
            'previous_claim': _previous_claim_payload(exact_record, 'exact'),
        })

    recent_claims = (
        ClaimRecord.query
        .filter(ClaimRecord.first_prediction_id.isnot(None))
        .order_by(ClaimRecord.last_submitted_at.desc())
        .limit(250)
        .all()
    )
    similar_record, similar_score = find_similar_claim(text, recent_claims)

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
            'model': 'All Models - Ensemble' if prediction_mode == 'ensemble' else model_key,
            'badge_color': '#64748b',
            'analysis': 'Qoraalkani ma khuseeyo siyaasad ama maamul dowladeed. Nidaamkani wuxuu falanqeeyaa oo keliya xogta siyaasadeed.',
            'text': text,
            'model_key': 'ensemble' if prediction_mode == 'ensemble' else model_key,
            'not_political': True,
        })

    ml = MLService()
    ensemble_result = None
    if prediction_mode == 'ensemble':
        ensemble_result = run_ensemble(ml, text)
        if ensemble_result is None:
            return jsonify({
                'error': 'Ensemble prediction requires at least two available models.'
            }), 503
        model_key = 'ensemble'
        result = {
            'prediction': ensemble_result['prediction'],
            'confidence': ensemble_result['average_confidence'],
            'model': 'All Models - Ensemble',
            'badge_color': ensemble_result['badge_color'],
        }
    else:
        prediction, proba = ml.predict(text, model_key)
        if prediction is None:
            return jsonify({'error': 'Prediction failed. Please try again.'}), 500
        result = ml.build_result(prediction, proba, model_key)

    prediction_record = Prediction(
        user_id=current_user.id, text=text[:1000],
        prediction=result['prediction'], confidence=result['confidence'],
        model_used=model_key,
    )
    claim_record = ClaimRecord(
        fingerprint=fingerprint,
        normalized_text=normalize_claim(text),
        original_text=text[:5000],
        first_prediction=prediction_record,
        submission_count=1,
        first_submitted_at=datetime.utcnow(),
        last_submitted_at=datetime.utcnow(),
    )
    db.session.add_all([prediction_record, claim_record])
    try:
        db.session.commit()
    except IntegrityError:
        # A concurrent request registered the same exact claim first.
        db.session.rollback()
        exact_record = ClaimRecord.query.filter_by(fingerprint=fingerprint).first()
        if exact_record:
            exact_record = _increment_claim_spread(exact_record)
            return jsonify({
                'duplicate_match': 'exact',
                'reuse_previous': True,
                'text': text,
                'previous_claim': _previous_claim_payload(exact_record, 'exact'),
            })
        raise

    pred_lower = result['prediction'].lower()
    if ensemble_result:
        analysis = (
            f"The final ensemble prediction is {result['prediction']}. The average "
            f"winning confidence is {ensemble_result['average_confidence']:.1f}%. "
            "This score summarizes the models that selected the final class and is not a fact-checker verdict."
        )
        if ensemble_result.get('tie_break'):
            analysis += ' ' + ensemble_result['tie_break']
    elif pred_lower == 'reliable':
        analysis = 'Moodalku wuxuu qoraalkan u kala saaray Reliable isagoo ku salaynaya qaababka luqadeed ee uu ka bartay xogta tababarka. Tani ma aha xaqiijin kama dambays ah; ilaha la helay iyo qiimaynta fact-checker-ka waa in si gaar ah loo eegaa.'
    elif pred_lower == 'misinformation':
        analysis = 'Moodalku wuxuu qoraalkan u kala saaray Misinformation isagoo ku salaynaya qaababka luqadeed ee uu ka bartay xogta tababarka. Saadaashu kaligeed ma caddaynayso in sheegashadu been tahay; ilaha iyo go\'aanka fact-checker-ka ayaa loo baahan yahay.'
    else:
        analysis = 'Moodalku wuxuu qoraalkan u kala saaray Misleading isagoo ku salaynaya qaababka luqadeed ee uu ka bartay xogta tababarka. Macnaha guud, taariikhda, ilaha, iyo go\'aanka fact-checker-ka waa in la hubiyaa ka hor xukun kama dambays ah.'

    response_payload = {
        'prediction': result['prediction'],
        'confidence': result['confidence'],
        'model': result['model'],
        'badge_color': result['badge_color'],
        'analysis': analysis,
        'text': text,
        'model_key': model_key,
        'prediction_id': prediction_record.id,
    }
    if ensemble_result:
        response_payload['prediction_mode'] = 'ensemble'
        response_payload['ensemble'] = ensemble_result
    if similar_record:
        response_payload['duplicate_match'] = 'similar'
        response_payload['previous_claim'] = _previous_claim_payload(
            similar_record, 'similar', similar_score
        )
    return jsonify(response_payload)


# ═══════════════════════════════════════════════════════════════
# API — Fact-check SSE (streaming steps to frontend)
# ═══════════════════════════════════════════════════════════════


def _legacy_api_fact_check():
    """Deprecated, unregistered implementation retained temporarily for reference."""
    data = request.get_json() or {}
    text = (data.get('text') or '').strip()
    prediction = data.get('prediction', '')
    model_key = data.get('model_key', 'sombertb')
    ai_explanation = (data.get('analysis') or '').strip()[:2000]

    def sse(event, payload):
        return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"

    def stream():
        try:
            # Step 1: Extract main claim
            yield sse('step', {'step': 1, 'name': 'extract_claim', 'status': 'running'})
            main_claim = extract_main_claim(text)
            if main_claim:
                yield sse('claim', {'claim': main_claim})
                yield sse('step', {'step': 1, 'name': 'extract_claim', 'status': 'done'})
            else:
                yield sse('step', {'step': 1, 'name': 'extract_claim', 'status': 'done'})
                yield sse('log', {'message': 'Could not extract claim, using original text'})
                main_claim = text[:200]

            # Step 2: Generate search queries
            yield sse('step', {'step': 2, 'name': 'generate_queries', 'status': 'running'})
            search_queries = generate_search_queries(text, main_claim)
            yield sse('queries', {'queries': search_queries})
            yield sse('step', {'step': 2, 'name': 'generate_queries', 'status': 'done'})

            reported_by = []
            similar_content = []

            if prediction == 'Reliable':
                # Step 3: Search sources + Facebook + YouTube in parallel
                yield sse('step', {'step': 3, 'name': 'search_sources', 'status': 'running'})
                yield sse('step', {'step': 4, 'name': 'search_facebook', 'status': 'running'})
                yield sse('step', {'step': 5, 'name': 'search_youtube', 'status': 'running'})

                from concurrent.futures import ThreadPoolExecutor, as_completed
                from search.youtube_search import search_youtube_with_fallback
                # Use original text as primary query (deterministic) — use more text
                primary_query = text[:300]
                with ThreadPoolExecutor(max_workers=3) as pool:
                    f_src = pool.submit(search_somali_sources_with_fallback, primary_query, text)
                    f_src2 = pool.submit(search_somali_sources_with_fallback, text[:300], text)
                    f_fb = pool.submit(search_facebook_with_fallback, search_queries, text, max_posts=5)
                    f_yt = pool.submit(search_youtube_with_fallback, search_queries, text, max_results=5)
                    reported_by = f_src.result()
                    reported_by2 = f_src2.result()
                    fb_posts = f_fb.result()
                    yt_posts = f_yt.result()

                seen_urls = set()
                merged = []
                for item in reported_by + reported_by2:
                    url = item.get('url', '') or item.get('first_result', {}).get('url', '')
                    if url and url not in seen_urls:
                        seen_urls.add(url)
                        merged.append(item)
                reported_by = merged

                if fb_posts:
                    for p in fb_posts:
                        reported_by.append(p)
                if yt_posts:
                    for p in yt_posts:
                        url = p.get('href', '')
                        if url and url not in seen_urls:
                            seen_urls.add(url)
                            reported_by.append({
                                'title': p.get('title', ''),
                                'snippet': p.get('body', ''),
                                'url': url,
                                'source': p.get('author', 'YouTube'),
                                'platform': 'YouTube',
                                'is_facebook': False,
                                'author': p.get('author', ''),
                                'date': p.get('date', ''),
                                'likes': 0,
                                'comments': 0,
                                'shares': 0,
                            })
                yield sse('step', {'step': 3, 'name': 'search_sources', 'status': 'done',
                                   'count': len(reported_by) - len(fb_posts) - len(yt_posts)})
                yield sse('step', {'step': 4, 'name': 'search_facebook', 'status': 'done',
                                   'count': len(fb_posts)})
                yield sse('step', {'step': 5, 'name': 'search_youtube', 'status': 'done',
                                   'count': len(yt_posts)})

                # Step 4: Verify sources
                yield sse('step', {'step': 5, 'name': 'verify_sources', 'status': 'running'})
                yield sse('step', {'step': 5, 'name': 'verify_sources', 'status': 'done',
                                   'count': len(reported_by)})

                # Step 6: Semantic match ALL items
                yield sse('step', {'step': 6, 'name': 'semantic_match', 'status': 'running'})
                match_items = []
                for item in reported_by:
                    match_items.append({
                        'title': item.get('title', '') or item.get('first_result', {}).get('title', ''),
                        'snippet': item.get('snippet', '') or item.get('first_result', {}).get('snippet', ''),
                        'url': item.get('url', '') or item.get('first_result', {}).get('url', ''),
                        'source': item.get('source', '') or item.get('name', 'Web'),
                        'platform': item.get('platform', ''),
                        'author': item.get('author', '') or item.get('source', ''),
                        'likes': item.get('likes', 0),
                        'comments': item.get('comments', 0),
                        'shares': item.get('shares', 0),
                        'is_facebook': item.get('is_facebook', False) or item.get('platform') == 'Facebook',
                        'credibility': item.get('credibility', ''),
                        'type': item.get('type', ''),
                        'date': '',
                    })
                if match_items:
                    similar_content = semantic_match(text, match_items[:15])
                else:
                    similar_content = []
                yield sse('step', {'step': 6, 'name': 'semantic_match', 'status': 'done',
                                   'count': len(similar_content)})

                # Sort by score descending, show all results
                similar_content.sort(key=lambda x: x.get('similarity', 0), reverse=True)
                scored = similar_content

                reported_data = []
                for item in scored:
                    is_fb = item.get('is_facebook', False)
                    reported_data.append({
                        'title': item.get('title', ''),
                        'url': item.get('url', ''),
                        'source': item.get('source', 'Web'),
                        'snippet': item.get('snippet', ''),
                        'is_facebook': is_fb,
                        'platform': item.get('platform', ''),
                        'author': item.get('author', ''),
                        'likes': item.get('likes', 0),
                        'comments': item.get('comments', 0),
                        'shares': item.get('shares', 0),
                        'credibility': item.get('credibility', ''),
                        'type': item.get('type', ''),
                        'similarity_score': item.get('similarity', 0),
                        'explanation': item.get('reason', ''),
                    })
                yield sse('reported_by', {'reported_by': reported_data})

            else:
                # Step 3: Search web + Facebook + YouTube (NO Somali sources for misinfo)
                yield sse('step', {'step': 3, 'name': 'search_web', 'status': 'running'})
                yield sse('step', {'step': 4, 'name': 'search_facebook', 'status': 'running'})
                yield sse('step', {'step': 5, 'name': 'search_youtube', 'status': 'running'})

                from concurrent.futures import ThreadPoolExecutor, as_completed
                from search.youtube_search import search_youtube_with_fallback
                with ThreadPoolExecutor(max_workers=3) as pool:
                    f_web = pool.submit(search_web_everywhere_with_fallback, search_queries, text, num_results=5)
                    f_fb = pool.submit(search_facebook_with_fallback, search_queries, text, max_posts=5)
                    f_yt = pool.submit(search_youtube_with_fallback, search_queries, text, max_results=5)
                    web_results = f_web.result()
                    fb_posts = f_fb.result()
                    yt_posts = f_yt.result()

                # Merge: web first, then YouTube, then Facebook
                all_results = []
                seen_urls = set()
                for r in web_results:
                    url = r.get('url', '')
                    if url and url not in seen_urls:
                        seen_urls.add(url)
                        all_results.append(r)
                for p in yt_posts:
                    url = p.get('href', '')
                    if url and url not in seen_urls:
                        seen_urls.add(url)
                        all_results.append({
                            'title': p.get('title', ''),
                            'snippet': p.get('body', ''),
                            'url': url,
                            'source': p.get('author', 'YouTube'),
                            'platform': 'YouTube',
                            'is_facebook': False,
                            'author': p.get('author', ''),
                            'date': p.get('date', ''),
                            'likes': 0,
                            'comments': 0,
                            'shares': 0,
                        })
                for p in fb_posts:
                    all_results.append(p)

                yield sse('step', {'step': 3, 'name': 'search_web', 'status': 'done',
                                   'count': len(web_results)})
                yield sse('step', {'step': 4, 'name': 'search_facebook', 'status': 'done',
                                   'count': len(fb_posts)})
                yield sse('step', {'step': 5, 'name': 'search_youtube', 'status': 'done',
                                   'count': len(yt_posts)})

                # Step 6: Semantic match ALL items — YouTube first so it's not cut off
                yield sse('step', {'step': 6, 'name': 'semantic_match', 'status': 'running'})
                match_items = []
                # Add YouTube first (highest priority)
                for item in all_results:
                    if item.get('platform') == 'YouTube':
                        match_items.append({
                            'title': item.get('title', ''),
                            'snippet': item.get('snippet', ''),
                            'url': item.get('url', ''),
                            'source': item.get('source', 'YouTube'),
                            'platform': 'YouTube',
                            'author': item.get('author', ''),
                            'is_facebook': False,
                        })
                # Add others
                for item in all_results:
                    if item.get('platform') != 'YouTube':
                        match_items.append({
                            'title': item.get('title', '') or item.get('first_result', {}).get('title', ''),
                            'snippet': item.get('snippet', '') or item.get('first_result', {}).get('snippet', ''),
                            'url': item.get('url', '') or item.get('first_result', {}).get('url', ''),
                            'source': item.get('source', '') or item.get('name', 'Web'),
                            'platform': item.get('platform', ''),
                            'author': item.get('author', '') or item.get('source', ''),
                            'likes': item.get('likes', 0),
                            'comments': item.get('comments', 0),
                            'shares': item.get('shares', 0),
                            'is_facebook': item.get('is_facebook', False) or item.get('platform') == 'Facebook',
                            'credibility': item.get('credibility', ''),
                            'type': item.get('type', ''),
                            'date': '',
                        })
                if match_items:
                    similar_content = semantic_match(text, match_items[:15])
                else:
                    similar_content = []
                yield sse('step', {'step': 6, 'name': 'semantic_match', 'status': 'done',
                                   'count': len(similar_content)})

                # Sort by score descending, show all results
                similar_content.sort(key=lambda x: x.get('similarity', 0), reverse=True)
                scored = similar_content
                print(f'[FC Debug] Scored results: {len(scored)}')
                for s in scored[:5]:
                    print(f'  {s.get("platform","?")} | {s.get("similarity",0)}% | {s.get("title","")[:50]}')

                similar_data = []
                for item in scored:
                    similar_data.append({
                        'title': item.get('title', ''),
                        'url': item.get('url', ''),
                        'source': item.get('source', 'Web'),
                        'snippet': item.get('snippet', ''),
                        'date': item.get('date', ''),
                        'similarity_score': item.get('similarity', 0),
                        'verdict': item.get('verdict', ''),
                        'explanation': item.get('explanation', ''),
                        'is_facebook': item.get('is_facebook', False),
                        'author': item.get('author', ''),
                        'likes': item.get('likes', 0),
                        'comments': item.get('comments', 0),
                        'shares': item.get('shares', 0),
                    })
                yield sse('similar_content', {'similar_content': similar_data})

            yield sse('done', {'status': 'complete'})

        except Exception as e:
            import traceback
            traceback.print_exc()
            yield sse('error', {'message': str(e)})

    return Response(stream(), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


@predict_bp.route('/api/fact-check', methods=['POST'])
@login_required
def api_fact_check():
    """Retrieve related evidence for any authenticated user and save an audit trail."""
    data = request.get_json() or {}
    text = (data.get('text') or '').strip()
    prediction = data.get('prediction', '')
    model_key = data.get('model_key', 'sombertb')
    ai_explanation = (data.get('analysis') or '').strip()[:2000]
    if not text or prediction not in ('Reliable', 'Misinformation', 'Misleading'):
        return jsonify({'error': 'A valid prediction is required for evidence search.'}), 400

    prediction_record = None
    prediction_id = data.get('prediction_id')
    if prediction_id:
        prediction_record = Prediction.query.filter_by(
            id=prediction_id, user_id=current_user.id
        ).first()

    audit = EvidenceAudit(
        user_id=current_user.id,
        prediction_id=prediction_record.id if prediction_record else None,
        original_text=text,
        ai_explanation=ai_explanation,
        predicted_label=prediction,
        model_used=model_key,
        status='running',
    )
    db.session.add(audit)
    db.session.commit()
    audit_id = audit.id
    claim_record = ClaimRecord.query.filter_by(
        fingerprint=claim_fingerprint(text)
    ).first()
    if claim_record and claim_record.investigation_audit_id is None:
        claim_record.investigation_audit_id = audit.id
        db.session.commit()

    def sse(event, payload):
        return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"

    def stream():
        try:
            yield sse('step', {'step': 1, 'name': 'extract_claim', 'status': 'running'})
            main_claim = extract_main_claim(text)
            if main_claim:
                yield sse('claim', {'claim': main_claim})
            else:
                main_claim = text[:200]
            yield sse('step', {'step': 1, 'name': 'extract_claim', 'status': 'done'})

            yield sse('step', {'step': 2, 'name': 'generate_queries', 'status': 'running'})
            search_queries = generate_search_queries(text, main_claim)
            yield sse('queries', {'queries': search_queries})
            yield sse('step', {'step': 2, 'name': 'generate_queries', 'status': 'done'})

            yield sse('step', {'step': 3, 'name': 'search_sources', 'status': 'running'})
            yield sse('step', {'step': 4, 'name': 'search_facebook', 'status': 'running'})
            yield sse('step', {'step': 5, 'name': 'search_youtube', 'status': 'running'})
            yield sse('step', {'step': 6, 'name': 'semantic_match', 'status': 'running'})

            evidence, provider_counts = retrieve_evidence(
                text, main_claim, search_queries, predicted_label=prediction
            )

            trusted_count = sum(
                1 for item in evidence if item.get('evidence_kind') == 'trusted'
            )
            facebook_count = sum(
                1 for item in evidence if item.get('platform') == 'Facebook'
            )
            youtube_count = sum(
                1 for item in evidence if item.get('platform') == 'YouTube'
            )
            yield sse('step', {'step': 3, 'name': 'search_sources', 'status': 'done',
                               'count': trusted_count})
            yield sse('step', {'step': 4, 'name': 'search_facebook', 'status': 'done',
                               'count': facebook_count})
            yield sse('step', {'step': 5, 'name': 'search_youtube', 'status': 'done',
                               'count': youtube_count})
            yield sse('step', {'step': 6, 'name': 'semantic_match', 'status': 'done',
                               'count': len(evidence)})

            audit_record = db.session.get(EvidenceAudit, audit_id)
            audit_record.main_claim = main_claim
            audit_record.queries_json = json.dumps(search_queries, ensure_ascii=False)
            audit_record.status = 'complete'
            audit_record.completed_at = datetime.utcnow()
            for item in evidence:
                db.session.add(EvidenceSource(
                    audit_id=audit_id,
                    title=item.get('title', ''),
                    url=item.get('url', ''),
                    source=item.get('source', 'Web'),
                    platform=item.get('platform', 'Web'),
                    provider=item.get('provider', ''),
                    snippet=item.get('snippet', ''),
                    published_date=item.get('date', ''),
                    similarity_score=item.get('similarity_score', 0),
                    scoring_method=item.get('scoring_method', 'lexical_fallback'),
                    stance=item.get('stance', 'Unclear'),
                    evidence_kind=item.get('evidence_kind', 'general'),
                    credibility=item.get('credibility', ''),
                    explanation=item.get('explanation', ''),
                ))
            db.session.commit()

            if evidence:
                yield sse('evidence', {
                    'audit_id': audit_id,
                    'evidence': evidence,
                    'provider_counts': provider_counts,
                    'notice': 'Evidence audit saved. An admin can assign the case to a fact-checker for a final human decision.',
                })
            else:
                yield sse('empty', {
                    'audit_id': audit_id,
                    'message': 'No related public sources were retrieved. This does not prove the claim true or false.',
                })
            yield sse('done', {'status': 'complete', 'audit_id': audit_id})

        except Exception as exc:
            import traceback
            traceback.print_exc()
            db.session.rollback()
            try:
                audit_record = db.session.get(EvidenceAudit, audit_id)
                if audit_record:
                    audit_record.status = 'failed'
                    audit_record.error_message = str(exc)[:1000]
                    audit_record.completed_at = datetime.utcnow()
                    db.session.commit()
            except Exception:
                db.session.rollback()
            yield sse('error', {
                'message': 'Evidence search failed. The prediction remains available, but source retrieval could not be completed.',
                'audit_id': audit_id,
            })

    return Response(
        stream_with_context(stream()),
        mimetype='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'},
    )


@predict_bp.route('/export-report', methods=['POST'])
@login_required
def export_report():
    from flask import send_file as _send_file
    from services.pdf_report import generate_report

    data = request.get_json()
    if not data:
        return jsonify({'error': 'No data provided'}), 400

    try:
        filepath, filename = generate_report(
            prediction=data.get('prediction', 'Unknown'),
            confidence=data.get('confidence', 0),
            main_claim=data.get('main_claim', ''),
            search_queries=data.get('search_queries', []),
            similar_content=data.get('similar_content', []),
        )
        return _send_file(filepath, as_attachment=True, download_name=filename,
                         mimetype='application/pdf')
    except Exception as e:
        print(f'[PDF] Export error: {e}')
        return jsonify({'error': 'Failed to generate report'}), 500
