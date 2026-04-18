# Module 01 - ollama_gateway

> The Ollama container and its runtime network contract. This is an infrastructure module and contains no Python business code.

## 1. Purpose

This module isolates the local LLM server behind Docker Compose. Other modules call Ollama through the internal Docker hostname `ollama`, while the Windows host cannot reach the runtime Ollama port directly.

The module is made of Compose files, model seeding scripts, model allowlists, and verification commands.

## 2. Deployment Modes

### 2.1 Runtime Mode

`compose.yaml` runs the normal offline service:

```yaml
services:
  ollama:
    image: ollama/ollama:latest
    gpus: all
    environment:
      OLLAMA_HOST: 0.0.0.0:11434
      OLLAMA_NO_CLOUD: "1"
      OLLAMA_CONTEXT_LENGTH: "4096"
      OLLAMA_KEEP_ALIVE: 2m
      OLLAMA_LOAD_TIMEOUT: 10m
      OLLAMA_MAX_LOADED_MODELS: "1"
      OLLAMA_NUM_PARALLEL: "1"
      HOME: /home/ollama
    user: "10001:10001"
    volumes:
      - ./volumes/ollama:/home/ollama/.ollama:ro
    networks:
      - llm_internal
    security_opt:
      - no-new-privileges:true
    cap_drop:
      - ALL
    restart: unless-stopped
    healthcheck:
      test: ["CMD-SHELL", "ollama list >/dev/null 2>&1 || exit 1"]
      interval: 10s
      timeout: 5s
      retries: 30
      start_period: 60s

networks:
  llm_internal:
    internal: true
  llm_frontend:
    driver: bridge
```

Key points:

- Runtime mode has no `ports:` field, so `127.0.0.1:11434` should fail from the host.
- `llm_internal` is marked `internal: true`, so runtime LLM traffic stays inside Docker.
- The model volume is mounted read-only in runtime mode.
- The official Ollama image does not include `curl` or `wget`, so the Docker healthcheck uses `ollama list`. The verification script still checks `GET /api/version` from a separate container on the internal network.
- Runtime limits keep a 16 GB GPU stable by allowing one loaded model and one request at a time. This matters because the text model and OCR model should not compete for VRAM.

### 2.2 Seed Mode

`compose.seed.yaml` is used only while downloading models:

```yaml
services:
  ollama_seed:
    image: ollama/ollama:latest
    gpus: all
    environment:
      OLLAMA_HOST: 0.0.0.0:11434
      OLLAMA_NO_CLOUD: "1"
      HOME: /home/ollama
    user: "10001:10001"
    volumes:
      - ./volumes/ollama:/home/ollama/.ollama
    ports:
      - "127.0.0.1:11434:11434"
    security_opt:
      - no-new-privileges:true
    cap_drop:
      - ALL
    restart: "no"
    profiles:
      - seed
```

Key points:

- Seed mode binds only to `127.0.0.1`, not the LAN.
- Seed mode mounts the model volume read-write.
- Seed mode uses GPU by default.
- Stop seed mode after downloads. Runtime mode will later mount the same model files read-only.

### 2.3 GPU Default

The default Compose path uses GPU:

```bash
docker compose up -d ollama
```

`compose.gpu.yaml` and `compose.seed.gpu.yaml` are legacy compatibility files from the earlier optional-GPU setup. They are no longer required for normal use.

Verify Docker can expose the GPU before running the gateway:

```bash
docker run --rm --gpus all nvidia/cuda:12.3.2-base-ubuntu22.04 nvidia-smi
```

The gateway verification script also checks Ollama logs for CUDA detection:

```bash
bash scripts/verify_gateway.sh
```

If Docker reports that no GPU device driver exists, fix Docker Desktop / WSL GPU integration before continuing.

## 3. Model Management

### 3.1 Required and Approved Models

Maintain `config/approved_model_tags.json` manually:

```json
{
  "required": [
    "qwen2.5:14b-instruct",
    "glm-ocr:latest"
  ],
  "approved": [
    "qwen2.5:14b-instruct",
    "mistral-small3.1:latest",
    "glm-ocr:latest"
  ]
}
```

`required` lists models that must be present for the current pipeline. `approved` lists models that are allowed for future switching or fallback.

### 3.2 Download Script

Download one model at a time:

```bash
./scripts/seed_model.sh qwen2.5:14b-instruct
./scripts/seed_model.sh glm-ocr:latest
```

The script stops runtime Ollama, starts seed mode, pulls the model, prints `ollama list`, shuts seed mode down, and leaves the model files under `volumes/ollama/`.

## 4. Client Contract

Runtime clients must use the Docker service hostname:

```python
OLLAMA_URL = "http://ollama:11434/api/generate"
```

Do not use `localhost` inside application containers. `localhost` would point to the caller container, not the Ollama container.

`OllamaClient.wait_ready()` should be called once at module startup before work begins.

## 5. Verification

Run:

```bash
bash scripts/verify_gateway.sh
```

The script checks:

- runtime Compose syntax
- seed Compose syntax
- runtime Ollama container status
- Docker GPU device request
- tiny generation through `/api/generate`
- GPU placement through `ollama ps`
- host isolation, where `127.0.0.1:11434` must fail
- internal reachability through `http://ollama:11434/api/version`
- required models from `config/approved_model_tags.json`

The current expected internal version response looks like:

```json
{"version":"0.16.2"}
```

## 6. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Host can access `127.0.0.1:11434` during runtime | Runtime service has a `ports:` mapping, or seed mode is still running | Stop seed mode and check `compose.yaml` |
| Internal curl cannot resolve `ollama` | Wrong network name or Compose project name | Run `docker compose ps` and use `COMPOSE_PROJECT_NAME=<name> bash scripts/verify_gateway.sh` if needed |
| Model is missing | It was not seeded into `volumes/ollama/` | Run `./scripts/seed_model.sh <model>` |
| GPU startup fails | Docker cannot expose a GPU to containers | Fix NVIDIA Docker Desktop / WSL GPU setup and rerun `docker run --rm --gpus all nvidia/cuda:12.3.2-base-ubuntu22.04 nvidia-smi` |
| LLM call hangs on first request | Model is loading | Check `docker compose logs -f ollama` and wait |

## 7. Build Checklist

- [x] Runtime `ollama` service exists and has no `ports:`.
- [x] Runtime network is internal.
- [x] Runtime model volume is read-only.
- [x] Runtime service runs as non-root and drops capabilities.
- [x] Seed service can temporarily expose `127.0.0.1:11434`.
- [x] Model seed script exists.
- [x] GPU is enabled by default.
- [x] Gateway verification script exists.
