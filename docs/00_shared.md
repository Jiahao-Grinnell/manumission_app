# 模块 00 — shared

> 共享核心库。所有其他模块都依赖它。**不是服务**，是纯 Python 包。

## 1. 目的

把所有模块都要用到的低层工具收口在一个地方：

- Ollama HTTP 客户端（带重试、超时、JSON 兜底）
- 数据模型（dataclass / pydantic）
- 路径约定（给 `doc_id` 返回所有相关目录）
- 文本工具（Unicode 规范化、空白合并、accent 剥离）
- 原子文件写入
- 日志和配置

## 2. 为什么单独列出来

如果不收口，你会在每个模块里看到复制粘贴的 `extract_json`、`clean_ocr`、`normalize_ws`，维护灾难。原 `ner_extract.py` 1800+ 行就是这么堆起来的。

## 3. 不做什么

- 不做规范化业务逻辑（名字/地名/日期 → 归 `08 normalizer`）
- 不做 Flask blueprint（它不是服务）
- 不依赖具体模型名或 prompt（模型名来自 `config.py`，prompt 来自 `config/prompts/`）

## 4. 目录结构

```
src/shared/
├── __init__.py
├── config.py               # 环境变量、默认值统一入口
├── paths.py                # 给 doc_id 返回所有路径
├── ollama_client.py        # OllamaClient
├── schemas.py              # 所有公开数据类型（PageDecision / CallStats / Row schemas）
├── text_utils.py           # normalize_ws / strip_accents / clean_ocr / extract_json
├── storage.py              # atomic write_csv / write_json / read_json
├── logging_setup.py        # 统一 logger
└── tests/
    ├── test_ollama_client.py
    ├── test_text_utils.py
    └── test_storage.py
```

## 5. 关键 API

### 5.1 `config.py`
```python
from shared.config import settings
settings.OLLAMA_URL        # "http://ollama:11434/api/generate"
settings.OLLAMA_MODEL      # "qwen2.5:14b-instruct"
settings.NUM_PREDICT       # 1200
settings.NUM_CTX           # None or int
settings.DATA_ROOT         # Path("/data")
settings.PROMPT_DIR        # Path("/app/config/prompts")
```
读取 `OLLAMA_URL`、`OLLAMA_MODEL`、`OLLAMA_NUM_PREDICT`、`OLLAMA_NUM_CTX`、`DATA_ROOT` 等环境变量，提供默认值。其他模块一律从这里取，禁止自己 `os.environ` 读。

### 5.2 `paths.py`
```python
from shared.paths import doc_paths
p = doc_paths("docABC")
p.pdf             # /data/input_pdfs/docABC.pdf
p.pages_dir       # /data/pages/docABC/
p.ocr_dir         # /data/ocr_text/docABC/
p.inter_dir       # /data/intermediate/docABC/
p.output_dir      # /data/output/docABC/
p.logs_dir        # /data/logs/docABC/
p.page_image(3)   # /data/pages/docABC/p003.png
p.ocr_text(3)     # /data/ocr_text/docABC/p003.txt
p.classify(3)     # /data/intermediate/docABC/p003.classify.json
p.names(3)        # /data/intermediate/docABC/p003.names.json
p.meta(3)         # /data/intermediate/docABC/p003.meta.json
p.places(3)       # /data/intermediate/docABC/p003.places.json
```
所有模块**只能通过这个对象取路径**，不允许自己拼字符串。改路径约定时改这一个文件就行。

### 5.3 `ollama_client.py`
```python
from shared.ollama_client import OllamaClient, CallStats

client = OllamaClient(url, model, num_predict, num_ctx)
stats = CallStats()

# 纯文本
text = client.generate("prompt...", stats)

# JSON，自带修复（失败时会重发 JSON_REPAIR_PROMPT）
obj = client.generate_json(prompt, schema_hint, stats, num_predict=900)

# 视觉（OCR 用）
text = client.generate_vision(prompt, image_b64, stats, num_predict=1200)

# 健康检查
client.wait_ready(timeout_s=240)
```
**要点**：
- 带 3 次重试 + 指数退避（从原代码搬）
- 超时 `(10, 600)`（连接 10s、读 600s）
- 用 `requests.Session` 复用 keep-alive
- 解析失败时自动发 JSON 修复 prompt（来自 `config/prompts/json_repair.txt`）

### 5.4 `schemas.py`
用 `pydantic.BaseModel` 或 `dataclass` 定义：
```python
class PageDecision(BaseModel):
    should_extract: bool
    skip_reason: Optional[Literal["index", "record_metadata", "bad_ocr"]]
    report_type: Literal["statement", "transport/admin", "correspondence"]
    evidence: str = ""

class NamedPerson(BaseModel):
    name: str
    evidence: str

class DetailRow(BaseModel):
    name: str; page: int; report_type: str
    crime_type: str = ""; whether_abuse: str = ""
    conflict_type: str = ""; trial: str = ""; amount_paid: str = ""

class PlaceRow(BaseModel):
    name: str; page: int
    place: str; order: int = 0
    arrival_date: str = ""; date_confidence: str = ""; time_info: str = ""
    evidence: str = ""  # 内部用，CSV 导出时丢

class CallStats(BaseModel):
    model_calls: int = 0; repair_calls: int = 0
```

### 5.5 `text_utils.py`
纯字符串操作，不涉及领域语义：
```python
normalize_ws(s) -> str
strip_accents(s) -> str
clean_ocr(s) -> str                # 统一换行/tab/BOM
extract_json(s) -> Any | None      # 从含噪 LLM 响应里抠 JSON
render_prompt(template, **kw) -> str
```

### 5.6 `storage.py`
```python
write_csv_atomic(path, rows, columns)   # tmp + rename
write_json_atomic(path, obj)
read_json(path) -> Any
```
原子写很关键：一旦某次跑到一半挂了，不希望留下半写入的 CSV 把后续续跑搞乱。

### 5.7 `logging_setup.py`
```python
setup_logger(module_name: str, log_dir: Path, verbose: bool=False) -> logging.Logger
```
统一的 format：`%(asctime)s | %(levelname)s | %(name)s | %(message)s`。

## 6. 怎么测

```bash
cd src/shared && pytest tests/
```

**不需要** Flask，**不需要** Docker（在主机 Python 虚拟环境里能跑）。测试重点：

- `extract_json` 对 30 种畸形输入的容错（markdown fence、前后文字、截断）
- `OllamaClient.generate_json` 用 `requests_mock` 模拟失败→重试→兜底修复
- `paths.doc_paths` 对各种 `doc_id`（含空格、中文、特殊字符）的行为

## 7. Docker

没有自己的容器。它被 `docker/base.Dockerfile` 复制进 `/app/shared/`，所有其他模块的镜像 `FROM base` 时天然带上它。

## 8. 构建检查清单

- [ ] 所有原脚本里的底层工具都搬过来了
- [ ] 单元测试覆盖率 > 80%（这层是可以有量化指标的）
- [ ] 没有任何 Flask 导入（强制纯库）
- [ ] 没有任何 `os.environ`（全走 config.py）
- [ ] 没有硬编码路径（全走 paths.py）

## 9. 典型用法

```python
from shared.config import settings
from shared.paths import doc_paths
from shared.ollama_client import OllamaClient, CallStats
from shared.storage import write_csv_atomic
from shared.text_utils import clean_ocr

paths = doc_paths("docABC")
client = OllamaClient(settings.OLLAMA_URL, settings.OLLAMA_MODEL, settings.NUM_PREDICT, settings.NUM_CTX)
stats = CallStats()

text = clean_ocr(paths.ocr_text(3).read_text())
obj = client.generate_json(prompt, schema, stats)
```
