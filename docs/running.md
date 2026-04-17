# Running the Project on Windows

This guide assumes:

- You are on Windows.
- Docker Desktop is installed and running.
- Ubuntu for Windows / WSL is installed.
- This repository is checked out on your Windows filesystem.

The application is built module by module. Early phases will not have every screen available yet. When a module is completed, its visual test UI should become reachable through the main Web UI or through its standalone Compose profile.

## 1. Open the Project Directory

If you are in PowerShell, use the normal Windows path:

```powershell
cd C:\Users\dengjiahao\Desktop\manumission_app
```

If you enter WSL / Ubuntu first, use the Linux-mounted Windows path:

```bash
cd /mnt/c/Users/dengjiahao/Desktop/manumission_app
```

Do not use `C:\Users\...` inside WSL bash. Backslashes are treated as escape characters there, and the path will not resolve.

You can also enter WSL directly in the project directory from PowerShell:

```powershell
wsl --cd C:\Users\dengjiahao\Desktop\manumission_app
```

Or convert the Windows path inside WSL:

```bash
cd "$(wslpath 'C:\Users\dengjiahao\Desktop\manumission_app')"
```

Confirm you are in the right place:

```bash
pwd
ls
```

You should see files such as `README.md`, `docs`, `src`, `compose.yaml`, and `scripts`.

## 2. Local Files and Large PDFs

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

## 3. First-Time Model Setup

Only this step needs internet access.

Seed the text extraction model:

```bash
./scripts/seed_model.sh qwen2.5:14b-instruct
```

On Windows PowerShell, run the same through WSL if shell permissions are awkward:

```powershell
wsl bash ./scripts/seed_model.sh qwen2.5:14b-instruct
```

Seed the OCR vision model too:

```bash
./scripts/seed_model.sh glm-ocr:latest
```

From PowerShell:

```powershell
wsl bash ./scripts/seed_model.sh glm-ocr:latest
```

The model is stored under `volumes/ollama/`.

## 4. Changing Models Later

There are two model roles to think about:

- **Text extraction model**: used by page classification, name extraction, metadata extraction, and place extraction. This is controlled by `OLLAMA_MODEL`.
- **OCR vision model**: used by the OCR module. This is controlled by `OCR_MODEL` by default, and can also be overridden by the OCR module's `--model` argument once module 03 is implemented.

To switch the text extraction model:

1. Seed the new model once:

   ```bash
   ./scripts/seed_model.sh mistral-small3.1:latest
   ```

   From PowerShell through WSL:

   ```powershell
   wsl bash ./scripts/seed_model.sh mistral-small3.1:latest
   ```

2. Set the model in `.env`:

   ```text
   OLLAMA_MODEL=mistral-small3.1:latest
   ```

   If `.env` does not exist yet, copy `.env.example` first:

   ```powershell
   copy .env.example .env
   ```

3. Restart the runtime stack:

   ```bash
   docker compose down
   docker compose up -d
   ```

4. Confirm the model exists inside runtime Ollama:

   ```powershell
   docker run --rm --network manumission_app_llm_internal curlimages/curl:latest -s http://ollama:11434/api/tags
   ```

5. Test a small generation call:

   ```powershell
   docker run --rm --network manumission_app_llm_internal manumission-base:phase1 `
     python -c "from shared.ollama_client import OllamaClient; from shared.schemas import CallStats; c=OllamaClient(); s=CallStats(); print(c.generate('Reply with exactly OK.', s, num_predict=10)); print(s)"
   ```

To switch the OCR vision model, seed the vision model and pass that model to the OCR module when OCR is implemented:

```bash
./scripts/seed_model.sh glm-ocr:latest
```

Then set it in `.env`:

```text
OCR_MODEL=glm-ocr:latest
```

Example future OCR CLI shape:

```bash
python -m modules.ocr.cli \
  --in_dir /data/pages/myDoc \
  --out_dir /data/ocr_text/myDoc \
  --model glm-ocr:latest
```

Important notes:

- Seeding downloads the model into `volumes/ollama/`; it does not by itself change which model the app uses.
- Changing `.env` changes the default model used by app containers after restart.
- If you already have intermediate JSON from an older model, rerun the affected stages if you want outputs from the new model.
- Keep model tags exact. `qwen2.5:14b-instruct` and `qwen2.5:latest` are different tags.

## 5. Start the Runtime Stack

After the model is seeded:

```bash
docker compose up -d
```

The main app, once module 11 is implemented, will be available at:

```text
http://127.0.0.1:5000
```

It is intentionally bound to localhost only.

## 6. Visual Test Pages by Module

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

## 7. Standalone Module UIs

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

## 8. Running Tests

Phase 1.1 shared-library tests do not require Ollama:

```bash
docker build -f docker/base.Dockerfile -t manumission-base:phase1 .
docker run --rm manumission-base:phase1 python -m unittest discover -s /app/shared/tests -p "test_*.py"
```

If you are running from PowerShell and Docker access is blocked by permissions, run the same commands from an elevated terminal or through WSL.

## 9. Typical Development Loop

1. Implement one module.
2. Run that module's unit tests.
3. Start Docker services for that module.
4. Open the module's visual test UI.
5. Test on a tiny fixture first.
6. Test on `sample input 1.pdf` or `sample input 2.pdf`.
7. For milestone checks, test with `full input.pdf` or another large PDF and verify resume behavior.

## 10. Useful Commands

```bash
docker compose ps
docker compose logs -f ollama
docker compose down
docker compose -f compose.yaml config
docker compose -f compose.seed.yaml --profile seed config
```

If `docker compose up -d` says the container name `/ollama` is already in use, an older container exists outside this Compose project. Current Compose files avoid fixed global container names, so first pull the latest local files and try again. If the error still appears, inspect and remove the old container:

```bash
docker ps -a --filter "name=ollama"
docker rm -f ollama
docker compose up -d
```

Only remove it if you are not intentionally using that old container for another project.

## 11. Where Results Are Stored

| Artifact | Path |
|---|---|
| Input PDFs | `data/input_pdfs/` |
| Rendered page PNGs | `data/pages/<doc_id>/` |
| OCR text | `data/ocr_text/<doc_id>/pNNN.txt` |
| Intermediate JSON | `data/intermediate/<doc_id>/` |
| Final CSVs | `data/output/<doc_id>/` |
| Logs and job state | `data/logs/<doc_id>/` |
| Optional prompt/response audit | `data/audit/<doc_id>/` |
