from __future__ import annotations

import os

from flask import Flask, redirect, url_for

from .blueprint import bp


def create_app() -> Flask:
    app = Flask(__name__)
    app.secret_key = os.environ.get("FLASK_SECRET_KEY", "orchestrator-local-dev")
    app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0
    app.register_blueprint(bp)

    @app.get("/")
    def root():
        return redirect(url_for("orchestrator.index"))

    @app.get("/healthz")
    def healthz():
        return {"status": "ok", "module": "orchestrator"}

    @app.after_request
    def disable_cache(response):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        return response

    return app
