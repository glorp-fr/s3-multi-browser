import os

from flask import Flask, g

from . import auth, backup, storage, usage_cache, version


def create_app():
    app = Flask(__name__)
    app.config["SECRET_KEY"] = os.environ.get("FLASK_SECRET_KEY", os.environ.get("APP_MASTER_KEY", "dev"))
    app.config["MAX_CONTENT_LENGTH"] = int(os.environ.get("MAX_UPLOAD_MB", "512")) * 1024 * 1024

    app.jinja_env.filters["format_size"] = usage_cache.format_size

    storage.migrate()
    storage.bootstrap_admin_if_empty()
    backup.start()

    @app.before_request
    def _load_user():
        auth.load_logged_in_user()

    @app.context_processor
    def _inject_globals():
        return {
            "current_user": g.get("user"),
            "app_version": version.VERSION,
            "update_available": version.update_available(),
        }

    from .routes.auth_routes import bp as auth_bp
    from .routes.admin_routes import bp as admin_bp
    from .routes.explorer_routes import bp as explorer_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(explorer_bp)

    return app
