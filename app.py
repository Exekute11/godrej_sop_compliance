"""Godrej AI SOP Compliance Monitoring System - application factory and entry point."""
import atexit
import logging
import os
import sys

os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "2")
os.environ.setdefault("GLOG_minloglevel", "2")

# Suppress TensorFlow Lite, MediaPipe and Abseil native logs.
os.environ["TF_CPP_MIN_LOG_LEVEL"] = "3"
os.environ["GLOG_minloglevel"] = "3"
os.environ["ABSL_MIN_LOG_LEVEL"] = "3"

from flask import Flask, g, jsonify, request, session, render_template

from database import db
from models import users as users_model
from services.config_service import ConfigService
from services.monitor import CameraManager
from utils.security import csrf_token, check_csrf


def create_app(config_path=None, start_cameras=True, testing=False):
    app = Flask(__name__)
    cfg = ConfigService(config_path) if config_path else ConfigService()
    app.config.update(SECRET_KEY=os.environ.get("SOP_SECRET_KEY") or cfg.section("app").get("secret_key", "dev"),
                      SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
                      PERMANENT_SESSION_LIFETIME=60 * 60 * 12, TESTING=testing)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
        force=True
    )

    logging.getLogger("werkzeug").disabled = True
    # Hide repetitive Flask request logs.
    logging.getLogger("werkzeug").disabled = True
    # Hide repetitive Flask HTTP access logs.
    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    db.init_db()
    users_model.seed_default_users()
    from models import records
    records.current_shift()

    manager = CameraManager(cfg)
    app.extensions["cfg"] = cfg
    app.extensions["manager"] = manager
    if start_cameras:
        manager.start_all()
        atexit.register(manager.stop_all)

    from routes.auth import bp as auth_bp
    from routes.pages import bp as pages_bp
    from routes.api import bp as api_bp
    for bp in (auth_bp, pages_bp, api_bp):
        app.register_blueprint(bp)

    @app.before_request
    def load_user():
        g.user = users_model.get(session["uid"]) if "uid" in session else None
        if g.user and not g.user["active"]:
            session.clear()
            g.user = None
        check_csrf()

    @app.context_processor
    def inject():
        return {"csrf_token": csrf_token(), "me": g.get("user"), "app_name": cfg.section("app").get("name")}

    @app.after_request
    def headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        return resp

    @app.errorhandler(400)
    @app.errorhandler(403)
    @app.errorhandler(404)
    def err(e):
        if request.path.startswith("/api/"):
            return jsonify(error=getattr(e, "description", str(e))), e.code
        return render_template("error.html", code=e.code, message=getattr(e, "description", "")), e.code

    return app


if __name__ == "__main__":
    application = create_app()

    from flask import cli
    cli.show_server_banner = lambda *args, **kwargs: None

    a = application.extensions["cfg"].section("app")

    host = a.get("host", "0.0.0.0")
    port = int(
        os.environ.get(
            "PORT",
            a.get("port", 5000)
        )
    )

    print()
    print("=" * 60)
    print("GODREJ AI SOP MONITORING SYSTEM")
    print(f"Open dashboard: http://127.0.0.1:{port}")
    print("Press CTRL+C to stop")
    print("=" * 60)
    print()

    application.run(
        host=host,
        port=port,
        threaded=True,
        debug=False,
        use_reloader=False
    )
