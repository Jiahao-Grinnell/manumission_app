from __future__ import annotations

import os

from flask import Flask, redirect, url_for

from shared.config import settings

from .blueprint import bp


def create_app() -> Flask:
    app = Flask(__name__)
    app.secret_key = os.environ.get("FLASK_SECRET_KEY", "pdf-ingest-local-dev")
    app.config["MAX_CONTENT_LENGTH"] = settings.MAX_UPLOAD_BYTES
    app.register_blueprint(bp)

    @app.get("/")
    def root():
        return redirect(url_for("pdf_ingest.index"))

    @app.get("/healthz")
    def healthz():
        return {"status": "ok", "module": "pdf_ingest"}

    return app
