"""
Flask App — Creates the app and connects everything.
"""
import re

from flask import Flask, flash, jsonify, redirect, request, session, url_for
from flask_login import current_user, logout_user
from sqlalchemy import inspect, text
from config import Config
from models import (
    db, login_manager, mail, User, FactCheckReview, EvidenceAssignmentEvent,
    EvidenceAudit, ClaimRecord, BatchItem, EvidenceCaseDeletionLog, Notification,
)
from services.claim_service import claim_fingerprint, normalize_claim
from services.time_service import format_somalia_time

app = Flask(__name__, template_folder='templates', static_folder='static')
app.config.from_object(Config)
app.jinja_env.filters['somali_time'] = format_somalia_time

# Init extensions
db.init_app(app)
mail.init_app(app)
login_manager.init_app(app)
login_manager.login_view = 'auth.login'
login_manager.login_message_category = 'info'


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


# Import and register all blueprints
from controllers import (
    main_bp, auth_bp, predict_bp, batch_bp,
    history_bp, models_bp, profile_bp, fact_checker_bp, admin_bp, api_bp,
    sources_bp, notifications_bp,
)

app.register_blueprint(main_bp)
app.register_blueprint(auth_bp)
app.register_blueprint(predict_bp)
app.register_blueprint(batch_bp)
app.register_blueprint(history_bp)
app.register_blueprint(models_bp)
app.register_blueprint(profile_bp)
app.register_blueprint(fact_checker_bp)
app.register_blueprint(admin_bp)
app.register_blueprint(api_bp)
app.register_blueprint(sources_bp)
app.register_blueprint(notifications_bp)


@app.before_request
def enforce_active_account():
    """End an existing session as soon as an administrator deactivates it."""
    if getattr(current_user, 'id', None) is None or current_user.is_active:
        return None
    logout_user()
    session.clear()
    message = 'Your account is inactive. Please contact an administrator.'
    if request.blueprint == 'api' or request.path.startswith('/api/') or request.is_json:
        return jsonify({'error': message}), 403
    flash(message, 'danger')
    return redirect(url_for('auth.login'))


@app.context_processor
def notification_context():
    """Provide the signed-in user's unread case updates to the app shell."""
    if not current_user.is_authenticated:
        return {
            'unread_notification_count': 0,
            'recent_notifications': [],
            'is_primary_admin_session': False,
        }
    unread_query = Notification.query.filter_by(
        user_id=current_user.id, is_read=False
    )
    return {
        'unread_notification_count': unread_query.count(),
        'recent_notifications': unread_query.order_by(
            Notification.created_at.desc()
        ).limit(5).all(),
        'is_primary_admin_session': (
            current_user.is_admin
            and current_user.email.casefold()
            == app.config['PRIMARY_ADMIN_EMAIL'].casefold()
        ),
    }


# Create tables + admin user
with app.app_context():
    db.create_all()
    user_columns = {
        column['name'] for column in inspect(db.engine).get_columns('users')
    }
    if 'is_active' not in user_columns:
        try:
            db.session.execute(text(
                'ALTER TABLE users ADD COLUMN is_active BOOLEAN NOT NULL DEFAULT 1'
            ))
            if 'is_blocked' in user_columns:
                db.session.execute(text("""
                    UPDATE users
                    SET is_active = CASE
                        WHEN COALESCE(is_blocked, 0) = 1 THEN 0
                        ELSE 1
                    END
                """))
            db.session.commit()
            user_columns.add('is_active')
        except Exception as exc:
            db.session.rollback()
            print(f'[Schema] Could not add users.is_active: {exc}')
    fact_checker_application_columns = {
        'fact_checker_application_status': (
            "VARCHAR(20) NOT NULL DEFAULT 'not_requested'"
        ),
        'fact_checker_organization': "VARCHAR(200) NOT NULL DEFAULT ''",
        'fact_checker_professional_id': "VARCHAR(120) NOT NULL DEFAULT ''",
        'fact_checker_id_document': "VARCHAR(500) NOT NULL DEFAULT ''",
        'fact_checker_certificates_json': "TEXT NULL",
        'fact_checker_application_note': "TEXT NULL",
        'fact_checker_applied_at': 'DATETIME NULL',
        'fact_checker_reviewed_at': 'DATETIME NULL',
        'fact_checker_reviewed_by_id': 'INTEGER NULL',
    }
    for column_name, column_type in fact_checker_application_columns.items():
        if column_name in user_columns:
            continue
        try:
            db.session.execute(text(
                f'ALTER TABLE users ADD COLUMN {column_name} {column_type}'
            ))
            db.session.commit()
        except Exception as exc:
            db.session.rollback()
            print(f'[Schema] Could not add users.{column_name}: {exc}')
    try:
        db.session.execute(text("""
            UPDATE users
            SET fact_checker_application_status = 'approved'
            WHERE role = 'fact_checker'
              AND (fact_checker_application_status IS NULL
                   OR fact_checker_application_status = ''
                   OR fact_checker_application_status = 'not_requested')
        """))
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        print(f'[Schema] Could not backfill fact-checker application status: {exc}')
    audit_columns = {
        column['name'] for column in inspect(db.engine).get_columns('evidence_audits')
    }
    if 'ai_explanation' not in audit_columns:
        try:
            column_sql = (
                "ALTER TABLE evidence_audits ADD COLUMN ai_explanation TEXT NULL"
                if db.engine.dialect.name == 'mysql'
                else "ALTER TABLE evidence_audits ADD COLUMN ai_explanation TEXT DEFAULT ''"
            )
            db.session.execute(text(column_sql))
            db.session.commit()
        except Exception as exc:
            db.session.rollback()
            print(f'[Schema] Could not add evidence_audits.ai_explanation: {exc}')
    # Add assignment workflow columns to existing installations. New databases
    # receive them through db.create_all(); this block is only a safe upgrade.
    audit_columns = {
        column['name'] for column in inspect(db.engine).get_columns('evidence_audits')
    }
    assignment_columns = {
        'assigned_to_id': 'INTEGER NULL',
        'assigned_by_id': 'INTEGER NULL',
        'assigned_at': 'DATETIME NULL',
        'review_status': "VARCHAR(20) NOT NULL DEFAULT 'unassigned'",
        'is_archived': 'BOOLEAN NOT NULL DEFAULT 0',
        'archived_by_id': 'INTEGER NULL',
        'archived_at': 'DATETIME NULL',
        'archive_reason': 'TEXT NULL',
    }
    for column_name, column_type in assignment_columns.items():
        if column_name in audit_columns:
            continue
        try:
            db.session.execute(text(
                f'ALTER TABLE evidence_audits ADD COLUMN {column_name} {column_type}'
            ))
            db.session.commit()
        except Exception as exc:
            db.session.rollback()
            print(f'[Schema] Could not add evidence_audits.{column_name}: {exc}')

    # Keep confidence on the evidence case itself. Prediction history may be
    # deleted later, but the fact-check case must retain its original score.
    audit_columns = {
        column['name'] for column in inspect(db.engine).get_columns('evidence_audits')
    }
    if 'prediction_confidence' not in audit_columns:
        try:
            db.session.execute(text(
                'ALTER TABLE evidence_audits ADD COLUMN prediction_confidence FLOAT NULL'
            ))
            db.session.commit()
        except Exception as exc:
            db.session.rollback()
            print(f'[Schema] Could not add evidence_audits.prediction_confidence: {exc}')

    try:
        backfilled_confidence = 0
        missing_confidence = EvidenceAudit.query.filter(
            EvidenceAudit.prediction_confidence.is_(None)
        ).all()
        for audit in missing_confidence:
            recovered = None
            if audit.prediction_record is not None:
                recovered = audit.prediction_record.confidence
            elif audit.spread_record and audit.spread_record.first_prediction:
                recovered = audit.spread_record.first_prediction.confidence
            else:
                batch_item = BatchItem.query.filter_by(
                    investigation_audit_id=audit.id
                ).order_by(BatchItem.id.asc()).first()
                if batch_item and batch_item.confidence is not None:
                    recovered = batch_item.confidence
            if recovered is None and audit.ai_explanation:
                match = re.search(
                    r'average(?:\s+winning)?\s+confidence.*?(\d+(?:\.\d+)?)%',
                    audit.ai_explanation,
                    flags=re.IGNORECASE,
                )
                if match:
                    recovered = float(match.group(1))
            if recovered is not None and 0 <= float(recovered) <= 100:
                audit.prediction_confidence = float(recovered)
                backfilled_confidence += 1
        if backfilled_confidence:
            db.session.commit()
            print(f'[Schema] Backfilled confidence for {backfilled_confidence} evidence audits.')
    except Exception as exc:
        db.session.rollback()
        print(f'[Schema] Could not backfill evidence confidence: {exc}')
    # Preserve previous decisions: the original reviewer becomes the assignee
    # and the case is marked completed. This update is idempotent.
    try:
        db.session.execute(text("""
            UPDATE evidence_audits
            SET review_status = 'completed',
                assigned_to_id = COALESCE(
                    assigned_to_id,
                    (SELECT reviewer_id FROM evidence_fact_check_reviews
                     WHERE evidence_fact_check_reviews.audit_id = evidence_audits.id)
                ),
                assigned_at = COALESCE(
                    assigned_at,
                    (SELECT created_at FROM evidence_fact_check_reviews
                     WHERE evidence_fact_check_reviews.audit_id = evidence_audits.id)
                )
            WHERE EXISTS (
                SELECT 1 FROM evidence_fact_check_reviews
                WHERE evidence_fact_check_reviews.audit_id = evidence_audits.id
            )
        """))
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        print(f'[Schema] Could not backfill review assignments: {exc}')

    # Reliable predictions complete without entering the human-review queue.
    # Preserve any legacy case that already has a published human decision.
    try:
        result = db.session.execute(text("""
            UPDATE evidence_audits
            SET review_status = 'not_required',
                assigned_to_id = NULL,
                assigned_by_id = NULL,
                assigned_at = NULL
            WHERE predicted_label = 'Reliable'
              AND NOT EXISTS (
                  SELECT 1 FROM evidence_fact_check_reviews
                  WHERE evidence_fact_check_reviews.audit_id = evidence_audits.id
              )
              AND review_status != 'not_required'
        """))
        db.session.commit()
        if result.rowcount:
            print(
                f'[Schema] Completed {result.rowcount} unreviewed Reliable cases '
                'without fact-check assignment.'
            )
    except Exception as exc:
        db.session.rollback()
        print(f'[Schema] Could not complete Reliable cases: {exc}')
    try:
        for review in FactCheckReview.query.all():
            has_event = EvidenceAssignmentEvent.query.filter_by(
                audit_id=review.audit_id
            ).first()
            if not has_event:
                db.session.add(EvidenceAssignmentEvent(
                    audit_id=review.audit_id,
                    actor_id=review.reviewer_id,
                    to_assignee_id=review.reviewer_id,
                    action='legacy_review_backfill',
                    created_at=review.created_at,
                ))
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        print(f'[Schema] Could not create legacy assignment events: {exc}')
    # One-time registry seed for evidence audits created before duplicate
    # detection existed. Subsequent submissions update these records normally.
    try:
        if ClaimRecord.query.count() == 0:
            for audit in EvidenceAudit.query.order_by(EvidenceAudit.created_at.asc()).all():
                fingerprint = claim_fingerprint(audit.original_text)
                if not fingerprint:
                    continue
                record = ClaimRecord.query.filter_by(fingerprint=fingerprint).first()
                if record is None:
                    record = ClaimRecord(
                        fingerprint=fingerprint,
                        normalized_text=normalize_claim(audit.original_text),
                        original_text=audit.original_text,
                        first_prediction_id=audit.prediction_id,
                        investigation_audit_id=audit.id,
                        submission_count=1,
                        first_submitted_at=audit.created_at,
                        last_submitted_at=audit.created_at,
                    )
                    db.session.add(record)
                    db.session.flush()
                else:
                    record.submission_count += 1
                    record.last_submitted_at = max(
                        record.last_submitted_at or audit.created_at, audit.created_at
                    )
                    existing = record.investigation
                    if audit.fact_check_review and not (
                        existing and existing.fact_check_review
                    ):
                        record.investigation_audit_id = audit.id
                    if not record.first_prediction_id and audit.prediction_id:
                        record.first_prediction_id = audit.prediction_id
            db.session.commit()
    except Exception as exc:
        db.session.rollback()
        print(f'[Schema] Could not seed claim spread records: {exc}')
    # db.create_all() does not resize existing columns. Keep the fact_checker role valid.
    batch_columns = {
        column['name'] for column in inspect(db.engine).get_columns('batch_jobs')
    }
    batch_workspace_columns = {
        'input_path': "VARCHAR(500) NOT NULL DEFAULT ''",
        'text_column': "VARCHAR(255) NOT NULL DEFAULT ''",
        'source_url_column': "VARCHAR(255) NOT NULL DEFAULT ''",
        'source_name_column': "VARCHAR(255) NOT NULL DEFAULT ''",
        'date_column': "VARCHAR(255) NOT NULL DEFAULT ''",
        'model_used': "VARCHAR(50) NOT NULL DEFAULT 'svm'",
        'duplicate_detection': 'BOOLEAN NOT NULL DEFAULT 1',
        'high_risk_only': 'BOOLEAN NOT NULL DEFAULT 1',
        'validation_json': "TEXT NULL",
        'summary_json': "TEXT NULL",
        'error_message': "TEXT NULL",
    }
    for column_name, column_type in batch_workspace_columns.items():
        if column_name in batch_columns:
            continue
        try:
            db.session.execute(text(
                f'ALTER TABLE batch_jobs ADD COLUMN {column_name} {column_type}'
            ))
            db.session.commit()
        except Exception as exc:
            db.session.rollback()
            print(f'[Schema] Could not add batch_jobs.{column_name}: {exc}')

    # db.create_all() does not resize existing columns. Keep the fact_checker role valid.
    if db.engine.dialect.name == 'mysql':
        role_column = next(
            (column for column in inspect(db.engine).get_columns('users')
             if column['name'] == 'role'),
            None,
        )
        if role_column and (getattr(role_column['type'], 'length', 0) or 0) < 20:
            try:
                db.session.execute(text(
                    "ALTER TABLE users MODIFY role VARCHAR(20) NOT NULL DEFAULT 'user'"
                ))
                db.session.commit()
            except Exception as exc:
                db.session.rollback()
                print(f'[Schema] Could not resize users.role automatically: {exc}')
    primary_admin = User.query.filter(
        db.func.lower(User.email)
        == app.config['PRIMARY_ADMIN_EMAIL'].casefold()
    ).first()
    if primary_admin:
        desired_username = app.config['PRIMARY_ADMIN_USERNAME'].strip() or 'Hussein'
        username_owner = User.query.filter(
            db.func.lower(User.username) == desired_username.casefold(),
            User.id != primary_admin.id,
        ).first()
        if username_owner is None and primary_admin.username != desired_username:
            primary_admin.username = desired_username
        primary_admin.role = 'admin'
        primary_admin.email_verified = True
        primary_admin.is_active = True
        db.session.commit()
    elif app.config['PRIMARY_ADMIN_PASSWORD']:
        primary_admin = User(
            username=app.config['PRIMARY_ADMIN_USERNAME'],
            email=app.config['PRIMARY_ADMIN_EMAIL'],
            role='admin',
            email_verified=True,
        )
        primary_admin.set_password(app.config['PRIMARY_ADMIN_PASSWORD'])
        db.session.add(primary_admin)
        db.session.commit()
        print('[INIT] Primary administrator Hussein created from .env settings.')
    else:
        print(
            '[INIT] No primary administrator exists. Set PRIMARY_ADMIN_PASSWORD '
            'in .env and restart once to create Hussein securely.'
        )
