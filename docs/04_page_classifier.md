# Module 04 - page_classifier

> Decides whether a page should be extracted and, if so, what report type it is. This is the simplest LLM module in the pipeline, but it determines the downstream processing path.

## 1. Purpose

Given one page of OCR text, make a single LLM call and output:

- `should_extract: bool`: extract or skip
- `skip_reason: "index" | "record_metadata" | "bad_ocr" | null`
- `report_type: "statement" | "transport/admin" | "correspondence"`
- `evidence: str`: a quote of no more than 25 words

This is the gatekeeper for all downstream modules. A wrong extract/skip decision either misses data or wastes work.

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
  "elapsed_seconds": 3.4
}
```

## 3. Core Algorithm

Inherited from the original `model_page_decision`:

```python
def classify(ocr: str, stats: CallStats, *, report_type_override=None) -> PageDecision:
    if report_type_override:
        return PageDecision(True, None, choose_report_type(report_type_override), "override")
    schema = '{"should_extract":true,"skip_reason":null,"report_type":"statement","evidence":"..."}'
    obj = client.generate_json(render(PAGE_CLASSIFY_PROMPT, ocr=ocr), schema, stats, num_predict=500)
    decision = parse_page_decision(obj)
    # Post-regex correction: some strong signals should not be overridden by the model.
    decision.report_type = override_report_type_from_ocr(ocr, decision.report_type)
    return decision
```

`override_report_type_from_ocr` is an important fallback. If the text clearly matches patterns such as `"Statement of"`, `"repatriation"`, or `"certificate delivered"`, it forcefully corrects the model's report type. The model can occasionally label an obvious statement page as correspondence.

Load the prompt from `config/prompts/page_classify.txt`, moved from the original `PAGE_CLASSIFY_PROMPT`.

## 4. Directory Structure

```text
src/modules/page_classifier/
|-- __init__.py
|-- core.py              # classify()
|-- rules.py             # STATEMENT_REPORT_PAT / TRANSPORT_ADMIN_REPORT_PAT and similar regexes
|-- parsing.py           # parse_page_decision()
|-- blueprint.py
|-- standalone.py
|-- cli.py
|-- templates/
|   `-- ui.html
`-- tests/
    |-- test_rules.py
    |-- test_parsing.py
    `-- fixtures/
        |-- statement_page.txt
        |-- transport_page.txt
        |-- index_page.txt
        `-- bad_ocr_page.txt
```

## 5. Blueprint API

| Method | Path | Behavior |
|---|---|---|
| GET | `/classify/` | Test UI |
| GET | `/classify/docs` | List classifiable `doc_id` values with completed OCR |
| GET | `/classify/pages/<doc_id>` | List all OCR-completed pages for the document |
| POST | `/classify/run-single/<doc_id>/<page>` | Classify one page and return the result |
| POST | `/classify/run-all/<doc_id>` | Classify the whole document asynchronously |
| GET | `/classify/result/<doc_id>/<page>` | Return an existing result |

## 6. CLI

```bash
python -m modules.page_classifier.cli \
  --in_dir /data/ocr_text/myDoc \
  --out_dir /data/intermediate/myDoc \
  --model qwen2.5:14b-instruct \
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
|  report_type:     STATEMENT                                     |
|  skip_reason:     -                                             |
|  evidence:        "Statement of slave Mariam bint Yusuf..."     |
|  model_calls: 1   repair_calls: 0   elapsed: 3.4s               |
+-----------------------------------------------------------------+
|  Regex override check                                           |
|  STATEMENT pattern:       matched -> would override -> no change |
|  TRANSPORT/ADMIN pattern: not matched                            |
+-----------------------------------------------------------------+
|  OCR text with evidence highlighted                             |
|  Statement of slave Mariam bint Yusuf, aged about 20 years, ... |
+-----------------------------------------------------------------+
|  [ Raw model response ]                                         |
|  { "should_extract": true, "skip_reason": null, ... }           |
+-----------------------------------------------------------------+
```

Visualization goals:

- **Verdict badge**: different `report_type` values should use different colors, for example statement green, transport blue, and correspondence gray.
- **Evidence highlighted in source text**: use fuzzy string matching; if the evidence cannot be located, show "evidence not located in text".
- **Regex override comparison**: if rules and model output disagree, the UI should call that out clearly.
- **Raw response collapse panel**: useful for occasional JSON debugging.

## 8. Docker

`docker/ner.Dockerfile`, shared by all NER modules:

```dockerfile
FROM llm-pipeline-base:latest
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
    networks: [ llm_internal ]
    volumes:
      - ./data/ocr_text:/data/ocr_text:ro
      - ./data/intermediate:/data/intermediate
    profiles: [ "standalone", "all" ]
    command: >
      gunicorn -b 0.0.0.0:5104 -w 1 --timeout 600
      'modules.page_classifier.standalone:create_app()'
```

## 9. Tests

Unit tests:

- `parse_page_decision` behavior for valid JSON, missing fields, and invalid `report_type`.
- `override_report_type_from_ocr` with fixtures that match STATEMENT, TRANSPORT, and no pattern.

Integration tests:

- Run `classify()` on four fixtures: statement, transport, index, and bad OCR. Assert the expected categories.

## 10. Build Checklist

- [ ] Prompt is moved to a file and `render_prompt` injects it correctly.
- [ ] `choose_report_type` supports `LEGACY_REPORT_TYPE_MAP` fallback.
- [ ] `override_report_type_from_ocr` has correct override behavior.
- [ ] `--report-type` forced override is supported for debugging.
- [ ] Test UI shows verdict, evidence highlighting, and rule comparison.
- [ ] All four fixture tests pass.
