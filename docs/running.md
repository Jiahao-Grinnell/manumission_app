# Running the Project on Windows

This guide is written for this machine setup:

- Windows host
- Docker Desktop installed and running
- Ubuntu / WSL installed
- NVIDIA GPU available to WSL and Docker
- Repository path: `C:\Users\dengjiahao\Desktop\manumission_app`

GPU is the default path. You do not need to add `compose.gpu.yaml` or set `OLLAMA_USE_GPU=1` anymore. A normal `docker compose up -d ollama` starts Ollama with GPU access.

## 1. What Works Right Now

The project is being built phase by phase. Do not expect every URL to work yet.

Current completed runtime target:

```text
M4 / Phase 4.3: PDF ingest + normalizer + aggregator + OCR + page_classifier + name_extractor + metadata_extractor
```

Available now:

- Ollama runs in Docker.
- Ollama uses the NVIDIA GPU by default.
- Ollama is reachable only from Docker's internal network.
- The host cannot reach runtime Ollama at `127.0.0.1:11434`.
- Required models are stored under `volumes/ollama/`.
- `pdf_ingest` can split PDFs into `data/pages/<doc_id>/`.
- `pdf_ingest` has a CLI and a standalone local UI at `http://127.0.0.1:5102/ingest/`.
- Browser upload and `data/input_pdfs/` registration both work for `pdf_ingest`.
- `normalizer` can normalize names, places, dates, evidence, compare names, and dedupe place rows.
- `normalizer` has a standalone local UI at `http://127.0.0.1:5108/normalizer/`.
- `aggregator` can read fake or real intermediate JSON and write final CSVs.
- `aggregator` has a standalone local UI at `http://127.0.0.1:5109/aggregate/`.
- `ocr` can preview the preprocessing pipeline for rendered page PNGs.
- `ocr` has a CLI and a standalone local UI at `http://127.0.0.1:5103/ocr/`.
- `ocr` writes durable text artifacts under `data/ocr_text/<doc_id>/` when the OCR model is available.
- `page_classifier` can classify OCR text pages into extract/skip decisions and report types.
- `page_classifier` has a CLI and a standalone local UI at `http://127.0.0.1:5104/classify/`.
- `page_classifier` writes durable JSON artifacts under `data/intermediate/<doc_id>/pNNN.classify.json`.
- `name_extractor` can run the five-stage subject-name pipeline for pages where `should_extract=true`.
- `name_extractor` has a CLI and a standalone local UI at `http://127.0.0.1:5105/names/`.
- `name_extractor` writes durable JSON artifacts under `data/intermediate/<doc_id>/pNNN.names.json`.
- `name_extractor` only lists documents that already have OCR text in `data/ocr_text/<doc_id>/` and at least one classifier result with `should_extract=true`.
- `metadata_extractor` can extract one validated `Detailed info.csv` row per named person on an extractable page.
- `metadata_extractor` has a CLI and a standalone local UI at `http://127.0.0.1:5106/meta/`.
- `metadata_extractor` writes durable JSON artifacts under `data/intermediate/<doc_id>/pNNN.meta.json`.
- `metadata_extractor` only lists documents and pages that already have OCR text, `should_extract=true`, and non-empty `pNNN.names.json`.

Not available yet:

- `http://127.0.0.1:5000/`
- Main Web App
- Dashboard
- Remaining place module and orchestration pages

`http://127.0.0.1:5000/` becomes available after M6 / Phase 6, when the `web_app` service is implemented and added to Compose. During the current Phase 4.3 target, port 5000 is expected to fail.

## 2. Open the Project

Recommended: use WSL for shell commands.

From PowerShell:

```powershell
wsl
```

Then in WSL:

```bash
cd /mnt/c/Users/dengjiahao/Desktop/manumission_app
```

Confirm the location:

```bash
pwd
ls
```

You should see:

```text
README.md
compose.yaml
docs
scripts
src
```

Do not run this inside WSL:

```bash
cd C:\Users\dengjiahao\Desktop\manumission_app
```

That is a Windows path. Inside WSL, use:

```bash
cd /mnt/c/Users/dengjiahao/Desktop/manumission_app
```

PowerShell-only alternative:

```powershell
cd C:\Users\dengjiahao\Desktop\manumission_app
```

## 3. Check Docker and GPU

Start Docker Desktop first.

In WSL, confirm Docker is reachable:

```bash
docker version
docker compose version
```

Confirm WSL can see the GPU:

```bash
nvidia-smi
```

Optional Docker Hub GPU image check:

```bash
docker run --rm --gpus all nvidia/cuda:12.3.2-base-ubuntu22.04 nvidia-smi
```

Expected result: the command prints your NVIDIA GPU. On this machine it should show the RTX 5000 Ada GPU.

If this fails with `error getting credentials`, that is a Docker Hub credential-helper problem, not necessarily a GPU problem. Continue with the project-specific GPU check in section 8, or fix the Docker credential config using the troubleshooting section below.

## 4. Prepare Local Folders

These folders are ignored by git and are safe for local data:

```bash
mkdir -p data/input_pdfs
mkdir -p data/pages
mkdir -p data/ocr_text
mkdir -p data/intermediate
mkdir -p data/output
mkdir -p data/logs
mkdir -p data/audit
mkdir -p volumes/ollama
```

Do not commit PDFs. The repo ignores:

```text
*.pdf
data/
volumes/
```

Recommended input layout:

```text
data/input_pdfs/
  my_document.pdf
data/pages/
data/ocr_text/
data/intermediate/
data/output/
data/logs/
data/audit/
```

Large PDFs should be placed directly in `data/input_pdfs/`. Do not rely on browser upload for files larger than hundreds of MB.

## 5. Configure Environment

Create `.env` once if it does not exist:

```bash
cp .env.example .env
```

Important model settings:

```text
OLLAMA_MODEL=qwen2.5:14b-instruct
OCR_MODEL=glm-ocr:latest
```

Roles:

- `OLLAMA_MODEL`: text extraction model for classifier and NER modules.
- `OCR_MODEL`: vision model for OCR.

Changing `.env` changes which model the app will use after containers restart. It does not download the model. Downloading is done by `scripts/seed_model.sh`.

## 6. Download Models

This step needs internet access. It downloads models into `volumes/ollama/`.

Download the text extraction model:

```bash
./scripts/seed_model.sh qwen2.5:14b-instruct
```

Download the OCR vision model:

```bash
./scripts/seed_model.sh glm-ocr:latest
```

If WSL says the script is not executable:

```bash
bash scripts/seed_model.sh qwen2.5:14b-instruct
bash scripts/seed_model.sh glm-ocr:latest
```

From PowerShell:

```powershell
wsl bash ./scripts/seed_model.sh qwen2.5:14b-instruct
wsl bash ./scripts/seed_model.sh glm-ocr:latest
```

Notes:

- Seed mode temporarily exposes Ollama at `127.0.0.1:11434` only.
- Seed mode uses GPU by default.
- Runtime mode later mounts the model folder read-only.
- If a model is already downloaded, Ollama will reuse it.

## 7. Start Ollama Runtime

Start the gateway:

```bash
docker compose up -d ollama
```

Check status:

```bash
docker compose ps ollama
```

Expected:

```text
manumission_app-ollama-1   Up ... (healthy)
```

Follow logs:

```bash
docker compose logs -f ollama
```

The logs should include CUDA detection, similar to:

```text
library=CUDA
NVIDIA RTX 5000 Ada Generation Laptop GPU
```

## 8. Verify the Gateway

Run the full gateway check:

```bash
bash scripts/verify_gateway.sh
```

The script checks:

- runtime Compose syntax
- seed Compose syntax
- Ollama container status
- Docker GPU device request
- tiny generation through the same HTTP API the app will use
- GPU placement through `ollama ps`
- host isolation
- internal Docker network reachability
- required model presence

Expected success output includes:

```text
PASS: Docker GPU device request is present
PASS: tiny HTTP generation returned a response
PASS: Ollama model is running on GPU
PASS: host cannot reach runtime Ollama
{"version":"0.16.2"}
PASS: all required models are present
Gateway verification complete.
```

The first run can take several minutes because `qwen2.5:14b-instruct` has to load into GPU memory. If the model load or generation hangs, the script fails after `VERIFY_GENERATE_TIMEOUT`, which defaults to 900 seconds.

Host isolation check:

```bash
curl http://127.0.0.1:11434/api/tags
```

During runtime, this should fail. That is correct. Runtime Ollama is intentionally not exposed to the host.

Internal Docker check:

```bash
docker run --rm --network manumission_app_llm_internal curlimages/curl:latest \
  -s http://ollama:11434/api/version
```

Expected:

```json
{"version":"0.16.2"}
```

## 9. Verify a Real Model Call

Run a small text generation through the project client:

If the `manumission-base:phase1` image does not exist yet, build it first:

```bash
docker build -f docker/base.Dockerfile -t manumission-base:phase1 .
```

```bash
docker run --rm \
  --network manumission_app_llm_internal \
  -e PYTHONPATH=/app/src \
  -v "$(pwd)":/app \
  -w /app \
  manumission-base:phase1 \
  python -c "from shared.ollama_client import OllamaClient; from shared.schemas import CallStats; c=OllamaClient(model='qwen2.5:14b-instruct'); s=CallStats(); print(c.generate('Reply with exactly OK.', s, num_predict=10)); print(s)"
```

Expected:

```text
OK
model_calls=1 repair_calls=0
```

Then confirm the model is on GPU:

```bash
docker compose exec -T ollama ollama ps
```

Expected `PROCESSOR`:

```text
100% GPU
```

The first model call can take a few minutes because the model must load into VRAM. Later calls are faster while the model remains loaded.

## 10. Start the Full App Later

When M6 / Phase 6 is implemented, the full app start command will be:

```bash
docker compose up -d
```

Then open:

```text
http://127.0.0.1:5000/
```

For now, this URL does not work because there is no `web_app` service in `compose.yaml`.

## 11. Phase 2.1 PDF Ingest Testing

`pdf_ingest` has both CLI tests and a visual test UI.

Build and run its standalone UI:

```bash
docker compose --profile ingest up -d --build pdf_ingest
```

Open:

```text
http://127.0.0.1:5102/ingest/
```

Health check:

```bash
curl http://127.0.0.1:5102/healthz
```

Expected:

```json
{"module":"pdf_ingest","status":"ok"}
```

Run the module unit tests:

```bash
docker build -f docker/ingest.Dockerfile -t manumission-ingest:phase2 .
docker run --rm manumission-ingest:phase2 python -m unittest discover -s /app/modules/pdf_ingest/tests -p "test_*.py"
```

Expected CLI shape:

```bash
docker compose --profile ingest run --rm pdf_ingest python -m modules.pdf_ingest.cli \
  --pdf /data/input_pdfs/sample_input_1.pdf \
  --doc-id sample_input_1 \
  --dpi 200 \
  --out /data/pages
```

Fast existing-file smoke test using a local sample:

```bash
mkdir -p data/input_pdfs
cp "sample input 2.pdf" data/input_pdfs/sample_input_2.pdf

curl -X POST http://127.0.0.1:5102/ingest/run \
  -H "Content-Type: application/json" \
  -d '{"doc_id":"sample_input_2_smoke","source_pdf":"sample_input_2.pdf","dpi":100,"end_page":3}'
```

Expected output folder:

```text
data/pages/sample_input_2_smoke/
  manifest.json
  p001.png
  p002.png
  p003.png
```

Expected visual UI:

```text
http://127.0.0.1:5102/ingest/
```

The UI should show:

- upload/register form
- manifest summary
- page count
- DPI
- completed pages
- thumbnail grid
- click-to-open original page image
- resume status for partial large-PDF ingest

Upload smoke test from PowerShell, using any small local PDF:

```powershell
curl.exe -L -F "pdf=@sample input 2.pdf" -F "doc_id=upload_fixture" -F "dpi=72" http://127.0.0.1:5102/ingest/upload
```

The upload route saves the PDF as `data/input_pdfs/<doc_id>.pdf`, renders page PNGs, and redirects back to the thumbnail grid.

## 12. Phase 2.2 Normalizer Testing

Build and run the standalone normalizer UI:

```bash
docker compose --profile normalizer up -d --build normalizer
```

Open:

```text
http://127.0.0.1:5108/normalizer/
```

Health check:

```bash
curl http://127.0.0.1:5108/healthz
```

Expected:

```json
{"module":"normalizer","status":"ok"}
```

Run the module unit tests:

```bash
docker build -f docker/normalizer.Dockerfile -t manumission-normalizer:phase2 .
docker run --rm manumission-normalizer:phase2 python -m unittest discover -s /app/modules/normalizer/tests -p "test_*.py"
```

Smoke-test key rules:

```bash
curl -X POST http://127.0.0.1:5108/normalizer/normalize/place \
  -H "Content-Type: application/json" \
  -d '{"raw":"shargah"}'

curl -X POST http://127.0.0.1:5108/normalizer/normalize/date \
  -H "Content-Type: application/json" \
  -d '{"raw":"17th May 1931","doc_year":"1931"}'

curl -X POST http://127.0.0.1:5108/normalizer/compare-names \
  -H "Content-Type: application/json" \
  -d '{"a":"Mariam bint Yusuf","b":"Marium bint Yousuf"}'
```

Expected highlights:

```text
"normalized":"Sharjah"
"iso":"1931-05-17"
"same":true
```

## 13. Phase 2.3 Aggregator Testing

Build and run the standalone aggregator UI:

```bash
docker compose --profile aggregator up -d --build aggregator
```

Open:

```text
http://127.0.0.1:5109/aggregate/
```

Health check:

```bash
curl http://127.0.0.1:5109/healthz
```

Expected:

```json
{"module":"aggregator","status":"ok"}
```

Run the module unit tests:

```bash
docker build -f docker/aggregator.Dockerfile -t manumission-aggregator:phase2 .
docker run --rm manumission-aggregator:phase2 python -m unittest discover -s /app/modules/aggregator/tests -p "test_*.py"
docker compose --profile aggregator run --rm aggregator python -m unittest discover -s /app/modules/aggregator/tests -p "test_*.py"
```

Run the fake-data smoke test:

```bash
docker compose --profile aggregator run --rm aggregator python -m modules.aggregator.cli --doc-id agg_smoke
```

Expected output files:

```text
data/output/agg_smoke/
  Detailed info.csv
  name place.csv
  run_status.csv
  aggregation_summary.json
```

Smoke-test preview and zip download:

```bash
curl http://127.0.0.1:5109/aggregate/result/agg_smoke
curl -I http://127.0.0.1:5109/aggregate/download/agg_smoke.zip
```

Expected highlights:

```text
"detail_rows":1
"place_rows":1
"Merged name variants"
Content-Type: application/zip
```

## 14. Phase 3 OCR Testing

Build and run the standalone OCR UI:

```bash
docker compose --profile ocr up -d --build ocr
```

Open:

```text
http://127.0.0.1:5103/ocr/
```

Health check:

```bash
curl http://127.0.0.1:5103/healthz
```

Expected:

```json
{"module":"ocr","status":"ok"}
```

Run the module unit tests:

```bash
docker build -f docker/ocr.Dockerfile -t manumission-ocr:phase3 .
docker run --rm manumission-ocr:phase3 python -m unittest discover -s /app/modules/ocr/tests -p "test_*.py"
```

Expected:

```text
Ran 5 tests
OK
```

Smoke-test preprocessing preview against an ingested document:

```bash
curl -X POST http://127.0.0.1:5103/ocr/preview/upload_fixture/1 \
  -H "Content-Type: application/json" \
  -d '{}'
```

Expected highlights:

```text
"label":"original"
"label":"enhanced"
"label":"deskewed"
"label":"cropped"
"label":"tile 0"
```

Run full OCR for a rendered document after `glm-ocr:latest` has been downloaded:

```bash
docker compose --profile ocr run --rm ocr python -m modules.ocr.cli \
  --in_dir /data/pages/upload_fixture \
  --out_dir /data/ocr_text/upload_fixture \
  --model glm-ocr:latest \
  --ollama_url http://ollama:11434/api/generate \
  --no_debug \
  --max_new_tokens 1200
```

Expected output files:

```text
data/ocr_text/upload_fixture/
  manifest.json
  p001.txt
  p002.txt
  run_status.log
```

Implemented smoke result on 2026-04-20:

```text
Done. Status: complete. Completed 2/2 pages.
p001.txt: Upload One
p002.txt: Upload Two
```

The full OCR call can take a while because it loads the vision model and sends one or more images per page to Ollama. The fast Phase 3 dev loop is the preprocessing preview plus mocked unit tests; the full live-model smoke test only needs to be repeated after OCR model, prompt, preprocessing, or runtime changes.

## 15. Phase 4.1 Page Classifier Testing

Build and run the standalone classifier UI:

```bash
docker compose --profile classifier up -d --build page_classifier
```

Open:

```text
http://127.0.0.1:5104/classify/
```

Health check:

```bash
curl http://127.0.0.1:5104/healthz
```

Expected:

```json
{"module":"page_classifier","status":"ok"}
```

Run the module unit tests:

```bash
docker build -f docker/ner.Dockerfile -t manumission-ner:phase4_1 .
docker run --rm manumission-ner:phase4_1 python -m unittest discover -s /app/modules/page_classifier/tests -p "test_*.py"
```

Run a single-page classification against existing OCR text:

```bash
curl -X POST http://127.0.0.1:5104/classify/run-single/sample%20input%201/6 \
  -H "Content-Type: application/json" \
  -d '{}'
```

Expected highlights:

```text
"should_extract":
"report_type":
"evidence":
"override":
```

Run a whole-document classification job:

```bash
curl -X POST http://127.0.0.1:5104/classify/run-all/sample%20input%201 \
  -H "Content-Type: application/json" \
  -d '{}'
```

The response returns a `job_id`. Poll it at:

```text
http://127.0.0.1:5104/classify/jobs/<job_id>
```

Expected output artifacts:

```text
data/intermediate/sample input 1/
  p001.classify.json
  p002.classify.json
  ...
```

## 16. Phase 4.2 Name Extractor Testing

Build and run the standalone name extractor UI:

```bash
docker compose --profile names up -d --build name_extractor
```

Open:

```text
http://127.0.0.1:5105/names/
```

Important UI behavior:

- the document selector is a dropdown, not a freeform input
- docs are discovered from `data/ocr_text/`
- a doc appears only after `page_classifier` has produced at least one `pNNN.classify.json` with `should_extract=true`
- if a doc is missing, run `http://127.0.0.1:5104/classify/` on the whole document first

Health check:

```bash
curl http://127.0.0.1:5105/healthz
```

Expected:

```json
{"module":"name_extractor","status":"ok"}
```

Run the module unit tests:

```bash
docker build -f docker/ner.Dockerfile -t manumission-ner:phase4_2 .
docker run --rm manumission-ner:phase4_2 python -m unittest discover -s /app/modules/name_extractor/tests -p "test_*.py"
```

Run one extractable page after classifier output exists:

```bash
curl -X POST http://127.0.0.1:5105/names/run-single/sample%20input%201/6 \
  -H "Content-Type: application/json" \
  -d '{}'
```

If `sample input 1` does not appear in the dropdown, inspect:

```text
data/ocr_text/sample input 1/
data/intermediate/sample input 1/pNNN.classify.json
```

The name extractor will hide that document until at least one classifier file says:

```json
{"should_extract": true}
```

Expected highlights:

```text
"named_people":
"passes":
"removed_candidates":
"final_reasons":
```

Rerun only `verify` for the same page:

```bash
curl -X POST http://127.0.0.1:5105/names/rerun-pass/sample%20input%201/6/verify \
  -H "Content-Type: application/json" \
  -d '{}'
```

Run a whole-document extraction job:

```bash
curl -X POST http://127.0.0.1:5105/names/run-all/sample%20input%201 \
  -H "Content-Type: application/json" \
  -d '{}'
```

Poll the returned `job_id` at:

```text
http://127.0.0.1:5105/names/jobs/<job_id>
```

Expected output artifacts:

```text
data/intermediate/sample input 1/
  p001.classify.json
  p001.names.json
  p002.classify.json
  p002.names.json
  ...
```

The UI should show:

- extractable pages only
- final names highlighted separately from dropped names
- stage cards for `pass1`, `pass1_filter`, `recall`, `recall_filter`, `merged`, `verify`, and `rule_filter`
- prompt text and parsed JSON for each model stage
- dropped-candidate reasons tagged by stage

## 17. Phase 4.3 Metadata Extractor Testing

Build and run the standalone metadata extractor UI:

```bash
docker compose --profile meta up -d --build metadata_extractor
```

Open:

```text
http://127.0.0.1:5106/meta/
```

Important UI behavior:

- the document selector is a dropdown, not a freeform input
- docs are discovered from `data/ocr_text/`
- a page appears only when `page_classifier` has produced `pNNN.classify.json` with `should_extract=true`
- the same page must also already have `pNNN.names.json` with at least one named person
- if a doc is missing, run classifier first, then run name extraction for at least one page on that doc

Health check:

```bash
curl http://127.0.0.1:5106/healthz
```

Expected:

```json
{"module":"metadata_extractor","status":"ok"}
```

Run the module unit tests:

```bash
docker build -f docker/ner.Dockerfile -t manumission-ner:phase4_3 .
docker run --rm manumission-ner:phase4_3 python -m unittest discover -s /app/modules/metadata_extractor/tests -p "test_*.py"
```

Run one named person after classifier and names output exist:

```bash
curl -X POST http://127.0.0.1:5106/meta/run-single/sample%20input%201/6/Mariam%20bint%20Yusuf \
  -H "Content-Type: application/json" \
  -d '{}'
```

Expected highlights:

```text
"rows":
"validation":
"raw_values":
"rendered_prompt":
"response_json":
```

Run all named people on one page:

```bash
curl -X POST http://127.0.0.1:5106/meta/run-page/sample%20input%201/6 \
  -H "Content-Type: application/json" \
  -d '{}'
```

Run a whole-document extraction job:

```bash
curl -X POST http://127.0.0.1:5106/meta/run-all/sample%20input%201 \
  -H "Content-Type: application/json" \
  -d '{}'
```

Poll the returned `job_id` at:

```text
http://127.0.0.1:5106/meta/jobs/<job_id>
```

Expected output artifacts:

```text
data/intermediate/sample input 1/
  p001.classify.json
  p001.names.json
  p001.meta.json
  p002.classify.json
  p002.names.json
  p002.meta.json
  ...
```

The UI should show:

- one field card per final metadata field for the selected person
- OCR text with evidence highlighted in different colors
- per-field validation statuses such as `ok`, `empty`, `cleared_invalid`, `cleared_missing_evidence`, and `inherited`
- the rendered prompt and parsed response JSON for debugging

## 18. Future Main Web UI Routes

After M6 / Phase 6, the main Web App should expose these local routes:

| Module | URL | Purpose |
|---|---|---|
| Home | `http://127.0.0.1:5000/` | Dashboard |
| Inputs | `http://127.0.0.1:5000/inputs` | Register large local PDFs |
| Upload | `http://127.0.0.1:5000/upload` | Browser PDF upload |
| Jobs | `http://127.0.0.1:5000/jobs` | Job history |
| PDF ingest | `http://127.0.0.1:5000/ingest/` | Page thumbnails and manifest |
| OCR | `http://127.0.0.1:5000/ocr/` | OCR preprocessing and text output |
| Classifier | `http://127.0.0.1:5000/classify/` | Extract/skip decision |
| Names | `http://127.0.0.1:5000/names/` | Name extraction review |
| Metadata | `http://127.0.0.1:5000/meta/` | Per-person metadata |
| Places | `http://127.0.0.1:5000/places/` | Place route extraction |
| Normalizer | `http://127.0.0.1:5000/normalizer/` | Rule playground |
| Aggregator | `http://127.0.0.1:5000/aggregate/` | CSV preview and download |

These routes are future targets, not current Phase 4.3 behavior.

## 19. Running Tests

Shared-library tests:

```bash
docker build -f docker/base.Dockerfile -t manumission-base:phase1 .
docker run --rm \
  -e PYTHONPATH=/app/src \
  -v "$(pwd)":/app \
  -w /app \
  manumission-base:phase1 \
  python -m unittest discover -s src/shared/tests -p "test_*.py"
```

Expected:

```text
Ran 21 tests
OK
```

Future module tests should follow this pattern:

```bash
docker compose run --rm <module_service> python -m unittest discover -s src/modules/<module>/tests -p "test_*.py"
```

## 20. Changing Models Later

To switch the text extraction model:

1. Download the new model:

   ```bash
   ./scripts/seed_model.sh mistral-small3.1:latest
   ```

2. Edit `.env`:

   ```text
   OLLAMA_MODEL=mistral-small3.1:latest
   ```

3. Restart relevant containers:

   ```bash
   docker compose down
   docker compose up -d ollama
   ```

4. Verify:

   ```bash
   bash scripts/verify_gateway.sh
   ```

To switch the OCR model:

1. Download the model:

   ```bash
   ./scripts/seed_model.sh glm-ocr:latest
   ```

2. Edit `.env`:

   ```text
   OCR_MODEL=glm-ocr:latest
   ```

3. Restart relevant containers.

Keep model tags exact. `qwen2.5:14b-instruct` and `qwen2.5:latest` are different models.

## 21. Useful Commands

Start GPU Ollama:

```bash
docker compose up -d ollama
```

Stop everything:

```bash
docker compose down
```

Show services:

```bash
docker compose ps
```

Show Ollama logs:

```bash
docker compose logs -f ollama
```

Show downloaded models:

```bash
docker compose exec -T ollama ollama list
```

Show loaded models and GPU/CPU placement:

```bash
docker compose exec -T ollama ollama ps
```

Validate Compose:

```bash
docker compose -f compose.yaml config
docker compose -f compose.seed.yaml --profile seed config
```

Run gateway verification:

```bash
bash scripts/verify_gateway.sh
```

## 22. Troubleshooting

### `docker compose up -d ollama` fails with a GPU error

Check Docker GPU support:

```bash
docker run --rm --gpus all nvidia/cuda:12.3.2-base-ubuntu22.04 nvidia-smi
```

If this fails, restart Docker Desktop and WSL. Then retry.

### `docker build` or `docker run` fails with `error getting credentials`

This means Docker in WSL could not use its configured Docker Hub credential helper. It usually happens before the image is downloaded, so it does not prove that GPU, Dockerfile syntax, or the project image is broken.

Common examples:

```text
failed to resolve source metadata for docker.io/library/python:3.11-slim
error getting credentials - err: exit status 1
```

```text
docker run --rm --gpus all nvidia/cuda:... nvidia-smi
error getting credentials
```

Check your WSL Docker config:

```bash
cat ~/.docker/config.json
```

If it contains this:

```json
{
  "credsStore": "desktop.exe"
}
```

then WSL is trying to call Docker Desktop's credential helper and that helper is failing. For public images, you can remove that setting:

```bash
cp ~/.docker/config.json ~/.docker/config.json.bak
python3 - <<'PY'
import json
from pathlib import Path

path = Path.home() / ".docker" / "config.json"
data = json.loads(path.read_text(encoding="utf-8"))
data.pop("credsStore", None)
data.pop("credHelpers", None)
path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
PY
```

Then retry:

```bash
docker pull python:3.11-slim
docker build -f docker/ingest.Dockerfile -t manumission-ingest:phase2 .
docker run --rm --gpus all nvidia/cuda:12.3.2-base-ubuntu22.04 nvidia-smi
```

This changes only your WSL Docker client config. If you rely on Docker Desktop login for private registries, sign in again later from Docker Desktop.

### `curl http://127.0.0.1:11434/api/tags` fails

This is expected during runtime. Runtime Ollama has no host port.

Use the internal Docker check instead:

```bash
docker run --rm --network manumission_app_llm_internal curlimages/curl:latest \
  -s http://ollama:11434/api/version
```

### `http://127.0.0.1:5000/` fails

This is expected until M6 / Phase 6. The `web_app` service does not exist yet.

### Container name `/ollama` is already in use

An old global container exists outside this Compose project.

Inspect:

```bash
docker ps -a --filter "name=ollama"
```

Remove it only if it is not used by another project:

```bash
docker rm -f ollama
```

Then start this project again:

```bash
docker compose up -d ollama
```

### First model call is very slow

Normal. The model is loading into GPU memory. On the tested RTX 5000 Ada setup, the first `qwen2.5:14b-instruct` call can take a couple of minutes.

If `bash scripts/verify_gateway.sh` appears stuck at model generation, check Task Manager or run this in another terminal:

```bash
docker compose exec -T ollama ollama ps
docker compose logs --tail=120 ollama
```

During a healthy first load, the logs should eventually show `offloaded ... layers to GPU`, and `ollama ps` should show `100% GPU`.

### GPU memory pressure

The runtime is intentionally conservative:

```text
OLLAMA_MAX_LOADED_MODELS=1
OLLAMA_NUM_PARALLEL=1
OLLAMA_CONTEXT_LENGTH=4096
OLLAMA_KEEP_ALIVE=2m
OLLAMA_LOAD_TIMEOUT=10m
```

This avoids keeping the text model and OCR model in VRAM at the same time on a 16 GB GPU.

## 23. Artifact Locations

| Artifact | Path |
|---|---|
| Input PDFs | `data/input_pdfs/` |
| Rendered page PNGs | `data/pages/<doc_id>/` |
| OCR text | `data/ocr_text/<doc_id>/pNNN.txt` |
| Intermediate JSON | `data/intermediate/<doc_id>/` |
| Final CSVs | `data/output/<doc_id>/` |
| Logs and job state | `data/logs/<doc_id>/` |
| Prompt/response audit | `data/audit/<doc_id>/` |
