# Overview - PDF-OCR-NER Extraction Pipeline (Refactor)

## 1. Project Purpose

This is an end-to-end extraction system for **historical slavery and manumission archival documents**. Given a scanned PDF, the system should:

1. Split the PDF into one image per page.
2. Run OCR on each page image to obtain text.
3. Use LLMs to extract from each page:
   - whether the page should be extracted, whether it is an index or bad-OCR page, and the report type
   - the **names of enslaved or manumitted subjects** mentioned on the page
   - each person's **case metadata**, including crime type, abuse, conflict type, trial outcome, and amount paid
   - each person's **place path**, including birthplace, place of capture, arrival place, transit places, and related dates
4. Normalize, deduplicate, and validate the extracted data.
5. Write final CSV files: `Detailed info.csv`, `name place.csv`, and `run_status.csv`.

The original system consisted of two monolithic Python scripts plus Docker Compose. The refactor splits it into a **modular Flask application** where each module can run independently, be tested independently, and expose a visual test UI.

---

## 1.1 Input Reality and Scale

The repository includes local sample PDFs that show the expected input shape:

- `sample input 1.pdf`: small sample, about 18 MB
- `sample input 2.pdf`: small sample, about 12 MB
- `full input.pdf`: fuller sample, about 303 MB

Real production PDFs can be **larger than 500 MB**. The design must therefore treat large PDFs as normal, not exceptional:

- The Web UI may support browser upload, but it must also support registering a file already placed in `data/input_pdfs/`. For very large files, folder-based registration is safer than pushing the entire PDF through a Flask request.
- Upload limits must be configurable and must not be hard-coded to 500 MB.
- Ingest must render pages one at a time and never load the whole PDF into memory as images.
- Every expensive stage must persist intermediate artifacts immediately so a crash or restart does not lose work.
- Tests must include small samples for quick feedback and at least one full-size smoke run that validates resume behavior, disk usage, and dashboard performance.

---

## 2. Core Design Goals

| Goal | Meaning |
|---|---|
| **Modularity** | Every stage is an independent module with its own directory, Dockerfile, tests, and UI. |
| **Independent execution** | Each module can start as its own container and complete its own stage of work. |
| **Flexible composition** | Modules exchange data through a file-system contract plus HTTP; they are not hard-coupled. |
| **Visual testing** | Every module exposes a Flask page showing inputs, intermediate artifacts, and outputs. |
| **Network isolation** | Ollama is never exposed externally; the Web UI binds only to `127.0.0.1`, not LAN. |
| **Offline runtime** | All runtime containers sit on an `internal: true` network with no internet access. |
| **Recoverability** | Modules are idempotent; after interruption, reruns skip completed artifacts. |
| **Artifact-first processing** | Important intermediate outputs, especially OCR text and per-page JSON, are written to disk and surfaced in the UI. |

---

## 3. Top-Level Architecture

### 3.1 Data Flow

```text
PDF file
  -> 02 pdf_ingest
     data/pages/<doc_id>/p001.png ...
  -> 03 ocr
     data/ocr_text/<doc_id>/p001.txt ...
  -> 04 page_classifier
     data/intermediate/<doc_id>/p001.classify.json
  -> 05 name_extractor
     data/intermediate/<doc_id>/p001.names.json
  -> 06 metadata_extractor
     data/intermediate/<doc_id>/p001.meta.json
  -> 07 place_extractor
     data/intermediate/<doc_id>/p001.places.json
  -> 08 normalizer
     name / place / date normalization, deduplication, validation
  -> 09 aggregator
     data/output/<doc_id>/
       Detailed info.csv
       name place.csv
       run_status.csv
```

Supporting services:

```text
01 ollama_gateway
  Ollama container + client contract
  Called by modules 03, 04, 05, 06, and 07 over HTTP.

10 orchestrator
  Schedules the whole pipeline and tracks per-page progress.

11 web_app
  Main Flask application on 127.0.0.1 only.
  Mounts all module blueprints and provides the main dashboard.

00 shared
  Shared Python library with OllamaClient, schemas, config, paths, storage, and I/O helpers.
```

### 3.2 Module List

| # | Module | Type | Independent Container? | Web UI? |
|---|---|---|---|---|
| 00 | `shared` | Python library, not a service | No | No |
| 01 | `ollama_gateway` | Infrastructure + client contract | Yes, Ollama itself | No |
| 02 | `pdf_ingest` | Processing module | Yes, optional | Yes |
| 03 | `ocr` | Processing module | Yes, optional | Yes |
| 04 | `page_classifier` | Processing module | Yes, optional | Yes |
| 05 | `name_extractor` | Processing module | Yes, optional | Yes |
| 06 | `metadata_extractor` | Processing module | Yes, optional | Yes |
| 07 | `place_extractor` | Processing module | Yes, optional | Yes |
| 08 | `normalizer` | Library + UI, pure Python | No | Yes |
| 09 | `aggregator` | Processing module | Yes, optional | Yes |
| 10 | `orchestrator` | Scheduler | Yes | Yes |
| 11 | `web_app` | Main Flask entry point | Yes, the only host-exposed service | Yes |

### 3.3 Inter-Module Contracts

All modules follow the same conventions:

1. **File-system data exchange** as the primary contract. Each module reads fixed directories and writes fixed directories with fixed formats. This is the default integration mode.
2. **HTTP blueprints** as a secondary contract. Each processing module implements REST endpoints that the orchestrator can trigger and monitor. This supports visualization and step-by-step debugging.
3. **CLI entry points** as a secondary contract. Each module can run as `python -m modules.<name> ...` with original-style command-line arguments. This supports debugging without Flask.

In other words: you can run the whole chain without the Web UI, run only OCR, or run the full workflow from the main Web UI. All three modes use the same core code.

---

## 4. Complete File Structure

```text
llm-pipeline/
|-- README.md
|-- .env.example
|-- .dockerignore
|-- .gitignore
|
|-- compose.seed.yaml              # One-time online Ollama model download
|-- compose.yaml                   # Offline runtime full stack
|-- compose.dev.yaml               # Development overlay with reload and source mounts
|
|-- docs/
|   |-- overview.md
|   |-- process.md
|   |-- 00_shared.md
|   |-- 01_ollama_gateway.md
|   |-- 02_pdf_ingest.md
|   |-- 03_ocr.md
|   |-- 04_page_classifier.md
|   |-- 05_name_extractor.md
|   |-- 06_metadata_extractor.md
|   |-- 07_place_extractor.md
|   |-- 08_normalizer.md
|   |-- 09_aggregator.md
|   |-- 10_orchestrator.md
|   `-- 11_web_app.md
|
|-- docker/                        # All Dockerfiles
|   |-- base.Dockerfile            # Shared base image
|   |-- ocr.Dockerfile             # Needs opencv
|   |-- ner.Dockerfile             # Lightweight, mostly requests
|   |-- ingest.Dockerfile          # Needs pymupdf/pdf2image
|   `-- web.Dockerfile             # Flask + gunicorn
|
|-- requirements/
|   |-- base.txt                   # requests, flask, pydantic
|   |-- ocr.txt                    # opencv-python-headless, numpy
|   |-- ingest.txt                 # pymupdf
|   |-- ner.txt                    # inherits base
|   `-- web.txt                    # flask, jinja2, gunicorn
|
|-- config/
|   |-- prompts/                   # Prompt templates as standalone files
|   |   |-- shared/
|   |   |   `-- json_repair.txt
|   |   |-- ocr/
|   |   |   `-- ocr.txt
|   |   |-- page_classifier/
|   |   |   `-- page_classify.txt
|   |   |-- name_extractor/
|   |   |   |-- name_pass.txt
|   |   |   |-- name_recall.txt
|   |   |   |-- name_filter.txt
|   |   |   `-- name_verify.txt
|   |   |-- metadata_extractor/
|   |   |   |-- category_guide.txt
|   |   |   `-- meta_pass.txt
|   |   `-- place_extractor/
|   |       |-- place_pass.txt
|   |       |-- place_recall.txt
|   |       |-- place_verify.txt
|   |       `-- place_date_enrich.txt
|   |-- schemas/                   # CSV column definitions and vocabulary
|   |   |-- detail.yaml
|   |   |-- place.yaml
|   |   |-- status.yaml
|   |   `-- vocab.yaml             # CRIME_TYPES, CONFLICT_TYPES, PLACE_MAP ...
|   `-- approved_model_tags.json
|
|-- src/
|   |-- shared/                    # 00 shared library, no Flask dependency
|   |-- modules/
|   |   |-- pdf_ingest/            # 02
|   |   |-- ocr/                   # 03
|   |   |-- page_classifier/       # 04
|   |   |-- name_extractor/        # 05
|   |   |-- metadata_extractor/    # 06
|   |   |-- place_extractor/       # 07
|   |   |-- normalizer/            # 08
|   |   `-- aggregator/            # 09
|   |-- orchestrator/              # 10
|   `-- web_app/                   # 11 Flask main app
|
|-- data/                          # All runtime data
|   |-- input_pdfs/                # User-provided PDFs, ignored by git
|   |-- pages/<doc_id>/            # Split PNG pages from pdf_ingest
|   |-- ocr_text/<doc_id>/         # Persisted OCR .txt outputs, one file per page
|   |-- intermediate/<doc_id>/     # Persisted per-page JSON from classifier, names, meta, places
|   |-- output/<doc_id>/           # Final CSV files
|   |-- logs/<doc_id>/             # Runtime logs and job state
|   `-- audit/<doc_id>/            # Optional prompt/response JSONL audit trail
|
|-- volumes/
|   `-- ollama/                    # Persistent Ollama models
|
`-- scripts/
    |-- seed_model.sh              # Pull a model
    |-- run_pipeline.sh            # Run a complete PDF
    `-- dev_up.sh                  # Start development mode
```

---

## 5. Technology Stack

| Layer | Choice | Reason |
|---|---|---|
| Python | 3.11 | Stable and compatible with the original code |
| Web | Flask + Jinja2 | Flask was requested; Jinja is enough without adding frontend framework complexity |
| Frontend | No SPA; Pico.css + vanilla JS | Simple, fast, and enough for visualization |
| PDF splitting | PyMuPDF (`fitz`) | Pure Python wheel, fast, no external executable |
| Images | opencv-python-headless | Already used by the original code |
| LLM | Ollama (`qwen2.5` / `mistral-small3.1` / `glm-ocr`) | Inherits the original architecture |
| Containers | Docker Compose v2 | Inherits the original architecture |
| Deployment | Compose profiles | Each module can be started independently |
| WSGI | gunicorn | Standard for production Flask |

---

## 6. Security Model

These are hard constraints for the whole project:

1. **Ollama is never exposed externally**. In runtime Compose, the `ollama` service has **no `ports:` field**. It exists only on the `llm_internal` `internal: true` network, and other containers access it through `http://ollama:11434`.
2. **Web UI is bound only to 127.0.0.1**. The only service reachable from the host is `web_app`, bound as `127.0.0.1:5000:5000`. Local browsers can open it; LAN and public networks cannot.
3. **Processing containers have no internet egress**. All `modules/*` services run on `internal: true` networks after dependencies are installed.
4. **Non-root user**. All containers run as `user: "10001:10001"`.
5. **Capability stripping**. Use `cap_drop: ALL` and `no-new-privileges:true`.
6. **Read-only input volumes**. PDF input volumes are mounted `:ro`.
7. **Special handling for seed mode**. Only the temporary seed phase has internet access for downloading models. Daily runtime does not run seed.

Network topology:

```text
Seed mode, one time:
  ollama_seed
  127.0.0.1:11434
  Internet access allowed only for model download.

Runtime mode:
  llm_internal (internal:true)
    ollama
    ocr / ner / other modules

  llm_frontend
    web_app
    127.0.0.1:5000
```

---

## 7. Three Runtime Modes

The same code supports three ways to run the system:

### Mode A: Full Web UI, daily use

```bash
docker compose up -d
# Open http://127.0.0.1:5000
# Upload PDF -> watch progress -> download CSV
```

### Mode B: Single-module standalone container for debugging one stage

```bash
# Start only Ollama and the OCR module UI
docker compose --profile ocr-only up -d ollama ocr
# Open http://127.0.0.1:5103 through the configured route/proxy for the OCR standalone UI
```

### Mode C: CLI-only, scripted use with no UI

```bash
docker compose run --rm ocr python -m modules.ocr.cli \
  --in_dir /data/pages/<doc_id> \
  --out_dir /data/ocr_text/<doc_id> \
  --model glm-ocr:latest
```

All three modes use the same `modules/ocr/core.py`; only the entry point differs.

---

## 8. Visual Testing Strategy

This is one of the key features of the refactor. Every processing module has a `/test` or module UI route with dedicated visual debugging:

| Module | Visual Output |
|---|---|
| 02 pdf_ingest | PDF thumbnail grid, page count, dimensions, file size summary |
| 03 ocr | Original image -> grayscale/enhanced -> deskew -> crop -> tiles; OCR text alignment; raw model response JSON |
| 04 page_classifier | Full OCR text, classification badges, highlighted evidence, raw responses |
| 05 name_extractor | Highlighted final and dropped names, five-stage cards with prompt/response inspection, removed-candidate reasons, rerun-stage controls |
| 06 metadata_extractor | Field cards with paired evidence, OCR highlighting, validation statuses, and prompt/response inspection for one selected person |
| 07 place_extractor | Ordered place path, highlighted evidence for each place, date-confidence blocks |
| 08 normalizer | Enter arbitrary names, places, dates, or text and see normalized output plus matched rules |
| 09 aggregator | Current CSV table preview, diff view for newly added rows, statistics panel |
| 10 orchestrator | Dashboard with one row per page, per-stage status lights, live log tail |
| 11 web_app | Combines all of the above, plus PDF upload, job management, and result downloads |

These UIs are not optional decoration. They are a first-class goal of the refactor. They let users validate each module visually instead of relying only on prints or guesses.

---

## 8.1 Intermediate Artifact Policy

Intermediate results are first-class outputs, not throwaway cache:

| Artifact | Location | Why It Must Persist |
|---|---|---|
| PDF source | `data/input_pdfs/<doc_id>.pdf` | Allows reruns and audit of the exact source file |
| Page images | `data/pages/<doc_id>/pNNN.png` | Avoids re-rendering large PDFs after OCR or LLM failures |
| OCR text | `data/ocr_text/<doc_id>/pNNN.txt` | Expensive to produce; must be inspectable and reused by every downstream module |
| Classifier JSON | `data/intermediate/<doc_id>/pNNN.classify.json` | Decides whether downstream extraction should run |
| Name JSON | `data/intermediate/<doc_id>/pNNN.names.json` | Stores pass-level extraction details and removed-candidate reasons |
| Metadata JSON | `data/intermediate/<doc_id>/pNNN.meta.json` | Stores field-level extracted rows and evidence |
| Place JSON | `data/intermediate/<doc_id>/pNNN.places.json` | Stores candidate, verified, and reconciled place rows |
| Final CSV | `data/output/<doc_id>/*.csv` | Deliverable outputs |
| Logs / job state | `data/logs/<doc_id>/` | Resume, dashboard, and debugging |

The dashboard and module UIs should always show whether each artifact exists, when it was written, and whether it is parseable. A "rerun this page/stage" action should replace only that stage's artifact and downstream artifacts that depend on it.

---

## 9. Success Criteria

The refactor is successful if all of the following are true:

1. Uploading one PDF runs end to end and produces CSVs equivalent to, or better than, the original script output.
2. Any module container can start independently and do its stage of work when given input.
3. Any module `/test` page shows the full inputs, outputs, and intermediate artifacts for a page, text segment, or person.
4. Ollama cannot be reached from the host or LAN.
5. The Web UI cannot be reached from LAN; only the local browser can access it.
6. Runtime processing requires no internet access.
7. If any pass fails, rerunning can resume from the breakpoint without reprocessing completed pages.

---

For the detailed build order, see [process.md](./process.md). For each module's detailed design, see the numbered module documents in this directory.
