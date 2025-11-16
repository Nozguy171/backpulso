# app/__init__.py
from flask import Flask
from flask_cors import CORS
from .config import Config
from .extensions import db, migrate, jwt
from .routes.auth import auth_bp
from .routes.prospects import prospects_bp  # 👈 nuevo

def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)

    CORS(app, resources={r"/api/*": {"origins": "*"}})

    db.init_app(app)
    migrate.init_app(app, db)
    jwt.init_app(app)

    # blueprints
    app.register_blueprint(auth_bp, url_prefix="/api/auth")
    app.register_blueprint(prospects_bp, url_prefix="/api/prospects")  # 👈 nuevo

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    return app
