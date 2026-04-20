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
M2 / Phase 2.3: PDF ingest + normalizer + aggregator
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

Not available yet:

- `http://127.0.0.1:5000/`
- Main Web App
- Dashboard
- OCR, classifier, NER, and orchestration pages

`http://127.0.0.1:5000/` becomes available after M6 / Phase 6, when the `web_app` service is implemented and added to Compose. During the current Phase 2.3 target, port 5000 is expected to fail.

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

## 14. Future Main Web UI Routes

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

These routes are future targets, not current Phase 2.3 behavior.

## 15. Running Tests

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

## 16. Changing Models Later

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

## 17. Useful Commands

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

## 18. Troubleshooting

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

## 19. Artifact Locations

| Artifact | Path |
|---|---|
| Input PDFs | `data/input_pdfs/` |
| Rendered page PNGs | `data/pages/<doc_id>/` |
| OCR text | `data/ocr_text/<doc_id>/pNNN.txt` |
| Intermediate JSON | `data/intermediate/<doc_id>/` |
| Final CSVs | `data/output/<doc_id>/` |
| Logs and job state | `data/logs/<doc_id>/` |
| Prompt/response audit | `data/audit/<doc_id>/` |
