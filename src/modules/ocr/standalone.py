from __future__ import annotations

import os

from flask import Flask, redirect, url_for

from .blueprint import bp


def create_app() -> Flask:
    app = Flask(__name__)
    app.secret_key = os.environ.get("FLASK_SECRET_KEY", "ocr-local-dev")
    app.register_blueprint(bp)

    @app.get("/")
    def root():
        return redirect(url_for("ocr.index"))

    @app.get("/healthz")
    def healthz():
        return {"status": "ok", "module": "ocr"}

    return app
