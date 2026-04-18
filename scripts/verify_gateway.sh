#!/usr/bin/env bash
set -euo pipefail

PROJECT_NAME="${COMPOSE_PROJECT_NAME:-manumission_app}"
NETWORK_NAME="${PROJECT_NAME}_llm_internal"
APPROVED_FILE="${APPROVED_FILE:-config/approved_model_tags.json}"
CURL_IMAGE="${CURL_IMAGE:-curlimages/curl:latest}"
VERIFY_GENERATE_TIMEOUT="${VERIFY_GENERATE_TIMEOUT:-900}"

RUNTIME_COMPOSE_FILES=(-f compose.yaml)
SEED_COMPOSE_FILES=(-f compose.seed.yaml)

echo "Checking compose configuration..."
docker compose "${RUNTIME_COMPOSE_FILES[@]}" config >/dev/null
docker compose "${SEED_COMPOSE_FILES[@]}" --profile seed config >/dev/null

echo "Checking runtime Ollama container..."
docker compose "${RUNTIME_COMPOSE_FILES[@]}" up -d ollama >/dev/null
docker compose "${RUNTIME_COMPOSE_FILES[@]}" ps ollama

echo "Checking Docker GPU device request..."
CONTAINER_ID="$(docker compose "${RUNTIME_COMPOSE_FILES[@]}" ps -q ollama)"
if ! docker inspect --format '{{json .HostConfig.DeviceRequests}}' "${CONTAINER_ID}" | grep -q "gpu"; then
  echo "FAIL: Ollama container was not started with a Docker GPU device request"
  exit 1
fi
echo "PASS: Docker GPU device request is present"

echo "Checking host isolation..."
if curl -fsS --max-time 3 http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
  echo "FAIL: host can reach runtime Ollama on 127.0.0.1:11434"
  exit 1
fi
echo "PASS: host cannot reach runtime Ollama"

echo "Checking internal reachability..."
docker run --rm --network "${NETWORK_NAME}" "${CURL_IMAGE}" -fsS --max-time 10 \
  http://ollama:11434/api/version
echo

echo "Checking approved model file..."
test -f "${APPROVED_FILE}"

echo "Checking approved models present in Ollama..."
python3 - <<'PY' "${APPROVED_FILE}" > /tmp/required_models.txt
import json
import sys
from pathlib import Path

data = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
required = data.get("required") or data.get("approved", [])
for model in required:
    print(model)
PY

docker run --rm --network "${NETWORK_NAME}" "${CURL_IMAGE}" -fsS --max-time 10 \
  http://ollama:11434/api/tags > /tmp/ollama_tags.json

python3 - <<'PY'
import json
from pathlib import Path

required = [line.strip() for line in Path("/tmp/required_models.txt").read_text().splitlines() if line.strip()]
available_payload = json.loads(Path("/tmp/ollama_tags.json").read_text())
available = {item.get("name") for item in available_payload.get("models", [])}
missing = [model for model in required if model not in available]
if missing:
    print("FAIL: missing required models:")
    for model in missing:
        print(f"  - {model}")
    raise SystemExit(1)
print("PASS: all required models are present")
PY

VERIFY_MODEL="${VERIFY_MODEL:-$(head -n 1 /tmp/required_models.txt)}"

echo "Checking model generation through Ollama HTTP API..."
python3 - <<'PY' "${VERIFY_MODEL}" > /tmp/ollama_verify_payload.json
import json
import sys

payload = {
    "model": sys.argv[1],
    "prompt": "Reply with exactly OK.",
    "stream": False,
    "options": {"num_predict": 10},
}
print(json.dumps(payload))
PY

if ! docker run --rm --network "${NETWORK_NAME}" -i "${CURL_IMAGE}" \
  -fsS --max-time "${VERIFY_GENERATE_TIMEOUT}" \
  -H "Content-Type: application/json" \
  --data-binary @- \
  http://ollama:11434/api/generate \
  < /tmp/ollama_verify_payload.json \
  > /tmp/ollama_verify_generation.json; then
  echo "FAIL: tiny HTTP generation failed or timed out after ${VERIFY_GENERATE_TIMEOUT}s"
  exit 1
fi

python3 - <<'PY'
import json
from pathlib import Path

payload = json.loads(Path("/tmp/ollama_verify_generation.json").read_text(encoding="utf-8"))
if payload.get("error"):
    print(f"FAIL: Ollama returned error: {payload['error']}")
    raise SystemExit(1)
if "response" not in payload:
    print("FAIL: Ollama response did not include a response field")
    raise SystemExit(1)
print("PASS: tiny HTTP generation returned a response")
PY

echo "Checking loaded model processor..."
PS_OUTPUT="$(docker compose "${RUNTIME_COMPOSE_FILES[@]}" exec -T ollama ollama ps)"
echo "${PS_OUTPUT}"
if ! echo "${PS_OUTPUT}" | grep -Eq "GPU"; then
  echo "FAIL: Ollama model is not reported as running on GPU"
  echo "If the model unloaded before this check, rerun this script or run: docker compose exec -T ollama ollama ps"
  exit 1
fi
echo "PASS: Ollama model is running on GPU"

echo "Gateway verification complete."
