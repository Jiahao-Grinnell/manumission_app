# Module 11 - web_app

> Main Flask application. Mounts every module blueprint into one unified web UI, and is the **only** service that exposes a port to the host (`127.0.0.1` only).

Implementation status as of 2026-04-24: not yet implemented. Phase 5 currently uses the standalone orchestrator dashboard at `http://127.0.0.1:5110/orchestrate/`; this document remains the Phase 6 target design.

## 1. Purpose

Provide the user entry point:

- PDF upload
- registration of large PDFs already placed in `data/input_pdfs/`
- Dashboard, inherited from the orchestrator UI as the home page
- Navigation to each module's test UI
- Job list and history
- CSV downloads
- Global navigation and authentication

This layer contains **no business logic**. It only mounts blueprints, provides navigation, enforces access control, and starts gunicorn.

## 2. Two Deployment Modes

### 2.1 Monolith Mode (recommended for daily use)

The main `web_app` process mounts all blueprints from modules 02 through 10. One container runs everything except Ollama, which remains an independent container.

Benefits: no HTTP serialization overhead, simpler debugging, and lower resource usage.

Tradeoff: a crash in one module can affect the whole app.

### 2.2 Microservice Mode (for debugging or isolation)

Each module runs as its own container with its own blueprint and standalone app. `web_app` acts as a reverse proxy. The orchestrator uses `ORCH_MODE=http` and calls modules over the internal network.

Benefits: module isolation, independent scaling, and independent restarts.

Tradeoff: more deployment complexity and slower debugging.

The same code supports both modes. Switch with Compose profiles and the `ORCH_MODE` environment variable.

## 3. Directory Structure

```text
src/web_app/
|-- __init__.py
|-- app.py               # create_app() factory
|-- register.py          # Mount all blueprints
|-- routes.py            # Home / upload / jobs list / health checks
|-- auth.py              # Local-only access middleware
|-- errors.py            # Error handlers
|-- templates/
|   |-- base.html        # Global layout + navigation
|   |-- home.html        # Home page, same as dashboard
|   |-- upload.html
|   |-- inputs.html      # Register existing large PDFs from data/input_pdfs/
|   |-- jobs.html
|   `-- download.html
|-- static/
|   |-- pico.min.css     # Minimal CSS framework
|   |-- app.css
|   `-- app.js
|-- wsgi.py              # gunicorn entry point
`-- tests/
    |-- test_routes.py
    `-- test_auth.py
```

## 4. App Factory

```python
# app.py
from flask import Flask
from .register import register_blueprints
from .auth import local_only
from .errors import register_error_handlers
from shared.config import settings
from shared.logging_setup import setup_logger

def create_app() -> Flask:
    app = Flask(__name__, template_folder="templates", static_folder="static")
    # Configurable upload limit. Production inputs may exceed 500 MB,
    # so large files should also be supported through input-folder registration.
    app.config["MAX_CONTENT_LENGTH"] = settings.MAX_UPLOAD_BYTES
    app.config["UPLOAD_FOLDER"] = settings.DATA_ROOT / "input_pdfs"
    setup_logger("web", settings.DATA_ROOT / "logs")

    app.before_request(local_only)
    register_blueprints(app)
    register_error_handlers(app)

    from . import routes
    app.register_blueprint(routes.bp)
    return app
```

## 5. `register.py`

```python
def register_blueprints(app):
    # Each module exposes its own bp.
    from modules.pdf_ingest.blueprint import bp as ingest_bp
    from modules.ocr.blueprint import bp as ocr_bp
    from modules.page_classifier.blueprint import bp as classify_bp
    from modules.name_extractor.blueprint import bp as names_bp
    from modules.metadata_extractor.blueprint import bp as meta_bp
    from modules.place_extractor.blueprint import bp as places_bp
    from modules.normalizer.blueprint import bp as norm_bp
    from modules.aggregator.blueprint import bp as agg_bp
    from orchestrator.blueprint import bp as orch_bp

    app.register_blueprint(ingest_bp,   url_prefix="/ingest")
    app.register_blueprint(ocr_bp,      url_prefix="/ocr")
    app.register_blueprint(classify_bp, url_prefix="/classify")
    app.register_blueprint(names_bp,    url_prefix="/names")
    app.register_blueprint(meta_bp,     url_prefix="/meta")
    app.register_blueprint(places_bp,   url_prefix="/places")
    app.register_blueprint(norm_bp,     url_prefix="/normalizer")
    app.register_blueprint(agg_bp,      url_prefix="/aggregate")
    app.register_blueprint(orch_bp,     url_prefix="/orchestrate")
```

In microservice mode, `register.py` only mounts `orch_bp`; the others are reached through reverse proxy routes from `web_app` to the corresponding service.

## 6. `auth.py` - Local Access Restriction

Double protection, so LAN access still fails even if Compose is misconfigured:

```python
from flask import request, abort
from ipaddress import ip_address

def local_only():
    # For direct browser connections, remote_addr is the client IP.
    # With a proxy, X-Forwarded-For may exist, but our reverse proxy is inside the container network.
    remote = request.remote_addr
    if remote is None:
        abort(403)
    addr = ip_address(remote)
    if not (addr.is_loopback or addr.is_private):
        # Docker bridge addresses are usually 172.17.0.x and count as private, so they are allowed.
        # LAN or internet clients are rejected.
        abort(403)
```

Compose binding `127.0.0.1:5000:5000` is the first layer. `auth.py` is the second layer.

## 7. `routes.py`

| Method | Path | Page |
|---|---|---|
| GET | `/` | Home page, the dashboard |
| GET | `/upload` | PDF upload form |
| POST | `/upload` | Receive a file, save to `data/input_pdfs/`, and redirect to `/orchestrate/run?doc_id=...` |
| GET | `/inputs` | List PDFs already present in `data/input_pdfs/` |
| POST | `/inputs/register` | Register an existing PDF without streaming it through Flask |
| GET | `/jobs` | Historical job list |
| GET | `/download/<doc_id>` | Download CSVs for the document, through aggregator |
| GET | `/health` | Liveness, returns 200 |
| GET | `/ready` | Readiness, checks Ollama reachability and blueprint registration |

## 8. `base.html` Navigation

Shared navigation:

```html
<nav>
  <ul>
    <li><a href="/">Dashboard</a></li>
    <li><a href="/upload">Upload PDF</a></li>
    <li><a href="/inputs">Input PDFs</a></li>
    <li><a href="/jobs">Jobs</a></li>
  </ul>
  <ul>
    <li><strong>Modules</strong></li>
    <li><a href="/ingest/">Ingest</a></li>
    <li><a href="/ocr/">OCR</a></li>
    <li><a href="/classify/">Classify</a></li>
    <li><a href="/names/">Names</a></li>
    <li><a href="/meta/">Meta</a></li>
    <li><a href="/places/">Places</a></li>
    <li><a href="/normalizer/">Normalizer</a></li>
    <li><a href="/aggregate/">Aggregate</a></li>
  </ul>
</nav>
```

Each module's test UI is reachable through this navigation.

## 9. Docker

`docker/web.Dockerfile`:

```dockerfile
FROM llm-pipeline-base:latest
USER root
COPY requirements/web.txt /tmp/web.txt
RUN pip install --no-cache-dir -r /tmp/web.txt
# Monolith mode: include all module code
COPY src/shared /app/shared
COPY src/modules /app/modules
COPY src/orchestrator /app/orchestrator
COPY src/web_app /app/web_app
COPY config /app/config
USER 10001:10001
ENV PYTHONPATH=/app
ENV FLASK_APP=web_app.app:create_app
EXPOSE 5000
```

`compose.yaml` fragment:

```yaml
  web_app:
    build:
      context: .
      dockerfile: docker/web.Dockerfile
    depends_on:
      ollama:
        condition: service_healthy
    networks:
      - llm_internal
      - llm_frontend     # Unique to web_app
    ports:
      - "127.0.0.1:5000:5000"   # Bind localhost only, not 0.0.0.0
    volumes:
      - ./data:/data
      - ./config:/app/config:ro
    environment:
      - ORCH_MODE=inproc          # Monolith: direct function calls
      - OLLAMA_URL=http://ollama:11434/api/generate
      - OLLAMA_MODEL=qwen2.5:14b-instruct
    command: >
      gunicorn -b 0.0.0.0:5000 -w 4 --threads 2 --timeout 3600
      --access-logfile - --error-logfile -
      'web_app.app:create_app()'
    restart: unless-stopped

networks:
  llm_internal:
    internal: true
  llm_frontend:           # Only used to expose web_app 5000 to the host
    driver: bridge
```

Key points:

- `ports: "127.0.0.1:5000:5000"` binds localhost and **does not** bind `0.0.0.0`. LAN and public networks cannot reach it.
- gunicorn `-w 4 --threads 2`: four processes times two threads, suitable for Flask plus many simultaneously open dashboard pages.
- `--timeout 3600`: OCR calls can take minutes, so workers must not be killed early.
- `restart: unless-stopped`: automatic recovery.

## 10. Extra Configuration for Microservice Mode

Add `--profile micro` in `compose.yaml`, start all module services, and let `web_app` proxy:

```yaml
  web_app:
    ...
    environment:
      - ORCH_MODE=http
      - MODULE_URL_OCR=http://ocr:5103
      - MODULE_URL_CLASSIFY=http://page_classifier:5104
      ...
    profiles: [ "all", "micro" ]
```

When `register.py` detects `ORCH_MODE=http`, it stops mounting those blueprints directly and instead forwards `/ocr/*` and similar requests to the service, either with `flask-reverse-proxy` or a small custom proxy.

## 11. Startup Flow

First time:

```bash
./scripts/seed_model.sh qwen2.5:14b-instruct   # Download model; only this step needs internet
docker compose up -d                            # Start the full stack
# Open http://127.0.0.1:5000 in the browser
```

Daily use:

```bash
docker compose up -d
# Open http://127.0.0.1:5000
# Upload a PDF on /upload, or place a large PDF in data/input_pdfs/ and register it from /inputs
# Dashboard runs -> download CSVs from /download/<doc_id>
```

## 12. Tests

- `test_routes.py`: basic 200/302 checks; uploading a small PDF redirects to the dashboard.
- `test_inputs.py`: existing PDFs in `data/input_pdfs/` can be listed and registered without upload.
- `test_auth.py`: simulated LAN IP requests return 403; loopback requests return 200.
- `test_blueprints_registered.py`: verify all nine blueprints are mounted.

## 13. Observability

- `/health` and `/ready` are separate.
- Structured logs include `X-Request-Id`, generated by middleware.
- `/jobs` aggregates every `job.json` under `data/logs/`.

## 14. Security Checklist

- [ ] `ports:` is strictly `127.0.0.1:5000:5000`; do not use `0.0.0.0` or only `5000:5000`.
- [ ] `auth.local_only` works and remote IP requests receive 403.
- [ ] Uploaded file type is limited to `application/pdf`; upload size is configurable with `MAX_UPLOAD_BYTES`.
- [ ] PDFs larger than the browser upload limit can be processed through `data/input_pdfs/` registration.
- [ ] Upload filename uses `secure_filename`; path traversal is not allowed.
- [ ] gunicorn runs as a non-root user.
- [ ] `data/` is only mounted inside containers and is not otherwise exposed from the host.
- [ ] Ollama container has no `ports:`.

## 15. Build Checklist

- [ ] `create_app()` factory is implemented.
- [ ] `register_blueprints` mounts nine blueprints.
- [ ] `local_only` middleware works.
- [ ] `base.html` navigation includes all module links.
- [ ] Upload page starts a job and redirects smoothly to the dashboard.
- [ ] Existing-input registration starts a job for large PDFs without requiring browser upload.
- [ ] `/jobs` lists completed and running jobs.
- [ ] `/download/<doc_id>` downloads a zip.
- [ ] `127.0.0.1` access succeeds and LAN IP access fails, verified at both Compose binding and auth layers.
- [ ] gunicorn is stable with multiple workers.
- [ ] Both monolith and microservice modes start.
- [ ] End to end: upload or register a PDF, complete the full pipeline, download CSVs, and verify output is equivalent to the original script.
