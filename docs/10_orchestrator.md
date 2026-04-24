# Module 10 - orchestrator

> Pipeline orchestrator. Connects modules 02 through 09 into an end-to-end pipeline, tracks per-page progress, and supports resume.

Implementation status as of 2026-04-24: standalone dashboard, persistent `job.json` store, `pipeline.log`, `events.jsonl`, background execution, server-rendered initial job state, live SSE updates with polling fallback, visible dashboard connection/error status, stale-job coercion from `running` to `paused` after restart or lost worker thread, `POST /orchestrate/run`, `POST /orchestrate/resume/<doc_id>`, `POST /orchestrate/cancel/<job_id>`, `GET /orchestrate/status/<job_id>`, `GET /orchestrate/stream/<job_id>`, artifact inspection, and unit tests are implemented in `src/orchestrator/`. The current runtime is standalone `ORCH_MODE=inproc`; HTTP dispatch and main `web_app` integration remain future Phase 6 work.

## 1. Purpose

The earlier modules can each work on their own, but **working individually** is not the same as **a working full pipeline**. This module is responsible for:

1. For a given `doc_id`, triggering stages 02 -> 03 -> 04 -> 05 -> 06 -> 07 -> 09 in order.
2. Persisting **page-granular status** so the dashboard can show which page is in which stage, even though the current pipeline executes stage-by-stage across the document.
3. Persisting state to `data/logs/<doc_id>/job.json` so a restarted process can resume.
4. Idempotency: stages with existing artifacts are skipped.
5. Treating intermediate artifacts, especially OCR text and per-page JSON, as first-class state visible in the dashboard.
6. Providing real-time status and log streams to the standalone orchestrator dashboard now, and later to `web_app`.
7. Avoiding business logic. All processing is performed by modules 02 through 09; the orchestrator only schedules.

## 2. Input / Output

**Input**: `data/input_pdfs/<doc_id>.pdf`, an uploaded PDF passed to `POST /orchestrate/run`, or an already-ingested `data/pages/<doc_id>/` directory.

**Output**: final `data/output/<doc_id>/*.csv` files. These are written by the aggregator; the orchestrator only triggers it.

**Intermediate artifacts**: `data/logs/<doc_id>/`

```text
|-- job.json           # Current job state
|-- pipeline.log       # Human-readable log
`-- events.jsonl       # Event stream consumed by dashboard SSE
```

## 3. Job State Model

The persisted state is JSON on disk, not a database. A representative shape is:

```json
{
  "job_id": "sample_input_1-20260424-221530",
  "doc_id": "sample_input_1",
  "status": "running",
  "current_stage": "metadata_extractor",
  "cancel_requested": false,
  "created_at": "2026-04-24T22:15:30Z",
  "updated_at": "2026-04-24T22:19:41Z",
  "total_pages": 10,
  "completed_pages": 6,
  "pages": [
    {
      "page": 1,
      "ingest": "done",
      "ocr": "done",
      "classify": "done",
      "names": "skipped",
      "meta_places": "skipped",
      "aggregate": "pending",
      "notes": "classify.should_extract=false"
    }
  ],
  "aggregate": {
    "state": "pending",
    "error": ""
  },
  "log_tail": [
    "[22:19:12] metadata_extractor page 6 done",
    "[22:19:41] place_extractor page 4 running"
  ],
  "errors": []
}
```

The exact JSON may grow over time, but these are the fields the current dashboard and tests rely on: job identity, overall status, current stage, per-page stage cells, aggregate stage, recent log lines, and error list. If the standalone service restarts and the job can no longer find its worker thread, the stored state is rewritten from `running` to `paused`, a recovery message is appended to `errors` and `log_tail`, and the operator resumes from the dashboard rather than leaving a stale `running` badge forever.

## 4. Core Scheduling Algorithm

Principle: the filesystem is the source of truth. Every time the orchestrator decides what to run next, it checks whether files exist. It does not trust only in-memory state, because the process may have restarted.

```python
def run_document(doc_id: str, resume: bool = True) -> Job:
    job = load_or_create_job(doc_id)
    run_ingest(job, doc_id)
    run_ocr_stage(job, doc_id)
    run_classify_stage(job, doc_id)
    propagate_classify_skips(job, doc_id)
    run_names_stage(job, doc_id)
    run_metadata_stage(job, doc_id)
    run_places_stage(job, doc_id)
    run_aggregate(job, doc_id)
    finalize_job(job)
    return job
```

The current implementation is intentionally simpler than the original design sketch:

1. It runs **stage-by-stage across the document**, not page-interleaved scheduling.
2. `metadata_extractor` and `place_extractor` run as separate sequential stages.
3. The dashboard still shows **page-level live progress**, because each stage updates page cells as work completes.

Module communication is still abstracted behind `router.py`, but the current Phase 5 implementation only uses **direct in-process dispatch**. HTTP dispatch is deferred until the main `web_app` / multi-service integration phase.

```python
# orchestrator/router.py
def call_module(name: str, payload: dict):
    if settings.ORCH_MODE != "inproc":
        raise NotImplementedError("Phase 5 currently supports inproc dispatch only")
    return DISPATCH[name](payload)
```

## 5. Directory Structure

```text
src/orchestrator/
|-- __init__.py
|-- standalone.py        # Standalone Flask entry point on :5110
|-- pipeline.py          # run_document and stage runners
|-- job_store.py         # load_job / save_job, JSON files with atomic writes
|-- router.py            # inproc dispatch abstraction
|-- blueprint.py         # /orchestrate/* routes
|-- templates/
|   `-- dashboard.html   # Main dashboard page
|-- static/
|   |-- dashboard.css
|   `-- dashboard.js     # server-rendered hydration, SSE, and polling fallback
`-- tests/
    |-- test_blueprint.py
    |-- test_pipeline_mocked.py
    `-- test_job_store.py
```

## 6. Blueprint API

| Method | Path | Behavior |
|---|---|---|
| GET | `/orchestrate/` | Dashboard UI |
| GET | `/orchestrate/jobs` | List all jobs |
| POST | `/orchestrate/run` | Start an async job from either an uploaded PDF or a registered `source_pdf`, then return `job_id` |
| POST | `/orchestrate/resume/<doc_id>` | Resume a previously interrupted job |
| POST | `/orchestrate/pause/<job_id>` | Soft-pause after the current stage finishes |
| POST | `/orchestrate/cancel/<job_id>` | Soft-cancel after the current stage finishes |
| POST | `/orchestrate/clear-results/<doc_id>` | Delete generated pages, OCR text, intermediate JSON, outputs, logs, and audit files for one document while keeping the source PDF |
| GET | `/orchestrate/status/<job_id>` | Current job state as JSON |
| GET | `/orchestrate/artifacts/<job_id>/<page>` | Artifact state for one page: page image, OCR text, classify JSON, names JSON, meta JSON, places JSON |
| GET | `/orchestrate/outputs/<job_id>` | Preview the current final CSV outputs plus aggregation summary |
| GET | `/orchestrate/download/<job_id>/<kind>` | Download one final output file |
| GET | `/orchestrate/stream/<job_id>` | **SSE live-update channel** |
| GET | `/orchestrate/log/<job_id>` | Return the most recent N lines from `pipeline.log` |

Dashboard refresh design:

- The initial HTML page is server-rendered with the currently selected job summary, progress bars, per-page table, and log tail already filled in.
- The front end then subscribes with `new EventSource('/orchestrate/stream/<job_id>')`. The server tails `events.jsonl` and converts it to SSE.
- If SSE is interrupted or the browser lacks `EventSource`, the dashboard falls back to polling `GET /orchestrate/status/<job_id>` so the page keeps moving without a full reload.
- The dashboard surfaces its live-update state directly in the UI, for example `Live updates connected.`, `Live stream disconnected. Retrying...`, or `Using polling fallback for live updates.`

## 7. Dashboard UI

This is the most important UI in the current Phase 5 implementation:

```text
+----------------------------------------------------------------------+
|  Pipeline dashboard - myDoc                         Status: running  |
|  [ Resume ]   [ Pause ]   [ Cancel ]   [ Clear Results ]             |
+----------------------------------------------------------------------+
|  Overall progress                                                     |
|  ingest  100%                                                         |
|  ocr      72% (99/137)                                                |
|  classi.  60% (82/137)                                                |
|  names    45% (62/137)                                                |
|  meta+pl  40% (55/137)                                                |
|  aggreg.   0%                                                         |
+----------------------------------------------------------------------+
|  Per-page status. Click any row or cell to open the module UI.        |
|  p | ing | ocr | cls | nm | meta+pl | agg | notes                    |
|  1 | done| done| done| -  | -       | -   | skipped: index page      |
|  2 | done| done| done|done| done    | -   | 2 ppl, 5 places          |
| 12 | done| done| done|done|running  | -   | running...               |
| 13 | done| done|running|queued|queued| -  | queued                   |
| 75 | done| failed|queued|queued|queued| - | OCR failed: timeout      |
+----------------------------------------------------------------------+
|  Live log tail                                                        |
|  [18:42:13] page 12 metadata_extractor done                           |
|  [18:42:15] page 13 page_classifier start                             |
|  [18:42:17] page 12 place_extractor running                           |
+----------------------------------------------------------------------+
|  Final outputs                                                        |
|  Detailed info.csv   [Download]   preview first rows                  |
|  name place.csv      [Download]   preview first rows                  |
|  run_status.csv      [Download]   preview first rows                  |
+----------------------------------------------------------------------+
```

Visualization goals:

1. **Stage-level progress bars** show where the run is spending time.
2. **Per-page stage grid** makes stuck pages obvious.
3. **Server-rendered first paint** means the current job summary, per-page table, and live log tail are visible immediately on page load before any client refresh code runs.
4. **Clickable rows/cells** jump to the corresponding standalone module UI with the page preselected.
5. **Artifact panel** shows the actual files behind a page, including `pNNN.png`, `pNNN.txt`, and each JSON file.
6. **Unified status colors**: done / running / queued / skipped / failed.
7. **Live refresh with fallback** uses SSE when available and polling when SSE is interrupted, so the page does not depend on one transport.
8. **Visible client status and error state** make it obvious whether the dashboard is currently connected, retrying, or using fallback refresh.
9. **Pause, resume, cancel, and clear-results controls** let the operator stop safely, continue from saved artifacts, or wipe generated artifacts for the current document.
10. **Final output preview** shows the current `Detailed info.csv`, `name place.csv`, `run_status.csv`, and aggregation summary without leaving the dashboard.

## 8. Docker

```yaml
  orchestrator:
    build:
      context: .
      dockerfile: docker/web.Dockerfile
    volumes:
      - ./data:/data
      - ./config:/app/config:ro
      - ./src:/app/src:ro
    environment:
      - ORCH_MODE=inproc
    ports:
      - "127.0.0.1:5110:5110"
    command: >
      gunicorn -b 0.0.0.0:5110 -w 1 --worker-class gthread --threads 8 --timeout 1800
      'orchestrator.standalone:create_app()'
    profiles: [ "orchestrator" ]
```

This is the current standalone Phase 5 service. The threaded Gunicorn worker is intentional: the long-lived `/orchestrate/stream/<job_id>` SSE connection must not block normal `/status`, `/outputs`, or dashboard page requests while a run is active. Future Phase 6 work may either mount this blueprint inside `web_app` or add true HTTP dispatch for separately running module services.

## 9. Tests

Automated coverage currently includes:

- `test_pipeline_mocked.py`: happy path, resume/idempotency, skip propagation, and failure handling with mocked module calls.
- `test_job_store.py`: JSON persistence, append-only event/log behavior, and state reload.
- `test_blueprint.py`: run/resume/pause/cancel/clear/status/log/artifact/output endpoints plus dashboard responses.

Still recommended as manual verification:

- Run a real PDF through the standalone dashboard and confirm final CSVs plus live page-cell updates.

## 10. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Dashboard looks stuck even though the backend is progressing | SSE is interrupted or the browser is on polling fallback | Check the dashboard client-status message, confirm `GET /orchestrate/status/<job_id>` changes, and allow one polling interval before assuming the run is frozen |
| Live log tail shows no updates | No new log lines have been emitted yet, or the live stream disconnected and the page is refreshing through fallback | Check `GET /orchestrate/log/<job_id>` or `GET /orchestrate/status/<job_id>`, and watch for the dashboard status message to switch back to `Live updates connected.` |
| Per-page table does not refresh immediately | The dashboard is between polling intervals or waiting for the next page/status event | Wait a few seconds, then refresh `GET /orchestrate/status/<job_id>` directly to distinguish UI lag from pipeline lag |
| Job unexpectedly shows `paused` after a restart | The orchestrator service restarted or lost its worker thread, so a stale `running` job was coerced to `paused` for safety | Click `Resume`; existing artifacts are reused and completed work is not discarded |
| One page stays `running` for a long time | The underlying in-process module call is still executing or blocked | Check `pipeline.log`, then inspect the linked standalone module UI for that page |
| Resume reruns completed pages | Artifact file is missing or corrupt | Idempotency checks file exists, is non-empty, and can parse; bad files trigger rerun |
| `job.json` is corrupt | Partial write | `job_store` uses atomic writes; if unrecoverable, delete it and rerun |

## 11. Build Checklist

- [x] `job_store` writes atomically.
- [x] `run_document` connects ingest, OCR, classify, names, metadata, places, and aggregate.
- [x] `router.py` supports current `inproc` dispatch.
- [x] `events.jsonl` and the SSE live-update channel work.
- [x] Idempotency skips each stage based on artifact existence.
- [x] Artifact validation checks non-empty OCR text and parseable JSON, not just file existence.
- [x] Artifact inspection endpoint is exposed per page.
- [x] `classify.should_extract=false` correctly propagates and skips names/meta/places.
- [x] Dashboard UI includes overall progress, status grid, logs, server-rendered initial state, SSE, and polling fallback.
- [x] Status cells can jump to the corresponding standalone module UI.
- [x] Resume, pause, cancel, and clear-results buttons work.
- [x] Final output preview and download routes work.
- [x] Unit tests for pipeline, job store, and blueprint pass.
- [ ] `call_module` supports true `http` mode.
- [ ] Two-page real PDF end-to-end integration test is automated.
