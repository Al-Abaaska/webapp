"""
Flask App — Creates the app and connects everything.
"""
from flask import Flask
from sqlalchemy import inspect, text
from config import Config
from models import db, login_manager, mail, User

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
from routes import (
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
