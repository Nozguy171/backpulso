# app/__init__.py
from flask import Flask
from flask_cors import CORS
from .config import Config
from .extensions import db, migrate, jwt
from .routes.auth import auth_bp
from .routes.prospects import prospects_bp  # 👈 nuevo
from .routes.history import history_bp
from .routes.users import users_bp
from .routes.appointments import appointments_bp
from .routes.calls import calls_bp
from .routes.invites import invites_bp

def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)

    CORS(app, resources={r"/api/*": {"origins": "*"}})

    db.init_app(app)
    migrate.init_app(app, db)
    jwt.init_app(app)

    # blueprints
    app.register_blueprint(auth_bp, url_prefix="/api/auth")
    app.register_blueprint(prospects_bp, url_prefix="/api/prospects")
    app.register_blueprint(history_bp, url_prefix="/api/history")
    app.register_blueprint(users_bp, url_prefix="/api/users")
    app.register_blueprint(appointments_bp, url_prefix="/api/appointments")
    app.register_blueprint(calls_bp, url_prefix="/api/calls")
    app.register_blueprint(invites_bp, url_prefix="/api/invites")
    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    return app
