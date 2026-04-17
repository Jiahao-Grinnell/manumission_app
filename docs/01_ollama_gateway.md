# 模块 01 — ollama_gateway

> Ollama 容器本身 + 它的运行时网络契约。基础设施模块，不写 Python 业务代码。

## 1. 目的

所有模块里，**只有这一个**需要 GPU、占大内存（14B 模型约 9 GB VRAM）、并且是**共享依赖**。把它独立成一个网关，让其他模块通过内部网络调用它。

这个模块的"代码"主要是 **compose 定义、Dockerfile 参数、运行脚本**。不写 Python。

## 2. 两套部署形态

### 2.1 Seed 形态（临时联网下载模型）

`compose.seed.yaml`：
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
      - "127.0.0.1:11434:11434"    # ← 只绑 localhost
    deploy:
      resources:
        reservations:
          devices: [{ capabilities: [gpu] }]
    security_opt: [ no-new-privileges:true ]
    cap_drop: [ ALL ]
```

**关键点**：
- `127.0.0.1:11434` — 绑 localhost 而非 `0.0.0.0`，LAN 和公网都打不到
- 这个 compose 只在**下载模型时**起来，下完立刻 `down`
- 模型文件落地在 `./volumes/ollama/`，runtime 形态挂这个卷就能用

### 2.2 Runtime 形态（离线运行）

`compose.yaml` 里的 `ollama` 服务：
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
      - ./volumes/ollama:/home/ollama/.ollama:ro   # 只读，模型不可改
    networks:
      - llm_internal                                # ← 只在内部网络
    # 注意：没有 ports: 字段
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
    internal: true      # ← 关键：无外网出口
```

**关键点**：
- **没有 `ports:`** → 主机完全访问不到
- `internal: true` → 容器在这网络里 ping 不通外网（Ollama 再高的权限都没法联网）
- 卷挂成 `:ro` → 运行时不能往里写（防止模型被篡改）
- healthcheck → Compose 知道它什么时候可用

## 3. 模型管理

### 3.1 Approved model 清单

`config/approved_model_tags.json` 手工维护一个白名单，列出允许使用的模型。运行时 `OllamaClient.wait_ready()` 之后可选地校验当前 Ollama 里的模型在白名单内。

示例：
```json
{
  "approved": [
    "qwen2.5:14b-instruct",
    "mistral-small3.1:latest",
    "glm-ocr:latest"
  ]
}
```

### 3.2 下载脚本

`scripts/seed_model.sh`：
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

### 3.3 常用模型

| 模型 | 用途 | 大小 |
|---|---|---|
| `qwen2.5:14b-instruct` | 主力 NER 文本抽取 | ~9 GB |
| `mistral-small3.1:latest` | 备用 NER，速度快 | ~12 GB |
| `glm-ocr:latest` | OCR 视觉模型 | ~4 GB |
| `llama3.2:latest` | 小备用 | ~2 GB |

## 4. 客户端侧（shared.ollama_client）

Runtime 形态下，其他容器访问 Ollama：
```python
# shared/config.py
OLLAMA_URL = "http://ollama:11434/api/generate"    # 注意是 hostname `ollama`，不是 localhost
```

`OllamaClient.wait_ready()` 必须在模块启动时被调用一次：LLM 容器起来太快、Ollama 还没加载完模型时会连不上，启动时要 block 等它好。

## 5. 独立验证脚本

`scripts/verify_gateway.sh`：
```bash
#!/usr/bin/env bash
set -euo pipefail

# 1. Ollama 在跑
docker compose ps ollama | grep -q "Up" || { echo "FAIL: ollama not running"; exit 1; }

# 2. 主机打不到
curl -sS --max-time 3 http://127.0.0.1:11434/api/tags && { echo "FAIL: port is exposed!"; exit 1; } || echo "PASS: host cannot reach ollama"

# 3. 内部打得到
docker run --rm --network llm-pipeline_llm_internal curlimages/curl -sS --max-time 10 http://ollama:11434/api/tags >/dev/null && echo "PASS: internal reachable" || { echo "FAIL: internal unreachable"; exit 1; }

# 4. 白名单模型都在
REQUIRED=$(jq -r '.approved[]' config/approved_model_tags.json)
AVAILABLE=$(docker run --rm --network llm-pipeline_llm_internal curlimages/curl -sS http://ollama:11434/api/tags | jq -r '.models[].name')
for m in $REQUIRED; do
  echo "$AVAILABLE" | grep -q "^$m$" && echo "PASS: $m present" || { echo "FAIL: $m missing"; exit 1; }
done
```

CI 和发布前都应该跑这个脚本。

## 6. 故障排查速查

| 症状 | 原因 | 解决 |
|---|---|---|
| 所有 LLM 模块挂起 | Ollama 没 ready | 看 `docker logs ollama`；等 `start_period` |
| `connection refused` | 用了错 URL | 确认是 `http://ollama:11434`，不是 localhost |
| 主机能访问 11434 | `ports:` 没删干净 | 检查 compose.yaml，确认 runtime 服务没有 ports |
| 模型找不到 | 没 seed | 跑 `scripts/seed_model.sh <model>` |
| GPU 没用上 | `deploy.resources` 缺失或驱动没装 | `docker run --rm --gpus all nvidia/cuda:12.3.2-base-ubuntu22.04 nvidia-smi` |

## 7. 构建检查清单

- [ ] `compose.seed.yaml` 写好、能跑
- [ ] `compose.yaml` 里 `ollama` 服务无 `ports:`
- [ ] `internal: true` 网络定义了
- [ ] healthcheck 配置
- [ ] `scripts/seed_model.sh` 可执行
- [ ] `scripts/verify_gateway.sh` 四项全通过
- [ ] `config/approved_model_tags.json` 存在
- [ ] README 里写清楚 "第一次用要先跑 seed"
