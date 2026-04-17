#!/usr/bin/env bash
set -euo pipefail

MODEL="${1:?usage: seed_model.sh <model-tag>}"

docker compose -f compose.seed.yaml --profile seed up -d ollama_seed
docker exec -i ollama_seed ollama pull "$MODEL"
docker exec -i ollama_seed ollama list
docker compose -f compose.seed.yaml --profile seed down

echo "Done. Model $MODEL persisted under ./volumes/ollama/"

