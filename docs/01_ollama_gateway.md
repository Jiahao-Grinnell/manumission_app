# Module 01 - ollama_gateway

> The Ollama container itself plus its runtime network contract. This is an infrastructure module and contains no Python business code.

## 1. Purpose

Among all modules, **only this one** requires a GPU, uses a large amount of memory (a 14B model uses about 9 GB VRAM), and acts as a **shared dependency**. It is isolated as a gateway so every other module can call it over the internal network.

The "code" for this module is mainly **Compose definitions, Docker parameters, and runtime scripts**. It does not add Python code.

## 2. Two Deployment Modes

### 2.1 Seed Mode (temporary internet access for model download)

`compose.seed.yaml`:

```yaml
services:
  ollama_seed:
    image: ollama/ollama:latest
    container_name: ollama_seed
    environment:
      - OLLAMA_HOST=0.0.0.0:11434
      - OLLAMA_NO_CLOUD=1
      - HOME=/home/ollama
    user: "10001:10001"
    volumes:
      - ./volumes/ollama:/home/ollama/.ollama
    ports:
      - "127.0.0.1:11434:11434"    # Bind localhost only
    deploy:
      resources:
        reservations:
          devices: [{ capabilities: [gpu] }]
    security_opt: [ no-new-privileges:true ]
    cap_drop: [ ALL ]
```

Key points:

- `127.0.0.1:11434` binds localhost, not `0.0.0.0`, so LAN and public networks cannot reach it.
- This Compose file is used **only while downloading models**. Shut it down immediately afterward.
- Model files persist under `./volumes/ollama/`, and runtime mode mounts that volume.

### 2.2 Runtime Mode (offline operation)

The `ollama` service in `compose.yaml`:

```yaml
services:
  ollama:
    image: ollama/ollama:latest
    container_name: ollama
    environment:
      - OLLAMA_HOST=0.0.0.0:11434
      - OLLAMA_NO_CLOUD=1
      - HOME=/home/ollama
    user: "10001:10001"
    volumes:
      - ./volumes/ollama:/home/ollama/.ollama:ro   # Read-only; models cannot be changed
    networks:
      - llm_internal                                # Internal network only
    # Note: no ports: field
    restart: unless-stopped
    deploy:
      resources:
        reservations:
          devices: [{ capabilities: [gpu] }]
    security_opt: [ no-new-privileges:true ]
    cap_drop: [ ALL ]
    healthcheck:
      test: ["CMD-SHELL", "curl -fsS http://127.0.0.1:11434/api/version || exit 1"]
      interval: 10s
      timeout: 5s
      retries: 30
      start_period: 60s

networks:
  llm_internal:
    internal: true      # Critical: no internet egress
```

Key points:

- There is **no `ports:` field**, so the host cannot reach Ollama directly.
- `internal: true` prevents containers on this network from reaching the internet.
- The volume is mounted `:ro`, so runtime cannot modify model files.
- The healthcheck lets Compose know when Ollama is ready.

## 3. Model Management

### 3.1 Approved Model List

Maintain `config/approved_model_tags.json` manually as an allowlist of permitted models. After `OllamaClient.wait_ready()`, runtime code may optionally verify that the models in Ollama are in this allowlist.

Example:

```json
{
  "approved": [
    "qwen2.5:14b-instruct",
    "mistral-small3.1:latest",
    "glm-ocr:latest"
  ]
}
```

### 3.2 Download Script

`scripts/seed_model.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail
MODEL="${1:?usage: seed_model.sh <model-tag>}"

docker compose -f compose.seed.yaml up -d
docker exec -i ollama_seed ollama pull "$MODEL"
curl -s http://127.0.0.1:11434/api/tags > config/approved_model_tags.json
docker compose -f compose.seed.yaml down
echo "Done. Model $MODEL persisted under ./volumes/ollama/"
```

### 3.3 Common Models

| Model | Use | Size |
|---|---|---|
| `qwen2.5:14b-instruct` | Main NER text extraction | ~9 GB |
| `mistral-small3.1:latest` | Faster fallback NER | ~12 GB |
| `glm-ocr:latest` | OCR vision model | ~4 GB |
| `llama3.2:latest` | Small fallback | ~2 GB |

## 4. Client Side (`shared.ollama_client`)

In runtime mode, other containers access Ollama through:

```python
# shared/config.py
OLLAMA_URL = "http://ollama:11434/api/generate"    # Hostname is `ollama`, not localhost
```

`OllamaClient.wait_ready()` must be called once at module startup. LLM modules may start faster than Ollama can load the model, so startup should block until Ollama is ready.

## 5. Independent Verification Script

`scripts/verify_gateway.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

# 1. Ollama is running
docker compose ps ollama | grep -q "Up" || { echo "FAIL: ollama not running"; exit 1; }

# 2. The host cannot reach it
curl -sS --max-time 3 http://127.0.0.1:11434/api/tags && { echo "FAIL: port is exposed!"; exit 1; } || echo "PASS: host cannot reach ollama"

# 3. Internal containers can reach it
docker run --rm --network llm-pipeline_llm_internal curlimages/curl -sS --max-time 10 http://ollama:11434/api/tags >/dev/null && echo "PASS: internal reachable" || { echo "FAIL: internal unreachable"; exit 1; }

# 4. All allowlisted models are present
REQUIRED=$(jq -r '.approved[]' config/approved_model_tags.json)
AVAILABLE=$(docker run --rm --network llm-pipeline_llm_internal curlimages/curl -sS http://ollama:11434/api/tags | jq -r '.models[].name')
for m in $REQUIRED; do
  echo "$AVAILABLE" | grep -q "^$m$" && echo "PASS: $m present" || { echo "FAIL: $m missing"; exit 1; }
done
```

Run this script in CI and before release.

## 6. Troubleshooting Quick Reference

| Symptom | Cause | Fix |
|---|---|---|
| All LLM modules hang | Ollama is not ready | Check `docker logs ollama`; wait through `start_period` |
| `connection refused` | Wrong URL | Confirm it is `http://ollama:11434`, not localhost |
| Host can access 11434 | `ports:` was not removed | Check compose.yaml and confirm the runtime service has no ports |
| Model not found | Model was not seeded | Run `scripts/seed_model.sh <model>` |
| GPU is not used | Missing `deploy.resources` or missing driver | Run `docker run --rm --gpus all nvidia/cuda:12.3.2-base-ubuntu22.04 nvidia-smi` |

## 7. Build Checklist

- [ ] `compose.seed.yaml` is complete and runnable.
- [ ] The `ollama` service in `compose.yaml` has no `ports:`.
- [ ] The `internal: true` network is defined.
- [ ] Healthcheck is configured.
- [ ] `scripts/seed_model.sh` is executable.
- [ ] All four checks in `scripts/verify_gateway.sh` pass.
- [ ] `config/approved_model_tags.json` exists.
- [ ] README clearly states that seed must be run before first use.
