# Module 04 - page_classifier

> Decides whether a page has any visible personal names and, if so, what report type it is. This is the first downstream gate, but it intentionally leaves subject-role decisions to `name_extractor`.

Status: implemented on 2026-04-21 as a prompt-backed classifier with CLI, Flask blueprint, standalone UI, report-type and visible-name rule inspection, and fixture-based unit tests.

## 1. Purpose

Given one page of OCR text, make a single LLM call and output:

- `should_extract: bool`: extract or skip
- `skip_reason: "index" | "record_metadata" | "bad_ocr" | null`
- `report_type: "statement" | "correspondence"`
- `evidence: str`: a quote of no more than 25 words

This is the gatekeeper for all downstream modules. Its final extract/skip rule is deliberately simple: keep pages with visible personal names and skip pages without visible personal names. It intentionally does not decide which names are enslaved/manumission subjects; later modules make that call.

## 2. Input / Output

**Input**: `data/ocr_text/<doc_id>/p*.txt`

**Output**: `data/intermediate/<doc_id>/p*.classify.json`

```json
{
  "page": 12,
  "should_extract": true,
  "skip_reason": null,
  "report_type": "statement",
  "evidence": "Statement of slave Mariam bint Yusuf, aged about 20",
  "model_calls": 1,
  "repair_calls": 0,
  "elapsed_seconds": 3.4,
  "raw_decision": {
    "should_extract": true,
    "skip_reason": null,
    "report_type": "correspondence",
    "evidence": "Statement of slave Mariam bint Yusuf"
  },
  "initial_decision": {
    "should_extract": true,
    "skip_reason": null,
    "report_type": "correspondence",
    "evidence": "Statement of slave Mariam bint Yusuf"
  },
  "override": {
    "from": "correspondence",
    "to": "statement",
    "applied": true,
    "applied_by": "statement_report"
  }
}
```

## 3. Core Algorithm

The LLM supplies a structured page decision, evidence, and report-type guess, but the final `should_extract` value is controlled by the OCR-based visible-name gate:

```python
def classify(ocr: str, stats: CallStats, *, report_type_override=None) -> PageDecision:
    schema = '{"should_extract":true,"skip_reason":null,"report_type":"statement","evidence":"..."}'
    obj = client.generate_json(render(PAGE_CLASSIFY_PROMPT, ocr=ocr), schema, stats, num_predict=500)
    decision = parse_page_decision(obj)

    skip_override = explain_skip_override(ocr, decision)
    decision.should_extract = not skip_override["should_skip"]
    decision.skip_reason = skip_override["skip_reason"]

    decision.report_type = override_report_type_from_ocr(ocr, decision.report_type)
    return decision
```

`override_report_type_from_ocr` is an important but conservative fallback. It corrects the model to `statement` only for strong subject-statement signals, such as `Statement of slave Mariam bint Yusuf`, `statement made by <real name>`, or clear first-person statement openings such as `I was born` / `I was kidnapped`. Generic phrases such as `statement made by the slave` and correspondence phrasing such as `I request` do not force `statement`. Administrative, forwarding, certificate, passage, and transport signals fall under `correspondence`.

`explain_skip_override` is the final extract/skip rule layer. It follows one rule: pages with any visible personal name stay extractable, even if the name is an official, buyer, owner, seller, witness, or signatory. Pages with no visible personal names are skipped, using `bad_ocr` only when the no-name page is unreadable and otherwise `record_metadata`. Diagnostic hints such as administrative-forwarding or index patterns can still be shown in the UI, but they do not independently force a skip.

Load the prompt from `config/prompts/page_classifier/page_classify.txt`, moved from the original `PAGE_CLASSIFY_PROMPT`.

## 4. Directory Structure

```text
src/modules/page_classifier/
|-- __init__.py
|-- core.py              # classify()
|-- rules.py             # STATEMENT_REPORT_PAT / CORRESPONDENCE_REPORT_PAT and related heuristics
|-- parsing.py           # parse_page_decision()
|-- blueprint.py
|-- standalone.py
|-- cli.py
|-- static/
|   `-- page_classifier.css
|-- templates/
|   `-- ui.html
`-- tests/
    |-- test_core.py
    |-- test_rules.py
    |-- test_parsing.py
    `-- fixtures/
        |-- correspondence_page.txt
        |-- statement_page.txt
        |-- transport_page.txt
        |-- index_page.txt
        |-- bad_ocr_page.txt
        |-- admin_forwarding_p234.txt
        `-- admin_forwarding_p279.txt
```

## 5. Blueprint API

| Method | Path | Behavior |
|---|---|---|
| GET | `/classify/` | Test UI |
| GET | `/classify/docs` | List classifiable `doc_id` values with completed OCR |
| GET | `/classify/pages/<doc_id>` | List all OCR-completed pages for the document |
| POST | `/classify/run-single/<doc_id>/<page>` | Classify one page and return the result |
| POST | `/classify/run-all/<doc_id>` | Classify the whole document asynchronously |
| GET | `/classify/result/<doc_id>/<page>` | Return the current page payload plus any existing result |
| GET | `/classify/jobs/<job_id>` | Poll an async whole-document job |

## 6. CLI

```bash
python -m modules.page_classifier.cli \
  --in_dir /data/ocr_text/myDoc \
  --out_dir /data/intermediate/myDoc \
  --model qwen2.5:14b-instruct \
  [--page 12] \
  [--report-type statement]      # Forced override for debugging
```

## 7. Test UI Design

```text
+-----------------------------------------------------------------+
|  Doc: [ myDoc ]   Page: [ p012 ]                                |
|  [ Classify this page ]                                         |
+-----------------------------------------------------------------+
|  Verdict                                                        |
|  should_extract: YES                                            |
|  report_type:     STATEMENT or CORRESPONDENCE                   |
|  skip_reason:     -                                             |
|  evidence:        "Statement of slave Mariam bint Yusuf..."     |
|  model_calls: 1   repair_calls: 0   elapsed: 3.4s               |
+-----------------------------------------------------------------+
|  Rule/override check                                            |
|  STATEMENT pattern:        matched -> would override -> no change |
|  CORRESPONDENCE pattern:   not matched                            |
|  Visible personal name:    true -> final should_extract true       |
+-----------------------------------------------------------------+
|  OCR text with evidence highlighted                             |
|  Statement of slave Mariam bint Yusuf, aged about 20 years, ... |
+-----------------------------------------------------------------+
|  [ Raw model response ]                                         |
|  { "should_extract": true, "skip_reason": null, ... }           |
+-----------------------------------------------------------------+
```

Visualization goals:

- **Verdict badge**: different `report_type` values should use different colors, for example statement green and correspondence gray.
- **Evidence highlighted in source text**: use fuzzy string matching; if the evidence cannot be located, show "evidence not located in text".
- **Rule/override comparison**: if deterministic rules and model output disagree, the UI should call that out clearly.
- **Raw response collapse panel**: useful for occasional JSON debugging.

## 8. Docker

`docker/ner.Dockerfile`, shared by all NER modules:

```dockerfile
FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app
WORKDIR /app
COPY requirements/base.txt /tmp/requirements-base.txt
RUN pip install --no-cache-dir -r /tmp/requirements-base.txt
COPY src/shared /app/shared
COPY src/modules/page_classifier /app/modules/page_classifier
COPY src/modules/name_extractor /app/modules/name_extractor
COPY src/modules/metadata_extractor /app/modules/metadata_extractor
COPY src/modules/place_extractor /app/modules/place_extractor
COPY src/modules/normalizer /app/modules/normalizer
COPY config/prompts /app/config/prompts
USER 10001:10001
```

Compose:

```yaml
  page_classifier:
    build:
      context: .
      dockerfile: docker/ner.Dockerfile
    depends_on:
      ollama:
        condition: service_healthy
    networks: [ llm_internal, llm_frontend ]
    volumes:
      - ./data:/data
    ports:
      - "127.0.0.1:5104:5104"
    profiles: [ "classifier", "standalone", "all" ]
    command: >
      gunicorn -b 0.0.0.0:5104 -w 1 --timeout 600
      'modules.page_classifier.standalone:create_app()'
```

## 9. Tests

Unit tests:

- `parse_page_decision` behavior for valid JSON, missing fields, and invalid `report_type`.
- `override_report_type_from_ocr` with fixtures that match STATEMENT, CORRESPONDENCE, and no pattern.
- Regression coverage that generic `statement made by the slave` and correspondence `I request` wording do not force `statement`.

Integration tests:

- Run `classify()` on fixtures covering statement, correspondence, index/bad-OCR pages, no-name administrative forwarding pages, and pages where the model asks to skip despite a visible personal name. Assert that no-name pages skip and any visible-name page stays extractable.

Verification commands:

```bash
docker compose --profile classifier config
docker build -f docker/ner.Dockerfile -t manumission-ner:phase4_1 .
docker run --rm manumission-ner:phase4_1 python -m unittest discover -s /app/modules/page_classifier/tests -p "test_*.py"
docker compose --profile classifier up -d --build page_classifier
curl http://127.0.0.1:5104/healthz
```

## 10. Build Checklist

- [x] Prompt is moved to a file and `render_prompt` injects it correctly.
- [x] `choose_report_type` supports `LEGACY_REPORT_TYPE_MAP` fallback.
- [x] `override_report_type_from_ocr` has correct override behavior.
- [x] `--report-type` forced override is supported for debugging.
- [x] Test UI shows verdict, evidence highlighting, and rule comparison.
- [x] Fixture-based tests cover statement/correspondence report-type overrides, no-name skips, visible-name extraction, model-skip override, index, and bad OCR.
