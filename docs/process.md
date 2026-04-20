# Process - Build Order and Steps

This document explains **the order for building the system**, what each step should produce, and how to verify it. Following the order strictly ensures that every phase has something runnable, visible, and testable instead of debugging everything only after the full system is written.

## Guiding Principles

1. **Build from the bottom up**: start with the shared library and infrastructure, then the processing modules that depend on them, and finally the orchestration layer.
2. **Start simple, then add complexity**: implement non-LLM modules first, such as PDF splitting, normalization, and aggregation. They are easiest to write and test.
3. **Make every phase demoable**: after every phase, there should be something visible in the browser.
4. **Build test UI together with core logic**: do not write core logic first and come back later for the UI. Ship them together.
5. **Use Docker from day one**: do not run bare scripts first and containerize at the end. The first module should already run in Docker.
6. **Treat large PDFs as normal input**: real files may exceed 500 MB, so support both browser upload and registering files already placed in `data/input_pdfs/`.
7. **Persist important intermediate artifacts**: page images, OCR text, per-page JSON, logs, and final CSVs must all be durable, visible in the UI, and usable for resume.

---

## Phase 0: Project Skeleton (about 0.5 day)

Before writing any business logic, create the shell of the project.

### Tasks

| # | Task | Output |
|---|---|---|
| 0.1 | Create directory tree | Create every directory from the structure in `overview.md` |
| 0.2 | `.dockerignore` / `.gitignore` | Ignore `*.pdf`, `data/`, `volumes/`, `__pycache__` |
| 0.3 | `requirements/base.txt` | `requests`, `flask`, `jinja2`, `pydantic`, `gunicorn`, `pyyaml` |
| 0.4 | `docker/base.Dockerfile` | `python:3.11-slim` plus non-root user and base dependencies |
| 0.5 | Empty `compose.yaml` + `compose.seed.yaml` | Define only `ollama` and the two networks at first |
| 0.6 | `.env.example` and `config/` skeleton | Document environment variables clearly |
| 0.7 | `src/shared/config.py` | Centralize paths, model names, and timeout parameters |
| 0.8 | `src/shared/logging_setup.py` | Unified log format |

### Verification

```bash
docker compose -f compose.yaml config        # Compose syntax passes
docker build -f docker/base.Dockerfile .     # Base image builds
```

Do **not** skip this phase. Every later module inherits the base image, uses `shared/config.py`, and follows unified directory conventions. Weak foundations cause rework later.

---

## Phase 1: Shared Core Library (Module 00) + Ollama Gateway (Module 01)

Neither of these is a standalone "business service", but **all later modules depend on them**, so they must exist first.

### 1.1 Module 00: shared

Move the following pieces from the original `ner_extract.py` and `glm_ocr_ollama.py` into `src/shared/`:

- `OllamaClient`, including retries, timeouts, JSON extraction, and repair-prompt fallback
- JSON extraction utility `extract_json`
- Text cleanup utilities: `clean_ocr`, `normalize_ws`, `strip_accents`
- Path conventions in `paths.py`, where a given `doc_id` returns all related directories
- Atomic writes such as `write_csv_atomic`

Verification: write unit tests only. This phase has no UI.

```bash
pytest src/shared/tests/
```

### 1.2 Module 01: ollama_gateway

This module is the Ollama container itself plus documented usage of `OllamaClient`. It does not need new Python code. Write:

- Full `ollama` service definition in `compose.yaml`, including GPU by default, internal network, no ports, non-root user, capability dropping, and a read-only model volume.
- GPU verification for Docker Desktop / WSL, because the target workstation uses an NVIDIA GPU.
- `ollama_seed` service in `compose.seed.yaml`, temporarily bound to `127.0.0.1:11434` for model download.
- `scripts/seed_model.sh` to pull a model.
- Runtime health check using the Ollama CLI, plus independent CUDA-log and internal-network `GET /api/version` verification.

Verification:

```bash
./scripts/seed_model.sh qwen2.5:14b-instruct
docker compose up -d ollama
# Verify internal reachability and host isolation.
docker run --rm --network manumission_app_llm_internal curlimages/curl http://ollama:11434/api/version
curl http://127.0.0.1:11434/api/tags   # Should be connection refused
```

Milestone M1: Ollama is running, reachable internally, and unreachable from the host.

---

## Phase 2: Non-LLM Modules

These modules do not touch the LLM. Their logic is simpler, and they are the pipeline's input and output boundary, so finish them early.

### 2.1 Module 02: pdf_ingest

Status: implemented on 2026-04-20. The module is available as a CLI, a Flask blueprint under `/ingest/*`, and a standalone Compose service at `http://127.0.0.1:5102/ingest/` when started with the `ingest` profile.

- Function: PDF -> `data/pages/<doc_id>/p001.png, p002.png, ...` plus `manifest.json`.
- Implementation: PyMuPDF, render every page to a 300 DPI PNG.
- Support two input paths: browser upload for small/medium PDFs and existing-file registration for large PDFs already in `data/input_pdfs/`.
- Render one page at a time, update the manifest incrementally, and support resume from a partial manifest.
- Standalone UI: upload PDF form -> thumbnail grid after splitting.
- Blueprint path: `/ingest/*`.
- CLI: `python -m modules.pdf_ingest.cli --pdf path/to.pdf --doc-id myDoc`.

Verification: upload a small PDF with a few pages, see thumbnails, and confirm `data/pages/myDoc/` contains corresponding PNG files.

Also run an existing-file registration smoke test using one local sample PDF. For the fuller local PDF, run a page-range or interrupt/resume test instead of requiring the whole document in every dev loop.

Implemented verification:

```bash
docker build -f docker/ingest.Dockerfile -t manumission-ingest:phase2 .
docker run --rm manumission-ingest:phase2 python -m unittest discover -s /app/modules/pdf_ingest/tests -p "test_*.py"
docker compose --profile ingest up -d --build pdf_ingest
curl http://127.0.0.1:5102/healthz
```

### 2.2 Module 08: normalizer

Status: implemented on 2026-04-20. The module is importable as pure Python utilities, exposes JSON normalization endpoints, and has a standalone UI at `http://127.0.0.1:5108/normalizer/` when started with the `normalizer` profile.

Move normalization functions from `ner_extract.py` and split them into files:

- `names.py`: `normalize_name`, `is_valid_name`, `names_maybe_same_person`, `merge_named_people`, `name_compare_tokens`
- `places.py`: `normalize_place`, `is_valid_place`, `PLACE_MAP`
- `dates.py`: `to_iso_date`, `parse_first_date_in_text`, `extract_doc_year`
- `evidence.py`: `clean_evidence`, `normalize_for_match`

Standalone UI: a single-page form with inputs for name, place, date string, and free text. Show normalized results and matched rules in real time.

Verification: unit tests should cover original edge cases such as `"shargah" -> "Sharjah"` and `"17th May 1931" -> ISO`.

Implemented verification:

```bash
docker build -f docker/normalizer.Dockerfile -t manumission-normalizer:phase2 .
docker run --rm manumission-normalizer:phase2 python -m unittest discover -s /app/modules/normalizer/tests -p "test_*.py"
docker compose --profile normalizer up -d --build normalizer
curl http://127.0.0.1:5108/healthz
```

### 2.3 Module 09: aggregator

Status: implemented on 2026-04-20. The module reads per-page intermediate JSON, applies cross-page cleanup with `normalizer`, writes the three final CSVs plus `aggregation_summary.json`, exposes a CLI, and has a standalone UI at `http://127.0.0.1:5109/aggregate/` when started with the `aggregator` profile.

- Function: read per-page outputs from `data/intermediate/<doc_id>/*.json`, merge, deduplicate, and write final CSVs.
- Reuse `dedupe_place_rows`, `merge_named_people`, and related functions from normalizer.
- Standalone UI: current CSV preview, three statistic columns, and download buttons.

Verification: put fake `.json` files into `data/intermediate/demo/`, run aggregation, and confirm the CSVs match expected output.

Implemented verification:

```bash
docker build -f docker/aggregator.Dockerfile -t manumission-aggregator:phase2 .
docker run --rm manumission-aggregator:phase2 python -m unittest discover -s /app/modules/aggregator/tests -p "test_*.py"
docker compose --profile aggregator run --rm aggregator python -m modules.aggregator.cli --doc-id agg_smoke
docker compose --profile aggregator up -d --build aggregator
curl http://127.0.0.1:5109/healthz
```

Milestone M2: three modules can run independently and each has a visible UI. Ollama is not used yet.

---

## Phase 3: OCR Module (Module 03)

OCR is the first LLM module and the heaviest vision-processing stage, so it gets its own phase.

Status: implemented on 2026-04-20. The module exposes preprocessing, single-page OCR, whole-folder OCR, status, debug, and text routes under `/ocr/*`; has a CLI; and has a standalone UI at `http://127.0.0.1:5103/ocr/` when started with the `ocr` profile.

### Tasks

- Split image preprocessing from `glm_ocr_ollama.py` into `preprocessing.py`: `enhance_gray`, `deskew`, `crop_foreground`, `split_vertical_with_overlap`.
- Implement `core.py` with the `OllamaClient` vision endpoint.
- Use `docker/ocr.Dockerfile`, including opencv.
- Reuse resume logic such as `should_skip_existing`.
- Build a standalone UI:
  - left column lists all PNG pages in `data/pages/<doc_id>/`
  - selecting a page shows the preprocessing strip: original, enhanced, deskewed, cropped, and tiles
  - show each tile's OCR response
  - top-level "OCR all" button triggers the whole directory
- Treat `data/ocr_text/<doc_id>/pNNN.txt` as a first-class saved artifact. Every downstream module reads it, and the UI should show file status, character count, and link/download.
- Write an OCR manifest with per-page status so full-document runs can be resumed and audited.
- Blueprint path: `/ocr/*`.

### Verification

Implemented verification:

```bash
docker build -f docker/ocr.Dockerfile -t manumission-ocr:phase3 .
docker run --rm manumission-ocr:phase3 python -m unittest discover -s /app/modules/ocr/tests -p "test_*.py"
docker compose --profile ocr up -d --build ocr
curl http://127.0.0.1:5103/healthz
```

Preprocessing UI smoke test:

```bash
curl -X POST http://127.0.0.1:5103/ocr/preview/upload_fixture/1 \
  -H "Content-Type: application/json" \
  -d '{}'
```

Expected highlight: the response contains five base64 images for original, enhanced, deskewed, cropped, and tile preview.

Full OCR smoke test with the OCR vision model:

```bash
docker compose run --rm ocr python -m modules.ocr.cli \
  --in_dir /data/pages/upload_fixture --out_dir /data/ocr_text/upload_fixture \
  --model glm-ocr:latest \
  --ollama_url http://ollama:11434/api/generate \
  --no_debug \
  --max_new_tokens 1200
```

Expected output: `data/ocr_text/upload_fixture/manifest.json` reports `status: complete`, `completed_pages: 2`, and writes `p001.txt` plus `p002.txt`.

Then open the OCR UI, click a page, and visually inspect preprocessing artifacts and OCR output.

For large inputs, verify that stopping and restarting OCR skips existing `pNNN.txt` files and does not delete successful OCR text.

Milestone M3: the pipeline can run from PDF to OCR text.

---

## Phase 4: NER Extraction Modules (Modules 04/05/06/07)

This is the core business logic. Build modules one at a time in dependency order, each with a UI.

### 4.1 Module 04: page_classifier

The simplest LLM module: one prompt, one JSON response, one decision.

- `core.py`: `classify(ocr_text) -> PageDecision`.
- Load prompt from `config/prompts/page_classify.txt`.
- UI: text selector, full OCR text, JSON result, classification badge, and highlighted evidence.

Verification: run known statement, transport/admin, correspondence, index, and bad-OCR pages and check classification.

### 4.2 Module 05: name_extractor

This is the most complex module: four LLM stages plus rule filtering.

- Functions: `pass1_extract`, `pass2_recall`, `filter_candidates`, `verify_final`.
- Every stage should be separately testable and visible in the UI.
- Final rule filter `keep_subject_name` lives in `core.py`.
- UI:
  - text selector at the top
  - four panels for pass1, recall, filter, and verify
  - final output panel
  - removed candidates shown with reasons such as matched negative role pattern or not in subject group

### 4.3 Module 06: metadata_extractor

- Input `(ocr_text, name, page, report_type)` -> one detail row.
- Single LLM call with a fixed schema.
- UI:
  - select page and person
  - show five field cards: crime_type, whether_abuse, conflict_type, trial, amount_paid
  - show field-level evidence and link it to the original text

### 4.4 Module 07: place_extractor

- Three LLM rounds: candidate, recall/verify, and final verification, plus date enrichment and rule reconciliation.
- Similar complexity to `name_extractor`.
- UI:
  - select page and person
  - show ordered route cards, with order 1 -> 2 -> 3 and `order=0` separated as background
  - show evidence, date, and confidence for each place
  - allow switching between candidate, verified, and reconciled stages

### Verification for Phase 4

Each module should be run through its UI on 3 to 5 test pages with reasonable outputs. Finally, chain modules 04 through 07 for one document with CLI:

```bash
docker compose run --rm classifier python -m modules.page_classifier.cli ...
docker compose run --rm names python -m modules.name_extractor.cli ...
# and so on
```

Milestone M4: all business modules can each run and be inspected.

---

## Phase 5: Orchestration Layer (Module 10)

At this point, every module that does real work exists. Now connect them.

### Tasks

- `pipeline.py`: `run_document(doc_id)` calls modules in order through the file-system contract.
- `job_store.py`: simple job state management with JSON files; no database needed.
- Per-page progress tracking.
- Idempotency: if a module artifact already exists, skip the rerun.
- Blueprint `/orchestrate/*`:
  - `POST /orchestrate/run`: start a job
  - `GET /orchestrate/status/<job_id>`: current status
  - `GET /orchestrate/stream/<job_id>`: SSE stream with live logs
- Dashboard UI:
  - one row per page
  - six status cells per row: ingest, ocr, classify, names, meta+places, aggregate
  - clicking a status cell opens the corresponding module UI for that page
  - live log tail in the lower-right area

### Verification

Upload a real PDF, run it end to end, and watch the dashboard. Each status cell should turn done in order, and final CSV files should be written.

Milestone M5: end-to-end execution works.

---

## Phase 6: Main Web App (Module 11)

Wrap everything into a user-facing Flask application.

### Tasks

- `create_app()` factory: mount all blueprints, register error handlers and logging.
- Shared `base.html`: top navigation linking all modules.
- Dashboard page as the home page, inherited from orchestrator.
- File upload page that calls the `pdf_ingest` API.
- Input registration page that lists local PDFs in `data/input_pdfs/` and starts jobs without uploading them through Flask.
- Job list page listing completed docs under `data/output/`.
- Results download page for the three CSVs for each doc.
- Production container: gunicorn with multiple workers, bound only to `127.0.0.1:5000`.
- Optional local access control: reject requests not coming from local/private Docker addresses.

### Verification

```bash
docker compose up -d
# Open http://127.0.0.1:5000
# Confirm another machine cannot reach port 5000
```

Milestone M6: a polished, deliverable system.

---

## Test Data Tiers

Use three levels of tests so development remains fast while still covering the real input shape:

| Tier | Source | Purpose | When to Run |
|---|---|---|---|
| Tiny fixture | Generated 1-2 page PDFs in test fixtures | Unit and CI tests | Every commit |
| Sample input | Local `sample input 1.pdf` / `sample input 2.pdf` | Real scan shape, quick smoke checks | During module work |
| Full input | Local `full input.pdf` or a production-sized PDF over 500 MB | Disk, resume, dashboard, and long-run behavior | Manual milestone tests |

Sample and full PDFs are local data and must not be committed to git. `.gitignore` excludes `*.pdf`, `data/`, and `volumes/`.

---

## Phase 7: Polish (Optional)

These are useful additions if time allows:

- **Performance**: concurrent OCR, processing N images at a time, or LLM batching.
- **Recoverability**: kill the run halfway and verify restart resumes from the breakpoint.
- **Audit logs**: record every LLM prompt and response to JSONL.
- **Export formats**: add JSON Lines and Parquet in addition to CSV.
- **Comparison tools**: UI for comparing old and new CSVs, plus validator module.
- **Regression tests**: freeze outputs from the original scripts on a test dataset as golden files and require the refactor to match.

---

## Build Order Summary

```text
M0: skeleton
  -> M1: Ollama
  -> M2: three non-LLM modules
  -> M3: OCR
  -> M4: four NER modules
  -> M5: orchestrator
  -> M6: main Web App
  -> M7: polish
```

The project is split into seven phases, each with a clear demoable result. This keeps progress calm and observable.

---

## Acceptance Checklist by Phase

### M1 Ollama

- [ ] `docker compose up -d ollama` succeeds.
- [ ] `curl http://127.0.0.1:11434/api/tags` fails.
- [ ] Internal curl works.
- [ ] At least one model has been pulled.

### M2 Non-LLM Modules

- [x] `pdf_ingest` UI can upload a PDF and show thumbnails.
- [x] `pdf_ingest` can register an existing local PDF from `data/input_pdfs/`.
- [x] Partial large-PDF ingest can resume from the manifest without rerendering completed pages.
- [x] `normalizer` UI can demonstrate rules in real time.
- [x] `aggregator` UI can show and download CSVs generated from fake data.
- [x] All three modules run through `docker compose run --rm <service> ...`.

### M3 OCR

- [x] OCR UI shows the five-step preprocessing strip for a selected page.
- [x] Whole-folder OCR code path writes to `data/ocr_text/` and is covered by mocked unit tests.
- [x] OCR text files are visible in the UI and exposed through `/ocr/text/<doc_id>/<page>`.
- [x] Resume behavior works.
- [x] Full OCR smoke test with `glm-ocr:latest` downloaded and loaded in runtime Ollama.

### M4 Four NER Modules

- [ ] Each module has its own UI and can show results for a selected page.
- [ ] At least five test pages are manually reviewed with reasonable outputs.
- [ ] Each module's CLI runs independently.

### M5 Orchestration

- [ ] Dashboard can upload a PDF and run end to end.
- [ ] Each page's six status cells turn done in order.
- [ ] Killing and restarting midway resumes the run.

### M6 Main Web App

- [ ] Home page is the dashboard.
- [ ] `/inputs` can register large PDFs without browser upload.
- [ ] LAN access fails and `127.0.0.1` access succeeds.
- [ ] All three CSV files can be downloaded.
- [ ] gunicorn runs with multiple workers.

---

## Common Pitfalls

1. **Do not write business logic before Phase 1**. A weak foundation causes continuous rework.
2. **OCR prompts are unstable**. Vision models may return markdown fences or extra explanations. Preserve the original `cleanup_ocr_text` handling.
3. **LLM JSON often has missing or extra fields**. Preserve the `OllamaClient.generate_json` fallback that repairs bad JSON with a repair prompt.
4. **Paths inside containers are always `/data/...`**. Do not hard-code host paths in Python; use `shared/config.py`.
5. **Windows line endings**. If the original `compose.yaml` uses `\r\n`, normalize to LF during migration to avoid strange docker-compose errors.
6. **Non-root users and file writes**. Container uid `10001` must be able to write under `data/`. Before starting Compose, run `chown -R 10001:10001 data/` or use an init container.
7. **`internal: true` networks cannot resolve external DNS**. This means `pip install` must happen during image build; runtime containers cannot download packages.

---

Next, read the detailed design document for each module in `docs/*.md`.
