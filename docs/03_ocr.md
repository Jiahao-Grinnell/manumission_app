# Module 03 - ocr

> Page image to OCR text. This is the first module in the pipeline that calls an LLM, and it is the heaviest visual-processing step.

## 1. Purpose

Run OCR page by page over `data/pages/<doc_id>/p*.png` and produce `data/ocr_text/<doc_id>/p*.txt`.

The underlying logic **inherits the approach from the original `glm_ocr_ollama.py`**: traditional CV preprocessing (deskew, enhancement, crop, tiling), send each tile to the vision model, merge text, and fall back to a single full-page model call.

Implementation status: Phase 3 was implemented on 2026-04-20. Unit tests run without a live LLM by mocking the Ollama image call; the standalone OCR service starts at `http://127.0.0.1:5103/ocr/`; preprocessing preview has been smoke-tested against `data/pages/upload_fixture/p001.png`; and a live `glm-ocr:latest` smoke test completed 2/2 `upload_fixture` pages into `data/ocr_text/upload_fixture/`.

## 2. Input / Output

**Input**:

- `data/pages/<doc_id>/p*.png` from module 02 `pdf_ingest`
- Model name, defaulting to `glm-ocr:latest`
- Runtime parameters such as tile count and `max_new_tokens`

**Output**:

```text
data/ocr_text/<doc_id>/
|-- p001.txt
|-- p002.txt
|-- ...
|-- run_status.log
|-- manifest.json               # Per-page OCR status and text statistics
`-- _debug/                    # When debug=True
    |-- p001__prep_0.png       # Preprocessed tile 0
    |-- p001__prep_1.png       # Preprocessed tile 1
    |-- p001__resp_0.json      # Raw model response for tile 0
    |-- p001__resp_1.json
    `-- p001__raw_0.txt        # Plain text response
```

Conventions:

- Text files should preserve original line breaks as much as possible.
- Pages that cannot be OCRed should contain the literal value `[OCR_EMPTY]`. Do not leave empty files; downstream modules need to know the page was attempted.
- OCR text is a durable intermediate artifact, not a temporary cache. Downstream classifier, name, metadata, and place modules must read from `data/ocr_text/<doc_id>/pNNN.txt` instead of re-running OCR.
- After every page, write or update `manifest.json` atomically with status, character count, elapsed time, model name, tile count, and any error. This makes large-document resume and dashboard display reliable.
- Debug image artifacts are useful but can be large. Keep them optional, and support a retention policy such as "keep debug only for failed pages" for full-size runs.

## 3. Core Algorithm (Inherited From the Original System)

```text
page.png
  |
  v enhance_gray              # Median blur background removal + CLAHE + unsharp
  |
  v deskew                    # Correct skew with minAreaRect
  |
  v crop_foreground           # Foreground crop using adaptive thresholding
  |
  v resize_long_side          # Ensure long side >= 1800 for OCR resolution
  |
  v split_vertical_with_overlap(parts=2, overlap=200)
  |
  v for each slice:
  |     base64(slice) -> ollama vision generate(prompt, image)
  |     -> cleanup_ocr_text(response)
  |
  v join non-empty slice texts with "\n\n"
  |
  v if empty: fallback to a single full-image call
  |
  v if still empty: write "[OCR_EMPTY]"
```

Move the original OCR prompt into `config/prompts/ocr/ocr.txt` unchanged:

```text
You are an OCR engine. Transcribe ALL visible text from the image.
Rules:
- Output ONLY the text (no markdown, no code fences).
- Preserve line breaks as best as possible.
- Do not add commentary or explanations.
- If you cannot read any text, output exactly: [OCR_EMPTY]
```

## 4. Directory Structure

```text
src/modules/ocr/
|-- __init__.py
|-- core.py                   # run_folder / ocr_page main flow
|-- preprocessing.py          # enhance_gray / deskew / crop_foreground / resize / tile
|-- blueprint.py              # Flask
|-- standalone.py
|-- cli.py
|-- templates/
|   `-- ui.html
|-- static/
|   `-- ocr.css
`-- tests/
    `-- test_preprocessing.py    # CV plus mocked core flow, no live LLM
```

Every function in `preprocessing.py` should be independently callable and testable, decoupled from the LLM.

## 5. Blueprint API

| Method | Path | Behavior |
|---|---|---|
| GET | `/ocr/` | Test UI |
| GET | `/ocr/docs` | List all `doc_id` values with ingested page artifacts |
| GET | `/ocr/pages/<doc_id>` | List all OCRable pages for the document |
| POST | `/ocr/preview/<doc_id>/<page>` | Run preprocessing only, without calling the LLM, and return five intermediate images as base64 |
| POST | `/ocr/run-single/<doc_id>/<page>` | Run full OCR for one page, including LLM call |
| POST | `/ocr/run-all/<doc_id>` | Run the whole document asynchronously and return `job_id` |
| GET | `/ocr/debug/<doc_id>/<page>` | Return debug directory contents for the page, if debug is enabled |
| GET | `/ocr/text/<doc_id>/<page>` | Return existing OCR text |
| GET | `/ocr/status/<doc_id>` | Return OCR progress for the document |

## 6. CLI

Fully compatible with the original script parameters:

```bash
python -m modules.ocr.cli \
  --in_dir /data/pages/myDoc \
  --out_dir /data/ocr_text/myDoc \
  --model glm-ocr:latest \
  --ollama_url http://ollama:11434/api/generate \
  --no_debug \
  --max_new_tokens 1200
```

## 7. Test UI Design

This UI must let a human debug every OCR step visually:

```text
+-------------------------------------------------------------------+
|  Doc: [ myDoc ]   Page: [ p012 ]   Model: [ glm-ocr ]             |
|  [ Preview only ]   [ Run OCR on this page ]   [ Run all pages ]  |
+-------------------------------------------------------------------+
|  Preprocessing pipeline                                           |
|  [orig] -> [enhanced] -> [deskewed] -> [crop] -> [tile0] [tile1]  |
|  Click any image to enlarge. Hover shows dimensions and timing.   |
+-------------------------------------------------------------------+
|  Model responses                                                  |
|  [ Tile 0 response ]                 [ Tile 1 response ]          |
|  elapsed: 4.3s, chars: 1203          elapsed: 3.9s, chars: 872    |
|  <OCR text here>                     <OCR text here>              |
|  [Raw JSON]                          [Raw JSON]                   |
+-------------------------------------------------------------------+
|  Final output (joined)                                            |
|  <merged page text>                                               |
|  [ Download .txt ]                                                |
+-------------------------------------------------------------------+
```

Visualization goals:

1. The **five-image strip** is the primary debugging view. It makes preprocessing mistakes obvious, such as over-deskewing or crop cutting off body text.
2. **Side-by-side model responses** show whether each tile produced reasonable text or whether one tile came back empty.
3. **Raw JSON collapse panels** stay hidden by default but are available for model metadata such as `done_reason` and `eval_count`.
4. Small **elapsed / chars badges** let the user scan performance and output volume quickly.
5. The UI should always show where the persisted OCR text lives, for example `data/ocr_text/<doc_id>/p012.txt`, and whether that file is parseable and non-empty.

## 8. Docker

`docker/ocr.Dockerfile`:

```dockerfile
FROM python:3.11-slim

WORKDIR /app
RUN groupadd --gid 10001 appuser \
    && useradd --uid 10001 --gid 10001 --create-home --home-dir /home/appuser appuser \
    && apt-get update \
    && apt-get install -y --no-install-recommends libglib2.0-0 libgl1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements/base.txt /tmp/base.txt
COPY requirements/ocr.txt /tmp/requirements-ocr.txt
RUN pip install --no-cache-dir -r /tmp/requirements-ocr.txt

COPY config /app/config
COPY src/shared /app/shared
COPY src/modules/__init__.py /app/modules/__init__.py
COPY src/modules/ocr /app/modules/ocr

ENV PYTHONPATH=/app
USER 10001:10001
```

`requirements/ocr.txt`:

```text
-r base.txt
numpy>=1.26,<3
opencv-python-headless>=4.8,<5
```

Compose fragment:

```yaml
  ocr:
    build:
      context: .
      dockerfile: docker/ocr.Dockerfile
    depends_on:
      ollama:
        condition: service_healthy
    networks: [ llm_internal, llm_frontend ]
    volumes:
      - ./data:/data
    ports:
      - "127.0.0.1:5103:5103"
    profiles: [ "ocr", "standalone", "all" ]
    command: >
      gunicorn -b 0.0.0.0:5103 -w 1 --timeout 1800
      'modules.ocr.standalone:create_app()'
```

`--timeout 1800`: a single OCR page may take tens of seconds or minutes, so gunicorn must not kill the worker prematurely.

## 9. Tests

Unit tests without LLM:

- Each `preprocessing.py` function should process clean, skewed, and noisy fixtures without throwing and with valid output dimensions.
- `cleanup_ocr_text` should remove markdown fences.
- `should_skip_existing` should behave correctly for empty files, `[OCR_EMPTY]`, and normal text.
- `ocr_page` and `run_folder` should be covered with mocked Ollama calls.

Implemented verification:

```bash
docker build -f docker/ocr.Dockerfile -t manumission-ocr:phase3 .
docker run --rm manumission-ocr:phase3 python -m unittest discover -s /app/modules/ocr/tests -p "test_*.py"
docker compose --profile ocr up -d --build ocr
curl http://127.0.0.1:5103/healthz
docker compose --profile ocr run --rm ocr python -m modules.ocr.cli \
  --in_dir /data/pages/upload_fixture \
  --out_dir /data/ocr_text/upload_fixture \
  --model glm-ocr:latest \
  --ollama_url http://ollama:11434/api/generate \
  --no_debug \
  --max_new_tokens 1200
```

Integration tests requiring Ollama:

- Use a clear English page and assert the OCR output contains a known substring.
- Use a blank page and assert the output is `[OCR_EMPTY]`.
- Add a sample-input smoke test that runs OCR on 2 to 5 selected pages from the local sample PDFs.
- Add a manual full-input resume test: start OCR on a large PDF, stop after several pages, restart, and verify completed `pNNN.txt` files are skipped.

Mark these integration tests with `pytest -m integration` so CI can skip them.

## 10. Performance / Failure Modes

- **Single-page time**: glm-ocr 7B on a 16 GB GPU takes about 3 to 10 seconds per tile, or 6 to 20 seconds per two-tile page.
- **Large batches**: default to serial processing. Parallelism requires Ollama multi-slot support or multiple instances.
- **Intermediate storage**: OCR text is expected to accumulate under `data/ocr_text/`. It should be small compared with rendered images and must not be deleted automatically.
- **OOM**: if an image is too large and the vision model OOMs, reduce the `preprocess_long` parameter.
- **Model hallucination**: vision models sometimes return explanatory prose like "the document appears to be X" instead of OCR text. The prompt forbids this, but `cleanup_ocr_text` should strip known patterns if they appear.

## 11. Build Checklist

- [x] All `preprocessing.py` functions are moved over and pass standalone unit tests.
- [x] `core.py` implements `ocr_page` and `run_folder`.
- [x] `cleanup_ocr_text` removes fences and explanatory prose.
- [x] All blueprint routes are implemented.
- [x] CLI is compatible with the original script arguments.
- [x] Test UI shows the preprocessing images and final merged text for saved OCR output.
- [x] OCR text is persisted as `data/ocr_text/<doc_id>/pNNN.txt` and surfaced in the UI.
- [x] OCR `manifest.json` records per-page status, timing, model, and character counts.
- [x] Resume behavior through `should_skip_existing` works.
- [x] `_debug/` artifacts are generated when debug is enabled.
- [ ] Debug retention policy can avoid storing huge debug directories for successful pages.
- [x] Standalone container starts.
- [x] Full live-model OCR smoke test passes with `glm-ocr:latest` in runtime Ollama.
