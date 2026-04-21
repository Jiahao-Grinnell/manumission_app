# Module 00 - shared

> Shared core library. All other modules depend on it. It is **not a service**; it is a pure Python package.

## 1. Purpose

Centralize the low-level tools used by all modules:

- Ollama HTTP client with retries, timeouts, and JSON fallback repair
- Data models with dataclass / pydantic
- Path conventions that return every related directory for a `doc_id`
- Text utilities for Unicode normalization, whitespace merging, and accent stripping
- Atomic file writes
- Logging and configuration

## 2. Why This Is Separate

Without a shared library, every module would contain copied versions of `extract_json`, `clean_ocr`, and `normalize_ws`, which is a maintenance problem. The original `ner_extract.py` grew to 1800+ lines for exactly this reason.

## 3. What It Does Not Do

- It does not implement normalization business logic. Name, place, and date normalization belong to `08 normalizer`.
- It does not provide a Flask blueprint, because it is not a service.
- It does not depend on concrete model names or prompts. Model names come from `config.py`; prompts come from `config/prompts/`.

## 4. Directory Structure

```text
src/shared/
|-- __init__.py
|-- config.py               # Unified entry point for environment variables and defaults
|-- paths.py                # Returns all paths for a doc_id
|-- ollama_client.py        # OllamaClient
|-- schemas.py              # Public data types: PageDecision / CallStats / row schemas
|-- text_utils.py           # normalize_ws / strip_accents / clean_ocr / extract_json
|-- storage.py              # atomic write_csv / write_json / read_json
|-- logging_setup.py        # Unified logger
`-- tests/
    |-- test_ollama_client.py
    |-- test_text_utils.py
    `-- test_storage.py
```

## 5. Key APIs

### 5.1 `config.py`

```python
from shared.config import settings
settings.OLLAMA_URL        # "http://ollama:11434/api/generate"
settings.OLLAMA_MODEL      # "qwen2.5:14b-instruct"
settings.NUM_PREDICT       # 1200
settings.NUM_CTX           # None or int
settings.DATA_ROOT         # Path("/data")
settings.PROMPT_DIR        # Path("/app/config/prompts")
settings.MAX_UPLOAD_BYTES  # Configurable; large PDFs can also be registered from disk
```

Reads environment variables such as `OLLAMA_URL`, `OLLAMA_MODEL`, `OLLAMA_NUM_PREDICT`, `OLLAMA_NUM_CTX`, and `DATA_ROOT`, and provides defaults. All other modules must read settings from here instead of calling `os.environ` directly.

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
p.audit_dir       # /data/audit/docABC/
p.page_image(3)   # /data/pages/docABC/p003.png
p.ocr_text(3)     # /data/ocr_text/docABC/p003.txt
p.classify(3)     # /data/intermediate/docABC/p003.classify.json
p.names(3)        # /data/intermediate/docABC/p003.names.json
p.meta(3)         # /data/intermediate/docABC/p003.meta.json
p.places(3)       # /data/intermediate/docABC/p003.places.json
```

All modules **must retrieve paths through this object** instead of manually concatenating strings. If the path convention changes, only this file should need to change.

### 5.3 `ollama_client.py`

```python
from shared.ollama_client import OllamaClient, CallStats

client = OllamaClient(url, model, num_predict, num_ctx)
stats = CallStats()

# Text-only generation
text = client.generate("prompt...", stats)

# JSON generation with built-in repair. On failure, JSON_REPAIR_PROMPT is sent.
obj = client.generate_json(prompt, schema_hint, stats, num_predict=900)

# Vision generation for OCR
text = client.generate_vision(prompt, image_b64, stats, num_predict=1200)

# Health check
client.wait_ready(timeout_s=240)
```

Key points:

- Three retries with exponential backoff, inherited from the original code.
- Timeout `(10, 600)`: 10 seconds to connect, 600 seconds to read.
- `requests.Session` is used to reuse keep-alive connections.
- If JSON parsing fails, the client automatically sends a JSON repair prompt from `config/prompts/shared/json_repair.txt`.

### 5.4 `schemas.py`

Define models with `pydantic.BaseModel` or `dataclass`:

```python
class PageDecision(BaseModel):
    should_extract: bool
    skip_reason: Optional[Literal["index", "record_metadata", "bad_ocr"]]
    report_type: Literal["statement", "correspondence"]
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
    evidence: str = ""  # Internal only; dropped during CSV export

class CallStats(BaseModel):
    model_calls: int = 0; repair_calls: int = 0
```

### 5.5 `text_utils.py`

Pure string operations with no domain semantics:

```python
normalize_ws(s) -> str
strip_accents(s) -> str
clean_ocr(s) -> str                # Normalize newlines, tabs, and BOM
extract_json(s) -> Any | None      # Extract JSON from noisy LLM responses
render_prompt(template, **kw) -> str
```

### 5.6 `storage.py`

```python
write_csv_atomic(path, rows, columns)   # tmp + rename
write_json_atomic(path, obj)
read_json(path) -> Any
artifact_ok(path, kind) -> bool         # non-empty text / parseable JSON / existing image
```

Atomic writes are critical. If a run crashes halfway through, the pipeline should not leave a half-written CSV that breaks a later resume.

`artifact_ok` is the common validation helper used by the orchestrator and module UIs. It should check OCR text files, JSON files, and image artifacts consistently instead of relying on file existence alone.

### 5.7 `logging_setup.py`

```python
setup_logger(module_name: str, log_dir: Path, verbose: bool=False) -> logging.Logger
```

Unified format: `%(asctime)s | %(levelname)s | %(name)s | %(message)s`.

## 6. How to Test

```bash
cd src/shared && pytest tests/
```

No Flask or Docker is required. These tests should run in a host Python virtual environment. Focus areas:

- `extract_json` tolerance for about 30 malformed inputs, including markdown fences, leading or trailing prose, and truncated text.
- `OllamaClient.generate_json` with `requests_mock` simulating failure, retry, and repair fallback.
- `paths.doc_paths` behavior for many `doc_id` values, including spaces, Chinese characters, and special characters.

## 7. Docker

This module has no container of its own. `docker/base.Dockerfile` copies it into `/app/shared/`, and every other module image naturally includes it when it uses `FROM base`.

## 8. Build Checklist

- [ ] All low-level tools from the original scripts have been moved here.
- [ ] Unit test coverage is above 80%, which is a realistic metric for this layer.
- [ ] There are no Flask imports, keeping it a pure library.
- [ ] There are no direct `os.environ` calls; everything goes through config.py.
- [ ] There are no hard-coded paths; everything goes through paths.py.
- [ ] Shared artifact validation exists for text, JSON, image, and CSV artifacts.

## 9. Typical Usage

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
