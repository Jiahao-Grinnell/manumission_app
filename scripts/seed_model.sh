#!/usr/bin/env bash
set -euo pipefail

MODEL="${1:?usage: seed_model.sh <model-tag>}"

docker compose -f compose.yaml stop ollama >/dev/null 2>&1 || true
docker compose -f compose.seed.yaml --profile seed up -d ollama_seed
docker compose -f compose.seed.yaml --profile seed exec -T ollama_seed ollama pull "$MODEL"
docker compose -f compose.seed.yaml --profile seed exec -T ollama_seed ollama list
docker compose -f compose.seed.yaml --profile seed down

echo "Done. Model $MODEL persisted under ./volumes/ollama/"
echo "Start runtime again with: docker compose up -d ollama"
