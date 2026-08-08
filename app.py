"""
Flask App — Creates the app and connects everything.
"""
from flask import Flask
from sqlalchemy import inspect, text
from config import Config
from models import (
    db, login_manager, mail, User, FactCheckReview, EvidenceAssignmentEvent,
    EvidenceAudit, ClaimRecord,
)
from services.claim_service import claim_fingerprint, normalize_claim

app = Flask(__name__, template_folder='templates', static_folder='static')
app.config.from_object(Config)

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
    history_bp, models_bp, profile_bp, fact_checker_bp, admin_bp, api_bp
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


# Create tables + admin user
with app.app_context():
    db.create_all()
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
    if not User.query.filter_by(role='admin').first():
        admin = User(username='admin', email='admin@somali.com', role='admin', email_verified=True)
        admin.set_password('admin123')
        db.session.add(admin)
        db.session.commit()
        print('[INIT] Admin user created (admin@somali.com / admin123)')
