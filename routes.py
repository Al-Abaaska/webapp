"""
All Routes — Every page of the app.
Blueprints: main, auth, predict, batch, history, models_bp, profile, admin, api
"""
import json
import io
import uuid
import os
import threading
import tempfile
from datetime import datetime
from functools import wraps

from flask import (
    Blueprint, render_template, request, redirect,
    url_for, flash, jsonify, send_file, Response, stream_with_context,
    current_app
)
from flask_login import login_user, logout_user, login_required, current_user
import pandas as pd
from sqlalchemy.exc import IntegrityError

from models import (
    db, User, Prediction, OTP, BatchJob, EvidenceAudit, EvidenceSource,
    FactCheckReview, EvidenceAssignmentEvent, ClaimRecord
)
from ml_service import MLService, MODEL_NAMES, MODEL_CONFIG
from email_service import create_otp_record, verify_otp_code, send_otp_email
from preprocessing import is_somali
from gemini_service import extract_main_claim, generate_search_queries, semantic_match, verify_same_event
from source_search import search_somali_sources_with_fallback, search_web_everywhere_with_fallback
from facebook_search import search_facebook_with_fallback
from claim_service import claim_fingerprint, find_similar_claim, normalize_claim
from ensemble_service import run_ensemble


FACT_CHECK_DECISIONS = (
    'Confirmed Reliable',
    'Confirmed Misinformation',
    'Confirmed Misleading',
    'Insufficient Evidence',
)


# ═══════════════════════════════════════════════════════════════
# MAIN — Landing page + Dashboard
# ═══════════════════════════════════════════════════════════════

main_bp = Blueprint('main', __name__)


@main_bp.route('/')
def index():
    if current_user.is_authenticated:
        return redirect(url_for('main.dashboard'))
    return render_template('landing/index.html')


@main_bp.route('/dashboard')
@login_required
def dashboard():
    if current_user.is_admin:
        return redirect(url_for('admin.index'))
    if current_user.can_fact_check:
        return redirect(url_for('fact_checker.dashboard'))
    ml = MLService()
    uid = current_user.id

    total_predictions = Prediction.query.filter_by(user_id=uid).count()
    reliable = Prediction.query.filter_by(user_id=uid, prediction='Reliable').count()
    misinfo = Prediction.query.filter_by(user_id=uid, prediction='Misinformation').count()
    misleading = Prediction.query.filter_by(user_id=uid, prediction='Misleading').count()
    batch_count = BatchJob.query.filter_by(user_id=uid).count()
    recent = Prediction.query.filter_by(user_id=uid).order_by(Prediction.created_at.desc()).limit(5).all()

    model_usage = db.session.query(
        Prediction.model_used, db.func.count(Prediction.id)
    ).filter_by(user_id=uid).group_by(Prediction.model_used).all()

    return render_template('dashboard/index.html',
        total_predictions=total_predictions,
        models_available=ml.available_model_count,
        batch_count=batch_count,
        reliable=reliable, misinfo=misinfo, misleading=misleading,
        recent=recent,
        model_labels=json.dumps([MODEL_NAMES.get(m[0], m[0]) for m in model_usage]),
        model_values=json.dumps([m[1] for m in model_usage]),
        label_labels=json.dumps(['Reliable', 'Misinformation', 'Misleading']),
        label_values=json.dumps([reliable, misinfo, misleading]),
    )


# ═══════════════════════════════════════════════════════════════
# AUTH — Login, Register, OTP, Forgot Password
# ═══════════════════════════════════════════════════════════════

auth_bp = Blueprint('auth', __name__)


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('main.dashboard'))
    if request.method == 'POST':
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '')
        remember = request.form.get('remember', False)
        user = User.query.filter_by(email=email).first()
        if user and user.check_password(password):
            if user.is_blocked:
                flash('Your account has been blocked. Please contact support.', 'danger')
                return render_template('auth/login.html')
            if not user.email_verified and not user.is_admin:
                flash('Please verify your email first.', 'warning')
                return redirect(url_for('auth.verify_otp', email=user.email))
            login_user(user, remember=bool(remember))
            flash(f'Welcome back, {user.username}!', 'success')
            return redirect(url_for('main.dashboard'))
        flash('Invalid email or password.', 'danger')
    return render_template('auth/login.html')


@auth_bp.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('main.dashboard'))
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '')
        confirm = request.form.get('confirm_password', '')
        if len(username) < 3:
            flash('Username must be at least 3 characters.', 'danger')
            return render_template('auth/register.html')
        if len(password) < 6:
            flash('Password must be at least 6 characters.', 'danger')
            return render_template('auth/register.html')
        if password != confirm:
            flash('Passwords do not match.', 'danger')
            return render_template('auth/register.html')
        if User.query.filter_by(username=username).first():
            flash('Username already taken.', 'danger')
            return render_template('auth/register.html')
        if User.query.filter_by(email=email).first():
            flash('Email already registered.', 'danger')
            return render_template('auth/register.html')
        user = User(username=username, email=email, role='user')
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        try:
            otp_code = create_otp_record(email, 'verify')
            send_otp_email(email, username, otp_code, 'verify')
            flash('Account created! Check your email for the OTP code.', 'success')
        except Exception as exc:
            current_app.logger.exception(
                'OTP email failed during registration (%s)', type(exc).__name__
            )
            flash('Account created but email could not be sent. Contact admin.', 'warning')
        return redirect(url_for('auth.verify_otp', email=email))
    return render_template('auth/register.html')


@auth_bp.route('/verify-otp', methods=['GET', 'POST'])
def verify_otp():
    email = request.args.get('email', '') if request.method == 'GET' else request.form.get('email', '').strip()
    if not email:
        flash('Email address is required.', 'danger')
        return redirect(url_for('auth.login'))
    if request.method == 'POST':
        code = request.form.get('otp', '').strip()
        success, msg = verify_otp_code(email, code, 'verify')
        if success:
            user = User.query.filter_by(email=email).first()
            if user:
                user.email_verified = True
                db.session.commit()
            flash('Email verified! You can now log in.', 'success')
            return redirect(url_for('auth.login'))
        flash(msg, 'danger')
    return render_template('auth/verify_otp.html', email=email)


@auth_bp.route('/resend-otp', methods=['POST'])
def resend_otp():
    email = request.form.get('email', '').strip()
    purpose = request.form.get('purpose', 'verify').strip()
    if not email:
        flash('Email is required.', 'danger')
        return redirect(url_for('auth.login'))
    user = User.query.filter_by(email=email).first()
    if not user:
        flash('No account found with that email.', 'danger')
        return redirect(url_for('auth.login'))
    if purpose == 'verify' and user.email_verified:
        flash('Email already verified. Please log in.', 'info')
        return redirect(url_for('auth.login'))
    try:
        otp_code = create_otp_record(email, purpose)
        send_otp_email(email, user.username, otp_code, purpose)
        flash('OTP code sent! Check your inbox.', 'success')
    except Exception as exc:
        current_app.logger.exception(
            'OTP email failed during resend (%s)', type(exc).__name__
        )
        flash('Email service is temporarily unavailable. Please try again.', 'danger')
    if purpose == 'verify':
        return redirect(url_for('auth.verify_otp', email=email))
    return redirect(url_for('auth.reset_password', email=email))


@auth_bp.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        email = request.form.get('email', '').strip()
        user = User.query.filter_by(email=email).first()
        if not user:
            flash('No account found with that email.', 'danger')
            return render_template('auth/forgot_password.html')
        try:
            otp_code = create_otp_record(email, 'reset')
            send_otp_email(email, user.username, otp_code, 'reset')
            flash('OTP code sent! Check your inbox.', 'success')
        except Exception as exc:
            current_app.logger.exception(
                'OTP email failed during password reset (%s)', type(exc).__name__
            )
            flash('Email service is temporarily unavailable. Please try again.', 'danger')
        return redirect(url_for('auth.reset_password', email=email))
    return render_template('auth/forgot_password.html')


@auth_bp.route('/reset-password', methods=['GET', 'POST'])
def reset_password():
    email = request.args.get('email', '') if request.method == 'GET' else request.form.get('email', '').strip()
    if not email:
        flash('Email is required.', 'danger')
        return redirect(url_for('auth.forgot_password'))
    if request.method == 'POST':
        code = request.form.get('otp', '').strip()
        new_password = request.form.get('new_password', '')
        confirm_password = request.form.get('confirm_password', '')
        if not new_password or len(new_password) < 6:
            flash('Password must be at least 6 characters.', 'danger')
            return render_template('auth/reset_password.html', email=email)
        if new_password != confirm_password:
            flash('Passwords do not match.', 'danger')
            return render_template('auth/reset_password.html', email=email)
        success, msg = verify_otp_code(email, code, 'reset')
        if not success:
            flash(msg, 'danger')
            return render_template('auth/reset_password.html', email=email)
        user = User.query.filter_by(email=email).first()
        if not user:
            flash('User not found.', 'danger')
            return redirect(url_for('auth.login'))
        user.set_password(new_password)
        db.session.commit()
        flash('Password reset successful!', 'success')
        return redirect(url_for('auth.login'))
    return render_template('auth/reset_password.html', email=email)

@auth_bp.route('/logout')
@login_required
def logout():
    logout_user()
    flash('Logged out successfully.', 'info')
    return redirect(url_for('main.index'))


# ═══════════════════════════════════════════════════════════════
# PREDICT — Single text prediction
# ═══════════════════════════════════════════════════════════════

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
            from gemini_service import is_political
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
    prediction_mode = data.get('mode', 'single')
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

    from gemini_service import is_political
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
            f"{ensemble_result['winning_count']} of {ensemble_result['total_models']} "
            f"models voted {result['prediction']}. Model agreement is "
            f"{ensemble_result['agreement']:.1f}%, while the average confidence "
            f"of models voting for the final class is "
            f"{ensemble_result['average_confidence']:.1f}%. These are separate measures."
        )
        if ensemble_result.get('tie_break'):
            analysis += ' ' + ensemble_result['tie_break']
    elif pred_lower == 'reliable':
        analysis = 'Moodalku wuxuu qoraalkan u kala saaray Reliable isagoo ku salaynaya qaababka luqadeed ee uu ka bartay xogta tababarka. Tani ma aha xaqiijin kama dambays ah; ilaha la helay iyo qiimaynta fact-checker-ka waa in si gaar ah loo eegaa.'
    elif pred_lower == 'misinformation':
        analysis = 'Moodalku wuxuu qoraalkan u kala saaray Misinformation isagoo ku salaynaya qaababka luqadeed ee uu ka bartay xogta tababarka. Saadaashu kaligeed ma caddaynayso in sheegashadu been tahay; ilaha iyo go’aanka fact-checker-ka ayaa loo baahan yahay.'
    else:
        analysis = 'Moodalku wuxuu qoraalkan u kala saaray Misleading isagoo ku salaynaya qaababka luqadeed ee uu ka bartay xogta tababarka. Macnaha guud, taariikhda, ilaha, iyo go’aanka fact-checker-ka waa in la hubiyaa ka hor xukun kama dambays ah.'

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
                from youtube_search import search_youtube_with_fallback
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
                from youtube_search import search_youtube_with_fallback
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

            from evidence_service import retrieve_evidence
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


# ═══════════════════════════════════════════════════════════════
# BATCH — Batch prediction from file
# ═══════════════════════════════════════════════════════════════

batch_bp = Blueprint('batch', __name__)


@batch_bp.route('/batch', methods=['GET', 'POST'])
@login_required
def index():
    ml = MLService()
    columns = None
    results = None
    if request.method == 'POST':
        file = request.files.get('file')
        column = request.form.get('column', '').strip()
        model_key = request.form.get('model', 'svm')
        export_format = request.form.get('format', 'xlsx')

        if not file or not file.filename:
            flash('Please upload a file.', 'warning')
            return render_template('batch/index.html', columns=columns, results=results)

        filename = file.filename.lower()
        if not filename.endswith(('.csv', '.xlsx', '.xls')):
            flash('Please upload a CSV or Excel file.', 'warning')
            return render_template('batch/index.html', columns=columns, results=results)

        try:
            df = pd.read_csv(file) if filename.endswith('.csv') else pd.read_excel(file)
            columns = df.columns.tolist()
        except Exception as e:
            flash(f'Error reading file: {e}', 'danger')
            return render_template('batch/index.html', columns=columns, results=results)

        if not column or column not in df.columns:
            flash(f'Column "{column}" not found.', 'danger')
            return render_template('batch/index.html', columns=columns, results=results)

        if model_key not in ml.models and model_key not in ml.transformers:
            flash('Selected model not available.', 'danger')
            return render_template('batch/index.html', columns=columns, results=results)

        results = []
        for text in df[column].astype(str):
            if is_somali(str(text)):
                pred, proba = ml.predict(str(text), model_key)
                if pred is not None:
                    r = ml.build_result(pred, proba, model_key)
                    results.append({'text': str(text)[:200], 'prediction': r['prediction'], 'confidence': r['confidence']})
                else:
                    results.append({'text': str(text)[:200], 'prediction': 'Error', 'confidence': 0})
            else:
                results.append({'text': str(text)[:200], 'prediction': 'Not Somali', 'confidence': 0})

        db.session.add(BatchJob(user_id=current_user.id, filename=file.filename, total_rows=len(results), status='completed'))
        for r in results:
            db.session.add(Prediction(user_id=current_user.id, text=r['text'], prediction=r['prediction'], confidence=r['confidence'], model_used=model_key))
        db.session.commit()

        out_df = df.copy()
        out_df['Prediction'] = [r['prediction'] for r in results]
        out_df['Confidence'] = [r['confidence'] for r in results]
        output = io.BytesIO()
        ext = 'csv' if export_format == 'csv' else 'xlsx'
        if ext == 'csv':
            out_df.to_csv(output, index=False)
            mimetype = 'text/csv'
        else:
            out_df.to_excel(output, index=False, engine='openpyxl')
            mimetype = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        output.seek(0)
        flash(f'Batch complete! {len(results)} rows processed.', 'success')
        return send_file(output, as_attachment=True,
                         download_name=f'predictions_{uuid.uuid4().hex[:8]}.{ext}',
                         mimetype=mimetype)

    return render_template('batch/index.html', columns=columns, results=results, model_key=request.form.get('model', 'svm'), models=ml.get_model_list())


def _process_batch(job_id, file_path, column, model_key, export_format):
    """Background thread: process each row and update progress."""
    from app import app as flask_app
    with flask_app.app_context():
        job = BatchJob.query.get(job_id)
        if not job:
            return
        job.status = 'processing'
        db.session.commit()

        try:
            if file_path.endswith('.csv'):
                df = pd.read_csv(file_path)
            else:
                df = pd.read_excel(file_path)

            ml = MLService()
            results = []
            total = len(df)
            job.total_rows = total
            db.session.commit()

            for i, text in enumerate(df[column].astype(str)):
                if is_somali(str(text)):
                    pred, proba = ml.predict(str(text), model_key)
                    if pred is not None:
                        r = ml.build_result(pred, proba, model_key)
                        results.append({'text': str(text)[:200], 'prediction': r['prediction'], 'confidence': r['confidence']})
                    else:
                        results.append({'text': str(text)[:200], 'prediction': 'Error', 'confidence': 0})
                else:
                    results.append({'text': str(text)[:200], 'prediction': 'Not Somali', 'confidence': 0})

                job.processed_rows = i + 1
                if (i + 1) % 5 == 0 or i + 1 == total:
                    db.session.commit()

            for r in results:
                db.session.add(Prediction(user_id=job.user_id, text=r['text'], prediction=r['prediction'], confidence=r['confidence'], model_used=model_key))

            out_df = df.copy()
            out_df['Prediction'] = [r['prediction'] for r in results]
            out_df['Confidence'] = [r['confidence'] for r in results]
            ext = 'csv' if export_format == 'csv' else 'xlsx'
            result_path = os.path.join(tempfile.gettempdir(), f'batch_{job_id}_{uuid.uuid4().hex[:8]}.{ext}')
            if ext == 'csv':
                out_df.to_csv(result_path, index=False)
            else:
                out_df.to_excel(result_path, index=False, engine='openpyxl')

            job.result_path = result_path
            job.status = 'completed'
            db.session.commit()
        except Exception as e:
            job.status = 'error'
            db.session.commit()


@batch_bp.route('/batch/upload', methods=['POST'])
@login_required
def upload():
    file = request.files.get('file')
    column = request.form.get('column', '').strip()
    model_key = request.form.get('model', 'svm')
    export_format = request.form.get('format', 'xlsx')

    if not file or not file.filename:
        return jsonify({'error': 'No file provided'}), 400

    filename = file.filename.lower()
    if not filename.endswith(('.csv', '.xlsx', '.xls')):
        return jsonify({'error': 'Invalid file type'}), 400

    ml = MLService()
    if model_key not in ml.models and model_key not in ml.transformers:
        return jsonify({'error': 'Model not available'}), 400

    temp_dir = tempfile.gettempdir()
    file_path = os.path.join(temp_dir, f'upload_{uuid.uuid4().hex[:8]}_{file.filename}')
    file.save(file_path)

    try:
        if file_path.endswith('.csv'):
            df = pd.read_csv(file_path)
        else:
            df = pd.read_excel(file_path)
    except Exception as e:
        return jsonify({'error': f'Cannot read file: {e}'}), 400

    if not column or column not in df.columns:
        return jsonify({'error': f'Column "{column}" not found'}), 400

    job = BatchJob(user_id=current_user.id, filename=file.filename, total_rows=len(df), status='queued')
    db.session.add(job)
    db.session.commit()

    thread = threading.Thread(target=_process_batch, args=(job.id, file_path, column, model_key, export_format))
    thread.daemon = True
    thread.start()

    return jsonify({'job_id': job.id, 'total_rows': len(df)})


@batch_bp.route('/batch/status/<int:job_id>')
@login_required
def status(job_id):
    job = BatchJob.query.get_or_404(job_id)
    if job.user_id != current_user.id:
        return jsonify({'error': 'Unauthorized'}), 403
    return jsonify({
        'status': job.status,
        'total': job.total_rows,
        'processed': job.processed_rows,
        'progress': round((job.processed_rows / job.total_rows) * 100) if job.total_rows > 0 else 0,
    })


@batch_bp.route('/batch/download/<int:job_id>')
@login_required
def download(job_id):
    job = BatchJob.query.get_or_404(job_id)
    if job.user_id != current_user.id:
        return jsonify({'error': 'Unauthorized'}), 403
    if job.status != 'completed' or not job.result_path or not os.path.exists(job.result_path):
        return jsonify({'error': 'File not ready'}), 400

    ext = os.path.splitext(job.result_path)[1].lstrip('.')
    mimetype = 'text/csv' if ext == 'csv' else 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    return send_file(job.result_path, as_attachment=True,
                     download_name=f'predictions_{uuid.uuid4().hex[:8]}.{ext}',
                     mimetype=mimetype)


# ═══════════════════════════════════════════════════════════════
# HISTORY — View, filter, delete, export
# ═══════════════════════════════════════════════════════════════

history_bp = Blueprint('history', __name__)


@history_bp.route('/history')
@login_required
def index():
    uid = current_user.id
    page = request.args.get('page', 1, type=int)
    model_filter = request.args.get('model', '')
    label_filter = request.args.get('label', '')
    search = request.args.get('search', '')

    query = Prediction.query.filter_by(user_id=uid)
    if model_filter:
        query = query.filter(Prediction.model_used == model_filter)
    if label_filter:
        query = query.filter(Prediction.prediction == label_filter)
    if search:
        query = query.filter(Prediction.text.contains(search))

    predictions = query.order_by(Prediction.created_at.desc()).paginate(page=page, per_page=15, error_out=False)
    total = query.count()
    return render_template('history/index.html', predictions=predictions, total=total,
                           page=page, model_filter=model_filter, label_filter=label_filter,
                           search=search, MODEL_NAMES=MODEL_NAMES)


@history_bp.route('/history/evidence/<int:audit_id>')
@login_required
def evidence_detail(audit_id):
    audit = EvidenceAudit.query.filter_by(
        id=audit_id, user_id=current_user.id
    ).first_or_404()
    sources = EvidenceSource.query.filter_by(audit_id=audit.id).order_by(
        EvidenceSource.similarity_score.desc()
    ).all()
    queries = json.loads(audit.queries_json or '[]')
    return render_template(
        'history/evidence_detail.html', audit=audit, sources=sources, queries=queries
    )


@history_bp.route('/history/delete/<int:pred_id>', methods=['POST'])
@login_required
def delete_prediction(pred_id):
    pred = Prediction.query.get_or_404(pred_id)
    if pred.user_id != current_user.id:
        flash('Unauthorized.', 'danger')
        return redirect(url_for('history.index'))
    db.session.delete(pred)
    db.session.commit()
    flash('Prediction deleted.', 'success')
    return redirect(url_for('history.index', page=request.args.get('page', 1),
                       model=request.args.get('model', ''), label=request.args.get('label', ''),
                       search=request.args.get('search', '')))


@history_bp.route('/history/delete-selected', methods=['POST'])
@login_required
def delete_selected():
    ids = request.form.getlist('selected_ids')
    if not ids:
        flash('No predictions selected.', 'warning')
        return redirect(url_for('history.index'))
    Prediction.query.filter(Prediction.id.in_(ids), Prediction.user_id == current_user.id).delete(synchronize_session=False)
    db.session.commit()
    flash(f'{len(ids)} prediction(s) deleted.', 'success')
    return redirect(url_for('history.index', page=request.args.get('page', 1),
                       model=request.args.get('model', ''), label=request.args.get('label', ''),
                       search=request.args.get('search', '')))


@history_bp.route('/history/delete-all', methods=['POST'])
@login_required
def delete_all():
    count = Prediction.query.filter_by(user_id=current_user.id).delete()
    db.session.commit()
    flash(f'All {count} predictions deleted.', 'success')
    return redirect(url_for('history.index'))


@history_bp.route('/history/export')
@login_required
def export():
    predictions = Prediction.query.filter_by(user_id=current_user.id).order_by(Prediction.created_at.desc()).all()
    data = [{
        'ID': p.id, 'Text': p.text, 'Prediction': p.prediction,
        'Confidence': f'{p.confidence:.2f}%', 'Model': MODEL_NAMES.get(p.model_used, p.model_used),
        'Date': p.created_at.strftime('%Y-%m-%d %H:%M'),
    } for p in predictions]
    df = pd.DataFrame(data) if data else pd.DataFrame(columns=['ID', 'Text', 'Prediction', 'Confidence', 'Model', 'Date'])
    output = io.BytesIO()
    df.to_excel(output, index=False, engine='openpyxl')
    output.seek(0)
    return send_file(output, as_attachment=True, download_name='history_export.xlsx',
                     mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')


# ═══════════════════════════════════════════════════════════════
# MODELS — Compare all ML models
# ═══════════════════════════════════════════════════════════════

models_bp = Blueprint('models_bp', __name__)


@models_bp.route('/models', methods=['GET', 'POST'])
@login_required
def index():
    import time
    from collections import Counter
    ml = MLService()
    text = request.form.get('text', '') if request.method == 'POST' else request.args.get('text', '')
    text = text.strip()
    results = {}
    consensus = None
    distribution = {}
    agreement_pct = 0
    high_disagreement = False

    if text and is_somali(text):
        for key in list(ml.models.keys()) + list(ml.transformers.keys()) + list(ml.keras_models.keys()):
            start = time.time()
            pred, proba = ml.predict(text, key)
            elapsed = round((time.time() - start) * 1000, 1)
            if pred is not None:
                r = ml.build_result(pred, proba, key)
                config = MODEL_CONFIG.get(key, {})
                r['type'] = config.get('type', 'unknown')
                r['f1'] = config.get('f1', 0)
                r['elapsed_ms'] = elapsed
                r['key'] = key
                results[key] = r

        if results:
            labels = [r['prediction'] for r in results.values()]
            counter = Counter(labels)
            distribution = dict(counter)
            total = len(labels)
            most_common_label, most_common_count = counter.most_common(1)[0]
            agreement_pct = round(most_common_count / total * 100, 1)
            consensus = {
                'label': most_common_label,
                'agreement': agreement_pct,
                'count': most_common_count,
                'total': total,
            }
            high_disagreement = agreement_pct < 60 and total >= 3

        # Group by type for template
        groups = [
            ('Traditional ML', 'fas fa-calculator', []),
            ('Transformers', 'fas fa-brain', []),
            ('Deep Learning (Keras)', 'fas fa-layer-group', []),
        ]
        type_index = {'traditional': 0, 'transformer': 1, 'keras': 2}
        for key, r in results.items():
            idx = type_index.get(r.get('type', ''), 0)
            groups[idx][2].append((key, r))
        groups = [(name, icon, items) for name, icon, items in groups if items]

    return render_template('models/index.html',
        text=text, results=results, consensus=consensus,
        distribution=distribution, agreement_pct=agreement_pct,
        high_disagreement=high_disagreement, groups=groups if text and results else [],
    )


# ═══════════════════════════════════════════════════════════════
# PROFILE — View and update account
# ═══════════════════════════════════════════════════════════════

profile_bp = Blueprint('profile', __name__)


@profile_bp.route('/profile', methods=['GET', 'POST'])
@login_required
def index():
    if request.method == 'POST':
        email = request.form.get('email', '').strip()
        current_password = request.form.get('current_password', '')
        new_password = request.form.get('new_password', '')
        if email and email != current_user.email:
            if User.query.filter_by(email=email).first():
                flash('Email already in use.', 'danger')
                return redirect(url_for('profile.index'))
            current_user.email = email
        if new_password:
            if not current_user.check_password(current_password):
                flash('Current password is incorrect.', 'danger')
                return redirect(url_for('profile.index'))
            if len(new_password) < 6:
                flash('New password must be at least 6 characters.', 'danger')
                return redirect(url_for('profile.index'))
            current_user.set_password(new_password)
        db.session.commit()
        flash('Profile updated.', 'success')
        return redirect(url_for('profile.index'))

    uid = current_user.id
    return render_template('profile/index.html',
        total_predictions=Prediction.query.filter_by(user_id=uid).count(),
        total_batches=BatchJob.query.filter_by(user_id=uid).count(),
        stats={
            'reliable': Prediction.query.filter_by(user_id=uid, prediction='Reliable').count(),
            'misinfo': Prediction.query.filter_by(user_id=uid, prediction='Misinformation').count(),
            'misleading': Prediction.query.filter_by(user_id=uid, prediction='Misleading').count(),
        })


# ═══════════════════════════════════════════════════════════════
# FACT CHECKER — Human review of AI predictions and source evidence
# ============================================================================

fact_checker_bp = Blueprint('fact_checker', __name__, url_prefix='/fact-checker')


def fact_checker_required(f):
    @wraps(f)
    @login_required
    def decorated(*args, **kwargs):
        if not current_user.can_fact_check:
            flash('Fact-checker access required.', 'danger')
            return redirect(url_for('main.dashboard'))
        return f(*args, **kwargs)
    return decorated


def _record_assignment_event(audit, action, from_assignee_id=None, to_assignee_id=None):
    db.session.add(EvidenceAssignmentEvent(
        audit=audit,
        actor_id=current_user.id,
        from_assignee_id=from_assignee_id,
        to_assignee_id=to_assignee_id,
        action=action,
    ))


def _can_manage_review(audit):
    return current_user.is_admin or audit.assigned_to_id == current_user.id


def _case_risk_rank(audit):
    confidence = audit.prediction_record.confidence if audit.prediction_record else 0
    if audit.predicted_label == 'Misinformation':
        return 'critical' if confidence >= 90 else ('high' if confidence >= 75 else 'medium')
    if audit.predicted_label == 'Misleading':
        return 'high' if confidence >= 80 else ('medium' if confidence >= 60 else 'uncertain')
    return 'low' if confidence >= 80 else 'uncertain'


@fact_checker_bp.route('/dashboard')
@fact_checker_required
def dashboard():
    """Fact-checker dashboard — priority queue, stats, evidence alerts."""
    from sqlalchemy import func, case

    # --- Counts ---
    complete_audits = EvidenceAudit.query.filter(
        EvidenceAudit.status == 'complete',
        EvidenceAudit.assigned_to_id == current_user.id,
    )

    pending_count = complete_audits.filter(EvidenceAudit.review_status == 'pending').count()
    reviewed_count = (
        EvidenceAudit.query.join(FactCheckReview)
        .filter(FactCheckReview.reviewer_id == current_user.id)
        .count()
    )
    total_count = complete_audits.count()
    unassigned_count = EvidenceAudit.query.filter(
        EvidenceAudit.status == 'complete',
        EvidenceAudit.assigned_to_id.is_(None),
        EvidenceAudit.review_status == 'unassigned',
    ).count()

    open_assigned = complete_audits.filter(
        EvidenceAudit.review_status.in_(['pending', 'under_review'])
    ).all()
    high_risk_count = sum(
        1 for audit in open_assigned
        if _case_risk_rank(audit) in ('critical', 'high')
    )

    under_review_count = complete_audits.filter(
        EvidenceAudit.review_status == 'under_review'
    ).count()

    # --- Priority Queue: pending audits sorted by risk then date ---
    priority_audits = (
        EvidenceAudit.query
        .outerjoin(
            ClaimRecord,
            ClaimRecord.investigation_audit_id == EvidenceAudit.id,
        )
        .filter(
            EvidenceAudit.status == 'complete',
            EvidenceAudit.assigned_to_id == current_user.id,
            EvidenceAudit.review_status.in_(['pending', 'under_review']),
        )
        .order_by(
            case(
                (EvidenceAudit.predicted_label == 'Misinformation', 0),
                (EvidenceAudit.predicted_label == 'Misleading', 1),
                else_=2,
            ),
            db.func.coalesce(ClaimRecord.submission_count, 1).desc(),
            EvidenceAudit.created_at.desc(),
        )
        .limit(10)
        .all()
    )

    # Enrich priority audits with source stats
    priority_data = []
    for audit in priority_audits:
        sources = EvidenceSource.query.filter_by(audit_id=audit.id).all()
        trusted = sum(1 for s in sources if s.evidence_kind == 'trusted')
        refutes = sum(1 for s in sources if s.stance == 'Refutes')
        repeats = sum(1 for s in sources if s.stance == 'Repeats')
        supports = sum(1 for s in sources if s.stance == 'Supports')
        avg_sim = (sum(s.similarity_score for s in sources) / len(sources)) if sources else 0

        # Evidence alert flags
        alerts = []
        if len(sources) == 0:
            alerts.append('no_evidence')
        elif trusted == 0:
            alerts.append('no_trusted')
        if refutes > 0 and repeats > 0:
            alerts.append('conflicting')
        if repeats > supports and repeats > 0:
            alerts.append('only_repeats')
        if avg_sim < 40 and len(sources) > 0:
            alerts.append('weak_match')

        confidence = audit.prediction_record.confidence if audit.prediction_record else 0
        risk_level = _case_risk_rank(audit)

        priority_data.append({
            'audit': audit,
            'source_count': len(sources),
            'trusted_count': trusted,
            'refutes_count': refutes,
            'repeats_count': repeats,
            'supports_count': supports,
            'avg_similarity': round(avg_sim, 1),
            'confidence': confidence,
            'risk_level': risk_level,
            'alerts': alerts,
        })

    # --- Evidence Alerts: pending audits with issues ---
    alert_audits = (
        EvidenceAudit.query
        .filter(
            EvidenceAudit.status == 'complete',
            EvidenceAudit.assigned_to_id == current_user.id,
            EvidenceAudit.review_status.in_(['pending', 'under_review']),
        )
        .order_by(EvidenceAudit.created_at.desc())
        .limit(20)
        .all()
    )

    evidence_alerts = []
    for audit in alert_audits:
        sources = EvidenceSource.query.filter_by(audit_id=audit.id).all()
        trusted = sum(1 for s in sources if s.evidence_kind == 'trusted')
        refutes = sum(1 for s in sources if s.stance == 'Refutes')
        repeats = sum(1 for s in sources if s.stance == 'Repeats')
        supports = sum(1 for s in sources if s.stance == 'Supports')

        alert_type = None
        alert_msg = ''
        if len(sources) == 0:
            alert_type = 'critical'
            alert_msg = 'No evidence sources retrieved'
        elif trusted == 0:
            alert_type = 'warning'
            alert_msg = 'No trusted Somali source confirmation'
        elif refutes > 0 and supports > 0:
            alert_type = 'warning'
            alert_msg = f'Conflicting sources: {supports} support, {refutes} refute'
        elif repeats > supports and repeats >= 2:
            alert_type = 'info'
            alert_msg = f'Content only repeated {repeats} times, not independently verified'

        if alert_type:
            evidence_alerts.append({
                'audit': audit,
                'alert_type': alert_type,
                'alert_msg': alert_msg,
                'source_count': len(sources),
            })

    # --- AI vs Human Agreement ---
    agreement_stats = {'confirm': 0, 'change': 0}
    reviewed_audits = (
        EvidenceAudit.query
        .join(FactCheckReview)
        .filter(FactCheckReview.reviewer_id == current_user.id)
        .order_by(FactCheckReview.updated_at.desc())
        .limit(50)
        .all()
    )
    for audit in reviewed_audits:
        review = audit.fact_check_review
        if not review:
            continue
        ai_label = audit.predicted_label
        decision = review.decision
        if (ai_label == 'Reliable' and decision == 'Confirmed Reliable') or \
           (ai_label == 'Misinformation' and decision == 'Confirmed Misinformation') or \
           (ai_label == 'Misleading' and decision == 'Confirmed Misleading'):
            agreement_stats['confirm'] += 1
        else:
            agreement_stats['change'] += 1

    total_decisions = agreement_stats['confirm'] + agreement_stats['change']
    agreement_pct = round((agreement_stats['confirm'] / total_decisions * 100) if total_decisions > 0 else 0, 1)

    # --- Recent Decisions ---
    recent_decisions = (
        EvidenceAudit.query
        .join(FactCheckReview)
        .filter(FactCheckReview.reviewer_id == current_user.id)
        .order_by(FactCheckReview.updated_at.desc())
        .limit(5)
        .all()
    )

    # --- Next case for "Review Next" button ---
    next_case = (
        EvidenceAudit.query
        .filter(
            EvidenceAudit.status == 'complete',
            EvidenceAudit.assigned_to_id == current_user.id,
            EvidenceAudit.review_status.in_(['pending', 'under_review']),
        )
        .order_by(
            case(
                (EvidenceAudit.predicted_label == 'Misinformation', 0),
                (EvidenceAudit.predicted_label == 'Misleading', 1),
                else_=2,
            ),
            EvidenceAudit.created_at.desc(),
        )
        .first()
    )

    return render_template(
        'fact_checker/dashboard.html',
        pending_count=pending_count,
        reviewed_count=reviewed_count,
        total_count=total_count,
        high_risk_count=high_risk_count,
        under_review_count=under_review_count,
        priority_data=priority_data,
        evidence_alerts=evidence_alerts[:8],
        agreement_stats=agreement_stats,
        agreement_pct=agreement_pct,
        total_decisions=total_decisions,
        recent_decisions=recent_decisions,
        next_case=next_case,
        unassigned_count=unassigned_count,
    )


@fact_checker_bp.route('/')
@fact_checker_required
def index():
    from sqlalchemy import and_, or_
    page = request.args.get('page', 1, type=int)
    status = request.args.get('status', 'pending')
    query = EvidenceAudit.query.filter(EvidenceAudit.status == 'complete')

    if status == 'under_review':
        query = query.filter(
            EvidenceAudit.assigned_to_id == current_user.id,
            EvidenceAudit.review_status == 'under_review',
        )
    elif status == 'completed':
        query = query.join(FactCheckReview).filter(
            FactCheckReview.reviewer_id == current_user.id
        )
    elif status == 'high_risk':
        query = query.join(Prediction, EvidenceAudit.prediction_id == Prediction.id).filter(
            EvidenceAudit.assigned_to_id == current_user.id,
            EvidenceAudit.review_status.in_(['pending', 'under_review']),
            or_(
                and_(
                    EvidenceAudit.predicted_label == 'Misinformation',
                    Prediction.confidence >= 75,
                ),
                and_(
                    EvidenceAudit.predicted_label == 'Misleading',
                    Prediction.confidence >= 80,
                ),
            ),
        )
    elif status == 'unassigned':
        query = query.filter(
            EvidenceAudit.assigned_to_id.is_(None),
            EvidenceAudit.review_status == 'unassigned',
        )
    else:
        status = 'pending'
        query = query.filter(
            EvidenceAudit.assigned_to_id == current_user.id,
            EvidenceAudit.review_status == 'pending',
        )

    audits = query.order_by(EvidenceAudit.created_at.desc()).paginate(
        page=page, per_page=15, error_out=False
    )
    return render_template(
        'fact_checker/index.html', audits=audits, status_filter=status
    )


@fact_checker_bp.route('/audit/<int:audit_id>')
@fact_checker_required
def review_detail(audit_id):
    audit = EvidenceAudit.query.get_or_404(audit_id)
    if not current_user.is_admin and audit.assigned_to_id not in (None, current_user.id):
        flash('This case is assigned to another fact-checker.', 'danger')
        return redirect(url_for('fact_checker.index'))
    sources = EvidenceSource.query.filter_by(audit_id=audit.id).order_by(
        EvidenceSource.similarity_score.desc()
    ).all()
    try:
        queries = json.loads(audit.queries_json or '[]')
    except (TypeError, ValueError):
        queries = []
    return render_template(
        'fact_checker/review.html',
        audit=audit,
        sources=sources,
        queries=queries,
        decisions=FACT_CHECK_DECISIONS,
        can_manage=_can_manage_review(audit),
        assignment_events=audit.assignment_events,
    )


@fact_checker_bp.route('/audit/<int:audit_id>/claim', methods=['POST'])
@fact_checker_required
def claim_case(audit_id):
    if current_user.is_admin:
        flash('Admins assign cases from the Admin Dashboard.', 'info')
        return redirect(url_for('admin.index'))
    now = datetime.utcnow()
    updated = EvidenceAudit.query.filter(
        EvidenceAudit.id == audit_id,
        EvidenceAudit.status == 'complete',
        EvidenceAudit.assigned_to_id.is_(None),
        EvidenceAudit.review_status == 'unassigned',
    ).update({
        EvidenceAudit.assigned_to_id: current_user.id,
        EvidenceAudit.assigned_at: now,
        EvidenceAudit.review_status: 'pending',
    }, synchronize_session=False)
    if updated != 1:
        db.session.rollback()
        flash('This case has already been claimed or is unavailable.', 'warning')
        return redirect(url_for('fact_checker.index', status='unassigned'))
    audit = db.session.get(EvidenceAudit, audit_id)
    _record_assignment_event(audit, 'claimed', None, current_user.id)
    db.session.commit()
    flash(f'Case #{audit.id} is now assigned to you.', 'success')
    return redirect(url_for('fact_checker.review_detail', audit_id=audit.id))


@fact_checker_bp.route('/audit/<int:audit_id>/start', methods=['POST'])
@fact_checker_required
def start_review(audit_id):
    audit = EvidenceAudit.query.get_or_404(audit_id)
    if not _can_manage_review(audit):
        flash('Only the assigned fact-checker can start this review.', 'danger')
        return redirect(url_for('fact_checker.index'))
    if audit.review_status == 'pending':
        audit.review_status = 'under_review'
        _record_assignment_event(
            audit, 'review_started', audit.assigned_to_id, audit.assigned_to_id
        )
        db.session.commit()
    return redirect(url_for('fact_checker.review_detail', audit_id=audit.id))


@fact_checker_bp.route('/audit/<int:audit_id>/decision', methods=['POST'])
@fact_checker_required
def submit_decision(audit_id):
    audit = EvidenceAudit.query.get_or_404(audit_id)
    if audit.status != 'complete':
        flash('This evidence audit is not complete yet.', 'warning')
        return redirect(url_for('fact_checker.review_detail', audit_id=audit.id))
    if not _can_manage_review(audit):
        flash('Only the assigned fact-checker or an admin can decide this case.', 'danger')
        return redirect(url_for('fact_checker.index'))
    if audit.review_status not in ('under_review', 'completed'):
        flash('Start the assigned review before submitting a decision.', 'warning')
        return redirect(url_for('fact_checker.review_detail', audit_id=audit.id))

    was_completed = audit.review_status == 'completed'
    decision = request.form.get('decision', '').strip()
    explanation = request.form.get('explanation', '').strip()
    if decision not in FACT_CHECK_DECISIONS:
        flash('Select a valid fact-checker decision.', 'danger')
        return redirect(url_for('fact_checker.review_detail', audit_id=audit.id))
    if len(explanation) < 10:
        flash('Provide a clear explanation of at least 10 characters.', 'danger')
        return redirect(url_for('fact_checker.review_detail', audit_id=audit.id))

    review = audit.fact_check_review
    if review is None:
        review = FactCheckReview(audit=audit)
        db.session.add(review)
    review.reviewer = current_user
    review.decision = decision
    review.explanation = explanation
    review.updated_at = datetime.utcnow()
    audit.review_status = 'completed'
    if audit.assigned_to_id is None:
        audit.assigned_to_id = current_user.id
        audit.assigned_at = datetime.utcnow()
    _record_assignment_event(
        audit, 'decision_updated' if was_completed else 'review_completed',
        audit.assigned_to_id, audit.assigned_to_id,
    )
    db.session.commit()
    flash('Fact-checker decision saved.', 'success')
    return redirect(url_for('fact_checker.review_detail', audit_id=audit.id))


# ADMIN — Admin panel + database viewer
# ═══════════════════════════════════════════════════════════════

admin_bp = Blueprint('admin', __name__, url_prefix='/admin')


def admin_required(f):
    @wraps(f)
    @login_required
    def decorated(*args, **kwargs):
        if not current_user.is_admin:
            flash('Admin access required.', 'danger')
            return redirect(url_for('main.dashboard'))
        return f(*args, **kwargs)
    return decorated


@admin_bp.route('/')
@admin_required
def index():
    from datetime import timedelta
    from collections import Counter
    from sqlalchemy import case

    users = User.query.all()
    pred_page = request.args.get('pred_page', 1, type=int)
    admin_predictions = Prediction.query.order_by(Prediction.created_at.desc()).paginate(page=pred_page, per_page=15, error_out=False)

    # --- System Stats ---
    total_users = len(users)
    fact_checker_count = sum(1 for u in users if u.role == 'fact_checker')
    fact_checkers = [u for u in users if u.role == 'fact_checker' and not u.is_blocked]
    admin_count = sum(1 for u in users if u.role == 'admin')
    total_predictions = Prediction.query.count()
    total_batches = BatchJob.query.count()
    total_evidence_audits = EvidenceAudit.query.count()

    # --- Review Progress ---
    complete_audits = EvidenceAudit.query.filter(EvidenceAudit.status == 'complete')
    pending_reviews = complete_audits.outerjoin(FactCheckReview).filter(FactCheckReview.id.is_(None)).count()
    completed_reviews = complete_audits.join(FactCheckReview).count()
    failed_audits = EvidenceAudit.query.filter(EvidenceAudit.status == 'failed').count()
    insufficient_evidence = (
        complete_audits.join(FactCheckReview)
        .filter(FactCheckReview.decision == 'Insufficient Evidence')
        .count()
    )
    unassigned_reviews = complete_audits.filter(
        EvidenceAudit.assigned_to_id.is_(None),
        EvidenceAudit.review_status == 'unassigned',
    ).count()
    assigned_open_reviews = complete_audits.filter(
        EvidenceAudit.assigned_to_id.isnot(None),
        EvidenceAudit.review_status.in_(['pending', 'under_review']),
    ).count()
    assignment_queue = (
        complete_audits
        .outerjoin(
            ClaimRecord,
            ClaimRecord.investigation_audit_id == EvidenceAudit.id,
        )
        .filter(EvidenceAudit.review_status != 'completed')
        .order_by(
            case(
                (EvidenceAudit.predicted_label == 'Misinformation', 0),
                (EvidenceAudit.predicted_label == 'Misleading', 1),
                else_=2,
            ),
            db.func.coalesce(ClaimRecord.submission_count, 1).desc(),
            EvidenceAudit.created_at.desc(),
        )
        .limit(20)
        .all()
    )

    # --- Prediction Distribution ---
    pred_dist_raw = db.session.query(
        Prediction.prediction, db.func.count(Prediction.id)
    ).group_by(Prediction.prediction).all()
    pred_distribution = {label: count for label, count in pred_dist_raw}

    # --- Model Usage ---
    raw_model_stats = db.session.query(
        Prediction.model_used, db.func.count(Prediction.id), db.func.avg(Prediction.confidence)
    ).group_by(Prediction.model_used).all()

    model_stats = []
    for key, count, avg_conf in raw_model_stats:
        avg_conf = avg_conf or 0
        most_pred = db.session.query(Prediction.prediction, db.func.count(Prediction.id))\
            .filter_by(model_used=key).group_by(Prediction.prediction)\
            .order_by(db.func.count(Prediction.id).desc()).first()
        model_stats.append({
            'name': MODEL_NAMES.get(key, key), 'key': key, 'count': count,
            'avg_confidence': round(avg_conf / 100, 4),
            'most_predicted': most_pred[0] if most_pred else 'N/A',
        })

    # --- AI vs Human Agreement ---
    agreement_stats = {'confirm': 0, 'change': 0}
    reviewed_audits = (
        EvidenceAudit.query.join(FactCheckReview)
        .order_by(FactCheckReview.updated_at.desc()).limit(50).all()
    )
    for audit in reviewed_audits:
        review = audit.fact_check_review
        if not review:
            continue
        ai_label = audit.predicted_label
        decision = review.decision
        if (ai_label == 'Reliable' and decision == 'Confirmed Reliable') or \
           (ai_label == 'Misinformation' and decision == 'Confirmed Misinformation') or \
           (ai_label == 'Misleading' and decision == 'Confirmed Misleading'):
            agreement_stats['confirm'] += 1
        else:
            agreement_stats['change'] += 1
    total_decisions = agreement_stats['confirm'] + agreement_stats['change']
    agreement_pct = round((agreement_stats['confirm'] / total_decisions * 100) if total_decisions > 0 else 0, 1)

    # --- High-Risk Activity ---
    high_risk = (
        EvidenceAudit.query
        .filter(EvidenceAudit.predicted_label.in_(['Misinformation', 'Misleading']))
        .order_by(EvidenceAudit.created_at.desc())
        .limit(5)
        .all()
    )
    high_risk_data = []
    for audit in high_risk:
        sources = EvidenceSource.query.filter_by(audit_id=audit.id).all()
        conf = audit.prediction_record.confidence if audit.prediction_record else 0
        high_risk_data.append({
            'audit': audit,
            'confidence': conf,
            'source_count': len(sources),
        })

    # --- Recent Activity (last 24h) ---
    recent_users = User.query.filter(User.created_at >= datetime.utcnow() - timedelta(hours=24)).count()
    recent_predictions = Prediction.query.filter(Prediction.created_at >= datetime.utcnow() - timedelta(hours=24)).count()
    recent_audits = EvidenceAudit.query.filter(EvidenceAudit.created_at >= datetime.utcnow() - timedelta(hours=24)).count()

    # --- Recent Predictions ---
    recent_predictions_list = Prediction.query.order_by(Prediction.created_at.desc()).limit(5).all()

    return render_template('admin/index.html',
        users=users, admin_predictions=admin_predictions,
        total_users=total_users, fact_checker_count=fact_checker_count,
        admin_count=admin_count, total_predictions=total_predictions,
        total_batches=total_batches, total_evidence_audits=total_evidence_audits,
        pending_reviews=pending_reviews, completed_reviews=completed_reviews,
        failed_audits=failed_audits, insufficient_evidence=insufficient_evidence,
        pred_distribution=pred_distribution, model_stats=model_stats,
        model_usage_names=json.dumps([m['name'] for m in model_stats]),
        model_usage_counts=json.dumps([m['count'] for m in model_stats]),
        agreement_stats=agreement_stats, agreement_pct=agreement_pct,
        total_decisions=total_decisions, high_risk_data=high_risk_data,
        recent_users=recent_users, recent_predictions=recent_predictions,
        recent_audits=recent_audits, recent_predictions_list=recent_predictions_list,
        fact_checkers=fact_checkers, assignment_queue=assignment_queue,
        unassigned_reviews=unassigned_reviews,
        assigned_open_reviews=assigned_open_reviews,
        MODEL_NAMES=MODEL_NAMES)


@admin_bp.route('/audit/<int:audit_id>/assign', methods=['POST'])
@admin_required
def assign_audit(audit_id):
    audit = EvidenceAudit.query.get_or_404(audit_id)
    assignee_id = request.form.get('assignee_id', type=int)
    assignee = db.session.get(User, assignee_id) if assignee_id else None
    if audit.status != 'complete' or audit.review_status == 'completed':
        flash('Only open, completed evidence audits can be assigned.', 'warning')
        return redirect(url_for('admin.index'))
    if not assignee or not assignee.is_fact_checker or assignee.is_blocked:
        flash('Select an active fact-checker.', 'danger')
        return redirect(url_for('admin.index'))
    previous_id = audit.assigned_to_id
    audit.assigned_to_id = assignee.id
    audit.assigned_by_id = current_user.id
    audit.assigned_at = datetime.utcnow()
    audit.review_status = 'pending'
    _record_assignment_event(
        audit, 'assigned' if previous_id is None else 'reassigned',
        previous_id, assignee.id,
    )
    db.session.commit()
    flash(f'Case #{audit.id} assigned to {assignee.username}.', 'success')
    return redirect(url_for('admin.index'))


@admin_bp.route('/audit/<int:audit_id>/unassign', methods=['POST'])
@admin_required
def unassign_audit(audit_id):
    audit = EvidenceAudit.query.get_or_404(audit_id)
    if audit.review_status == 'completed':
        flash('A completed case cannot be returned to the queue.', 'warning')
        return redirect(url_for('admin.index'))
    previous_id = audit.assigned_to_id
    audit.assigned_to_id = None
    audit.assigned_by_id = current_user.id
    audit.assigned_at = None
    audit.review_status = 'unassigned'
    _record_assignment_event(audit, 'unassigned', previous_id, None)
    db.session.commit()
    flash(f'Case #{audit.id} returned to the unassigned queue.', 'success')
    return redirect(url_for('admin.index'))


@admin_bp.route('/create_user', methods=['POST'])
@admin_required
def create_user():
    username = request.form.get('username', '').strip()
    email = request.form.get('email', '').strip()
    password = request.form.get('password', '')
    role = request.form.get('role', 'user')
    if role not in {'user', 'fact_checker', 'admin'}:
        flash('Invalid user role.', 'danger')
        return redirect(url_for('admin.index'))
    if role != 'user' and current_user.email != 'admin@somali.com':
        flash('Only the primary admin can assign privileged roles.', 'danger')
        return redirect(url_for('admin.index'))
    if len(username) < 3 or len(password) < 6:
        flash('Username must be 3+ chars, password 6+ chars.', 'danger')
        return redirect(url_for('admin.index'))
    if User.query.filter_by(username=username).first():
        flash('Username already taken.', 'danger')
        return redirect(url_for('admin.index'))
    if User.query.filter_by(email=email).first():
        flash('Email already registered.', 'danger')
        return redirect(url_for('admin.index'))
    user = User(username=username, email=email, role=role, email_verified=True)
    user.set_password(password)
    db.session.add(user)
    db.session.commit()
    flash(f'User {username} created.', 'success')
    return redirect(url_for('admin.index'))


@admin_bp.route('/edit_user/<int:user_id>', methods=['POST'])
@admin_required
def edit_user(user_id):
    user = User.query.get_or_404(user_id)
    username = request.form.get('username', '').strip()
    email = request.form.get('email', '').strip()
    new_password = request.form.get('password', '').strip()
    if len(username) < 3:
        flash('Username must be at least 3 characters.', 'danger')
        return redirect(url_for('admin.index'))
    existing = User.query.filter_by(username=username).first()
    if existing and existing.id != user.id:
        flash('Username already taken.', 'danger')
        return redirect(url_for('admin.index'))
    existing = User.query.filter_by(email=email).first()
    if existing and existing.id != user.id:
        flash('Email already registered.', 'danger')
        return redirect(url_for('admin.index'))
    user.username = username
    user.email = email
    if new_password:
        if len(new_password) < 6:
            flash('Password must be at least 6 characters.', 'danger')
            return redirect(url_for('admin.index'))
        user.set_password(new_password)
    db.session.commit()
    flash(f'User {username} updated.', 'success')
    return redirect(url_for('admin.index'))


@admin_bp.route('/block_user/<int:user_id>', methods=['POST'])
@admin_required
def block_user(user_id):
    user = User.query.get_or_404(user_id)
    if user.is_admin:
        flash('Cannot block admin.', 'danger')
        return redirect(url_for('admin.index'))
    user.is_blocked = not user.is_blocked
    if user.is_blocked:
        for audit in EvidenceAudit.query.filter(
            EvidenceAudit.assigned_to_id == user.id,
            EvidenceAudit.review_status != 'completed',
        ).all():
            audit.assigned_to_id = None
            audit.assigned_at = None
            audit.review_status = 'unassigned'
            _record_assignment_event(audit, 'unassigned_blocked_user', user.id, None)
    db.session.commit()
    state = 'blocked' if user.is_blocked else 'unblocked'
    flash(f'User {user.username} {state}.', 'success')
    return redirect(url_for('admin.index'))


@admin_bp.route('/set_role/<int:user_id>', methods=['POST'])
@admin_required
def set_role(user_id):
    user = User.query.get_or_404(user_id)
    if current_user.email != 'admin@somali.com':
        flash('Only the primary admin can change roles.', 'danger')
        return redirect(url_for('admin.index'))
    if user.email == 'admin@somali.com':
        flash('Cannot change the primary admin role.', 'danger')
        return redirect(url_for('admin.index'))
    new_role = request.form.get('role', '').strip()
    if new_role not in {'user', 'fact_checker', 'admin'}:
        flash('Invalid user role.', 'danger')
        return redirect(url_for('admin.index'))
    if user.is_fact_checker and new_role != 'fact_checker':
        for audit in EvidenceAudit.query.filter(
            EvidenceAudit.assigned_to_id == user.id,
            EvidenceAudit.review_status != 'completed',
        ).all():
            audit.assigned_to_id = None
            audit.assigned_at = None
            audit.review_status = 'unassigned'
            _record_assignment_event(audit, 'unassigned_role_change', user.id, None)
    user.role = new_role
    db.session.commit()
    flash(f'User {user.username} role set to {user.role}.', 'success')
    return redirect(url_for('admin.index'))


@admin_bp.route('/delete_user/<int:user_id>', methods=['POST'])
@admin_required
def delete_user(user_id):
    user = User.query.get_or_404(user_id)
    if user.is_admin:
        flash('Cannot delete admin.', 'danger')
        return redirect(url_for('admin.index'))
    FactCheckReview.query.filter_by(reviewer_id=user.id).update(
        {'reviewer_id': None}, synchronize_session=False
    )
    EvidenceAudit.query.filter_by(assigned_to_id=user.id).update({
        'assigned_to_id': None,
        'assigned_at': None,
        'review_status': 'unassigned',
    }, synchronize_session=False)
    EvidenceAudit.query.filter_by(assigned_by_id=user.id).update(
        {'assigned_by_id': None}, synchronize_session=False
    )
    for audit in EvidenceAudit.query.filter_by(user_id=user.id).all():
        db.session.delete(audit)
    Prediction.query.filter_by(user_id=user.id).delete()
    BatchJob.query.filter_by(user_id=user.id).delete()
    db.session.delete(user)
    db.session.commit()
    flash(f'User {user.username} deleted.', 'success')
    return redirect(url_for('admin.index'))


@admin_bp.route('/delete_prediction/<int:pred_id>', methods=['POST'])
@admin_required
def delete_prediction(pred_id):
    pred = Prediction.query.get_or_404(pred_id)
    db.session.delete(pred)
    db.session.commit()
    flash('Prediction deleted.', 'success')
    return redirect(url_for('admin.index'))


# ═══════════════════════════════════════════════════════════════
# API — Public endpoint for landing page demo
# ═══════════════════════════════════════════════════════════════

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

    from gemini_service import is_political
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
        from gemini_service import extract_main_claim, generate_search_queries, semantic_match, verify_same_event
        from source_search import search_somali_sources_with_fallback, search_web_everywhere_with_fallback
        from facebook_search import search_facebook_with_fallback

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


# ═══════════════════════════════════════════════════════════════
# EXPORT — PDF Investigation Report
# ═══════════════════════════════════════════════════════════════

@predict_bp.route('/export-report', methods=['POST'])
@login_required
def export_report():
    from flask import send_file
    from pdf_report import generate_report

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
        return send_file(filepath, as_attachment=True, download_name=filename,
                         mimetype='application/pdf')
    except Exception as e:
        print(f'[PDF] Export error: {e}')
        return jsonify({'error': 'Failed to generate report'}), 500
