# Running the Project on Windows

This guide assumes:

- You are on Windows.
- Docker Desktop is installed and running.
- Ubuntu for Windows / WSL is installed.
- This repository is checked out on your Windows filesystem.

The application is built module by module. Early phases will not have every screen available yet. When a module is completed, its visual test UI should become reachable through the main Web UI or through its standalone Compose profile.

## 1. Local Files and Large PDFs

Do not commit input PDFs. The repo ignores `*.pdf`, `data/`, and `volumes/`.

Recommended layout for large inputs:

```text
data/
  input_pdfs/
    my_large_document.pdf
  pages/
  ocr_text/
  intermediate/
  output/
  logs/
  audit/
```

For PDFs larger than the browser upload limit, put the file directly in `data/input_pdfs/` and register it from the `/inputs` page once the Web App module is available.

## 2. First-Time Model Setup

Only this step needs internet access:

```bash
./scripts/seed_model.sh qwen2.5:14b-instruct
```

On Windows PowerShell, run the same through WSL if shell permissions are awkward:

```powershell
wsl bash ./scripts/seed_model.sh qwen2.5:14b-instruct
```

The model is stored under `volumes/ollama/`.

## 3. Start the Runtime Stack

After the model is seeded:

```bash
docker compose up -d
```

The main app, once module 11 is implemented, will be available at:

```text
http://127.0.0.1:5000
```

It is intentionally bound to localhost only.

## 4. Visual Test Pages by Module

When the main Web App is complete, module UIs should be reachable from the navigation bar:

| Module | URL | What You Should See |
|---|---|---|
| pdf_ingest | `http://127.0.0.1:5000/ingest/` | Upload/register PDF, page thumbnails, manifest |
| ocr | `http://127.0.0.1:5000/ocr/` | Page picker, preprocessing strip, OCR text, raw model responses |
| page_classifier | `http://127.0.0.1:5000/classify/` | OCR text, extract/skip verdict, report type, evidence |
| name_extractor | `http://127.0.0.1:5000/names/` | Multi-pass name extraction, highlights, dropped-candidate reasons |
| metadata_extractor | `http://127.0.0.1:5000/meta/` | Per-person case metadata fields and evidence |
| place_extractor | `http://127.0.0.1:5000/places/` | Ordered place route, dates, confidence, evidence |
| normalizer | `http://127.0.0.1:5000/normalizer/` | Name/place/date normalization playground |
| aggregator | `http://127.0.0.1:5000/aggregate/` | CSV preview, stats, downloads |
| orchestrator | `http://127.0.0.1:5000/orchestrate/` | End-to-end dashboard, per-page status, log tail |

## 5. Standalone Module UIs

During development, a module can also run as a standalone service once its Compose profile exists. The exact profile names may change as modules are implemented, but the pattern is:

```bash
docker compose --profile standalone up -d ollama <module_service>
```

Then open the module through the configured local route or the main `web_app` proxy.

For example, after OCR is implemented:

```bash
docker compose --profile standalone up -d ollama ocr
```

Expected OCR page:

```text
http://127.0.0.1:5000/ocr/
```

## 6. Running Tests

Phase 1.1 shared-library tests do not require Ollama:

```bash
docker build -f docker/base.Dockerfile -t manumission-base:phase1 .
docker run --rm manumission-base:phase1 python -m unittest discover -s /app/shared/tests -p "test_*.py"
```

If you are running from PowerShell and Docker access is blocked by permissions, run the same commands from an elevated terminal or through WSL.

## 7. Typical Development Loop

1. Implement one module.
2. Run that module's unit tests.
3. Start Docker services for that module.
4. Open the module's visual test UI.
5. Test on a tiny fixture first.
6. Test on `sample input 1.pdf` or `sample input 2.pdf`.
7. For milestone checks, test with `full input.pdf` or another large PDF and verify resume behavior.

## 8. Useful Commands

```bash
docker compose ps
docker compose logs -f ollama
docker compose down
docker compose -f compose.yaml config
docker compose -f compose.seed.yaml --profile seed config
```

## 9. Where Results Are Stored

| Artifact | Path |
|---|---|
| Input PDFs | `data/input_pdfs/` |
| Rendered page PNGs | `data/pages/<doc_id>/` |
| OCR text | `data/ocr_text/<doc_id>/pNNN.txt` |
| Intermediate JSON | `data/intermediate/<doc_id>/` |
| Final CSVs | `data/output/<doc_id>/` |
| Logs and job state | `data/logs/<doc_id>/` |
| Optional prompt/response audit | `data/audit/<doc_id>/` |

