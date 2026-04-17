# Module 10 - orchestrator

> Pipeline orchestrator. Connects modules 02 through 09 into an end-to-end pipeline, tracks per-page progress, and supports resume.

## 1. Purpose

The earlier modules can each work on their own, but **working individually** is not the same as **a working full pipeline**. This module is responsible for:

1. For a given `doc_id`, triggering stages 02 -> 03 -> 04 -> 05 -> 06 -> 07 -> 09 in order.
2. Scheduling by **page granularity**. A page can start classification as soon as OCR for that page finishes; it does not need to wait for the whole document OCR to finish.
3. Persisting state to `data/logs/<doc_id>/job.json` so a restarted process can resume.
4. Idempotency: stages with existing artifacts are skipped.
5. Treating intermediate artifacts, especially OCR text and per-page JSON, as first-class state visible in the dashboard.
6. Providing real-time status and log streams to the `web_app` dashboard.
7. Avoiding business logic. All processing is performed by modules 02 through 09; the orchestrator only schedules.

## 2. Input / Output

**Input**: `data/input_pdfs/<doc_id>.pdf`, or an already-ingested `data/pages/<doc_id>/` directory.

**Output**: final `data/output/<doc_id>/*.csv` files. These are written by the aggregator; the orchestrator only triggers it.

**Intermediate artifacts**: `data/logs/<doc_id>/`

```text
|-- job.json           # Current job state
|-- pipeline.log       # Human-readable log
`-- events.jsonl       # Event stream consumed by dashboard
```

## 3. Job State Model

```python
class Job(BaseModel):
    job_id: str                    # uuid4
    doc_id: str
    status: Literal["pending","running","paused","done","failed"]
    created_at: datetime
    updated_at: datetime
    total_pages: int
    pages: List[PageState]         # One item per page
    counters: dict                 # model_calls / elapsed / ...

class PageState(BaseModel):
    page: int
    ingest:   StageStatus
    ocr:      StageStatus
    classify: StageStatus
    names:    StageStatus
    meta:     StageStatus
    places:   StageStatus

class StageStatus(BaseModel):
    state: Literal["pending","running","done","skipped","failed"]
    started_at: Optional[datetime]
    finished_at: Optional[datetime]
    error: Optional[str]
    elapsed_seconds: float = 0
```

`aggregate` is job-level rather than page-level, so it appears as a separate field:

```python
class Job(BaseModel):
    ...
    aggregate: StageStatus
```

## 4. Core Scheduling Algorithm

Principle: the filesystem is the source of truth. Every time the orchestrator decides what to run next, it checks whether files exist. It does not trust only in-memory state, because the process may have restarted.

```python
def run_document(doc_id: str, resume: bool = True) -> Job:
    job = load_or_create_job(doc_id)
    paths = doc_paths(doc_id)

    # Stage 1: ingest, once per document
    if not paths.manifest().exists():
        call_module("pdf_ingest", {"doc_id": doc_id})
    job.total_pages = read_manifest(paths)["page_count"]

    # Stages 2-6: page-level processing, serial for now
    for p in range(1, job.total_pages + 1):
        run_page(doc_id, p, job)
        save_job(job)               # Persist after every page
        emit_event(job_id, "page_updated", p)

    # Stage 7: aggregate, once per document
    if any_intermediate_exists(paths):
        call_module("aggregator", {"doc_id": doc_id})
        job.aggregate.state = "done"

    job.status = "done"
    save_job(job)
    return job


def run_page(doc_id, p, job):
    paths = doc_paths(doc_id)
    state = job.pages[p-1]

    # OCR
    if not artifact_ok(paths.ocr_text(p), kind="ocr_text"):
        state.ocr.state = "running"; emit_stage_start(job, p, "ocr")
        try:
            call_module("ocr", {"doc_id": doc_id, "page": p})
            state.ocr.state = "done"
        except Exception as e:
            state.ocr.state = "failed"; state.ocr.error = str(e); raise
        emit_stage_end(job, p, "ocr")
    else:
        state.ocr.state = "done"        # skipped

    # classify
    if not artifact_ok(paths.classify(p), kind="json"):
        ...

    # If classify.should_extract == false, skip names/meta/places.
    decision = read_json(paths.classify(p))
    if not decision["should_extract"]:
        state.names.state = "skipped"
        state.meta.state = "skipped"
        state.places.state = "skipped"
        return

    # names
    if not artifact_ok(paths.names(p), kind="json"):
        ...

    # meta and places can run in parallel; both depend only on ocr_text and names.
    run_parallel([
        ("meta",   lambda: call_module("meta",   {"doc_id":doc_id,"page":p})),
        ("places", lambda: call_module("places", {"doc_id":doc_id,"page":p})),
    ])
```

Module communication: `call_module` is an abstraction. Prefer **HTTP** when modules run as independent services, and use **direct function calls** when everything is mounted in the main `web_app` process. The same scheduling logic works in both deployment modes.

`artifact_ok` should check more than existence. For text artifacts, require a non-empty file or the explicit `[OCR_EMPTY]` marker. For JSON artifacts, require that the file parses and contains the expected page number. Corrupt or partial files should trigger a rerun of that stage and any downstream stage that depends on it.

```python
# orchestrator/router.py
def call_module(name: str, payload: dict):
    if settings.ORCH_MODE == "http":
        url = MODULE_URLS[name]      # http://ocr:5103/ocr/run-single/...
        r = requests.post(url, json=payload, timeout=3600)
        r.raise_for_status()
        return r.json()
    else:
        # Direct core calls, faster in monolith mode with no HTTP serialization.
        return DISPATCH[name](payload)
```

## 5. Directory Structure

```text
src/orchestrator/
|-- __init__.py
|-- pipeline.py          # run_document / run_page
|-- job_store.py         # load_job / save_job, JSON files with atomic writes
|-- router.py            # call_module abstraction
|-- events.py            # emit_event to events.jsonl for SSE
|-- blueprint.py         # /orchestrate/* routes
|-- templates/
|   |-- dashboard.html   # Main dashboard page
|   `-- _partials/
|       |-- status_grid.html
|       `-- log_tail.html
|-- static/
|   |-- dashboard.css
|   `-- dashboard.js     # SSE + DOM updates
`-- tests/
    |-- test_pipeline_mocked.py
    `-- test_job_store.py
```

## 6. Blueprint API

| Method | Path | Behavior |
|---|---|---|
| GET | `/orchestrate/` | Dashboard UI |
| GET | `/orchestrate/jobs` | List all jobs |
| POST | `/orchestrate/run` | `{"doc_id":"..."}` -> start an async job and return `job_id` |
| POST | `/orchestrate/resume/<doc_id>` | Resume a previously interrupted job |
| POST | `/orchestrate/cancel/<job_id>` | Soft-cancel after the current stage finishes |
| GET | `/orchestrate/status/<doc_id>` | Current job state as `Job` JSON |
| GET | `/orchestrate/artifacts/<doc_id>/<page>` | Artifact state for one page: page image, OCR text, classify JSON, names JSON, meta JSON, places JSON |
| GET | `/orchestrate/stream/<doc_id>` | **SSE event stream**: `page_updated` / `log` / `done` |
| GET | `/orchestrate/log/<doc_id>` | Return the most recent N lines from `pipeline.log` |

SSE design: the front end subscribes with `new EventSource('/orchestrate/stream/myDoc')`. The server tails `events.jsonl` and converts it to SSE. This is simple and reliable, and it does not require WebSocket.

## 7. Dashboard UI

This is the most important UI in the project:

```text
+----------------------------------------------------------------------+
|  Pipeline dashboard - myDoc                         Status: running  |
|  [ Cancel ]   [ Pause ]   [ View logs ]                              |
+----------------------------------------------------------------------+
|  Overall progress                                                     |
|  ingest  100%                                                         |
|  ocr      72% (99/137)                                                |
|  classi.  60% (82/137)                                                |
|  names    45% (62/137)                                                |
|  meta     40% (55/137)                                                |
|  places   35% (48/137)                                                |
|  aggreg.   0%                                                         |
|  Counters: 412 model calls, 11 repair calls, elapsed 00:18:42         |
+----------------------------------------------------------------------+
|  Per-page status. Click any row or cell to open the module UI.        |
|  p | ing | ocr | cls | nm | meta | place | notes                     |
|  1 | done| done| done| -  | -    | -     | skipped: index page       |
|  2 | done| done| done|done| done | done  | 2 ppl, 5 places           |
| 12 | done| done| done|done|running|running| running...              |
| 13 | done| done|running|queued|queued|queued| queued                |
| 75 | done| failed|queued|queued|queued|queued| OCR failed: timeout  |
+----------------------------------------------------------------------+
|  Live log tail                                                        |
|  [18:42:13] page 12 meta done (4.1s, 1 call)                         |
|  [18:42:15] page 13 classify start                                   |
|  [18:42:17] page 12 places running (person 1/2)                      |
+----------------------------------------------------------------------+
```

Visualization goals:

1. **Stage-level progress bars** show which stage is slow, usually `name_extractor`.
2. **Per-page stage grid** gives a compact 137 pages by 6 stages view, making stuck pages obvious.
3. **Clickable rows/cells** jump to the corresponding module UI with the page preselected.
4. **Artifact panel** shows the actual files behind a page, including `pNNN.png`, `pNNN.txt`, and each JSON file, with size, modified time, and parse status.
5. **Unified status colors**: done / running / queued / skipped / failed.
6. **SSE real-time updates**: the page does not refresh; cells and log tail update live.
7. **Failure does not stop the whole run**: if one page fails at a stage, other pages continue. Failed pages are summarized at the end.

## 8. Docker

```yaml
  orchestrator:
    build:
      context: .
      dockerfile: docker/web.Dockerfile
    depends_on:
      ollama:
        condition: service_healthy
      pdf_ingest: { condition: service_started }
      ocr:        { condition: service_started }
      page_classifier: { condition: service_started }
      name_extractor:  { condition: service_started }
      metadata_extractor: { condition: service_started }
      place_extractor: { condition: service_started }
      aggregator: { condition: service_started }
    networks: [ llm_internal ]
    volumes:
      - ./data:/data
      - ./config:/app/config:ro
    environment:
      - ORCH_MODE=http
      - ORCH_MODULE_URLS_JSON={"ocr":"http://ocr:5103",...}
    profiles: [ "all" ]
    # Note: no ports; internal only, accessed through web_app proxy
```

In monolith mode, this independent container is not needed. Use `ORCH_MODE=inproc`, mount the blueprint in `web_app`, and call Python functions directly.

## 9. Tests

Unit tests with all `call_module` calls mocked:

- `test_pipeline_happy_path`: three pages all succeed.
- `test_pipeline_one_page_fails`: one page fails OCR and other pages continue.
- `test_resume_skips_done_pages`: the second run skips completed artifacts.
- `test_skip_reason_propagates`: when `classify.should_extract=false`, downstream stages are skipped.

Integration test:

- Run a two-page real PDF through the full pipeline and verify CSV row counts.

## 10. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Dashboard cells stay gray | SSE is not connected | Check that `web_app` proxy passes `/orchestrate/stream` as `text/event-stream` |
| One page stays `running` for a long time | The corresponding module HTTP call timed out | Check that module's container logs and increase `requests.post(timeout=)` |
| Resume reruns completed pages | Artifact file is missing or corrupt | Idempotency checks file exists, is non-empty, and can parse; bad files trigger rerun |
| `job.json` is corrupt | Partial write | `job_store` should use atomic writes; if unrecoverable, delete it and rerun |

## 11. Build Checklist

- [ ] `Job`, `PageState`, and `StageStatus` models are defined.
- [ ] `run_document` and `run_page` connect all stages.
- [ ] `call_module` supports both `http` and `inproc` modes.
- [ ] `job_store` writes atomically.
- [ ] `events.jsonl` and SSE stream work.
- [ ] Idempotency skips each stage based on artifact existence.
- [ ] Artifact validation checks non-empty OCR text and parseable JSON, not just file existence.
- [ ] Dashboard exposes artifact paths, sizes, timestamps, and parse status per page.
- [ ] `classify.should_extract=false` correctly propagates and skips names/meta/places.
- [ ] meta and places run in parallel.
- [ ] Dashboard UI includes overall progress, status grid, logs, and SSE.
- [ ] Status cells can jump to the corresponding module UI.
- [ ] Resume and cancel buttons work.
- [ ] Four typical unit tests pass.
- [ ] Two-page real PDF integration test passes.
