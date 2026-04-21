# Module 06 - metadata_extractor

> Extract one validated `Detailed info.csv` row per named subject on an extractable page.

Implementation status as of 2026-04-21: core extraction, YAML-backed vocab loading, strict post-parse validation, per-person upsert, CLI, standalone UI, prompt-folder loading, and unit tests are implemented. Main `web_app` mounting remains a Phase 6 integration step.

## 1. Purpose

Given one OCR page plus one already-identified subject name, produce the final detail-row fields used by `Detailed info.csv`.

| Field | Type | Allowed Values |
|---|---|---|
| Name | str | Subject name |
| Page | int | Page number |
| Report Type | enum | `statement` / `correspondence` |
| Crime Type | enum | `kidnapping` / `illegal detention` / `""` |
| Whether abuse | enum | `yes` / `no` / `""` |
| Conflict Type | enum | `manumission dispute` / `ownership dispute` / `debt dispute` / `free-status dispute` / `forced-transfer dispute` / `repatriation dispute` / `kidnapping case` / `""` |
| Trial | enum | `manumission requested` / `freedom/manumission outcome` / `repatriation arranged` / `""` |
| Amount paid | str | Literal amount text or `""` |

Key constraints:

- one model call per person
- every non-empty field needs page-local evidence
- invalid enum values are cleared
- values with missing evidence are cleared
- upstream `report_type` from `page_classifier` is context, but the final row must still land in the final `Detailed info.csv` categories

## 2. Inputs and Output

**Inputs**:

- `data/ocr_text/<doc_id>/pNNN.txt`
- `data/intermediate/<doc_id>/pNNN.classify.json`
- `data/intermediate/<doc_id>/pNNN.names.json`

The page is eligible only when:

- `pNNN.classify.json` exists and has `should_extract=true`
- `pNNN.names.json` exists and has at least one `named_people[]` item

**Output**:

- `data/intermediate/<doc_id>/pNNN.meta.json`

Stored shape:

```json
{
  "page": 12,
  "report_type": "statement",
  "classify": {
    "should_extract": true,
    "skip_reason": null,
    "report_type": "statement",
    "evidence": "Statement of slave Mariam bint Yusuf"
  },
  "names": ["Mariam bint Yusuf"],
  "people": [
    {
      "name": "Mariam bint Yusuf",
      "row": {
        "Name": "Mariam bint Yusuf",
        "Page": 12,
        "Report Type": "statement",
        "Crime Type": "kidnapping",
        "Whether abuse": "yes",
        "Conflict Type": "",
        "Trial": "manumission requested",
        "Amount paid": "",
        "_evidence": {
          "report_type": "Statement of slave Mariam bint Yusuf",
          "crime_type": "kidnapped from Zanzibar",
          "whether_abuse": "beaten severely by her owner",
          "conflict_type": "",
          "trial": "requests freedom",
          "amount_paid": ""
        }
      },
      "validation": {
        "crime_type": {
          "status": "ok",
          "message": "Crime Type is in the allowed set."
        }
      },
      "raw_values": {
        "crime_type": "kidnapping"
      },
      "rendered_prompt": "...",
      "response_json": {
        "crime_type": "kidnapping"
      },
      "model_calls": 1,
      "repair_calls": 0,
      "elapsed_seconds": 3.8
    }
  ],
  "rows": [
    {
      "Name": "Mariam bint Yusuf",
      "Page": 12,
      "Report Type": "statement",
      "Crime Type": "kidnapping",
      "Whether abuse": "yes",
      "Conflict Type": "",
      "Trial": "manumission requested",
      "Amount paid": "",
      "_evidence": {
        "report_type": "Statement of slave Mariam bint Yusuf",
        "crime_type": "kidnapped from Zanzibar",
        "whether_abuse": "beaten severely by her owner",
        "conflict_type": "",
        "trial": "requests freedom",
        "amount_paid": ""
      }
    }
  ],
  "model_calls": 1,
  "repair_calls": 0,
  "elapsed_seconds": 3.8
}
```

`aggregator` reads the `rows` array from this file directly.

## 3. Core Algorithm

```python
def extract_person(ocr_text, name, page, report_type, classify_record):
    prompt = render_prompt(
        load_prompt(),
        name=name,
        page=page,
        report_type=report_type,
        ocr=clean_ocr(ocr_text),
    )
    obj = client.generate_json(prompt, schema_hint(name, page, report_type), stats)
    parsed = parse_meta(
        obj,
        name,
        page,
        report_type,
        classify_evidence=classify_record["evidence"],
    )
    return build_person_result(parsed, prompt, obj, stats)
```

Post-parse validation rules in `parsing.py`:

- `choose_allowed(value, allowed)` does case-insensitive matching against the YAML allowlist.
- `choose_yes_no_blank(value)` normalizes abuse to `yes`, `no`, or `""`.
- any non-empty enum or amount without evidence becomes `""`.
- `amount_paid` keeps literal text only; `"null"` and `"none"` collapse to `""`.
- invalid model `report_type` values do not survive; the extractor falls back to the page-classifier context instead.

Whole-page behavior in `core.py`:

- `run_page_file(...)` extracts every person from `pNNN.names.json`
- `run_page_file(..., person_name="...")` re-extracts just one named person and upserts that record into the existing `pNNN.meta.json`
- `run_folder(...)` processes only pages that have OCR text, `should_extract=true`, and non-empty names

## 4. Prompt and Vocab Layout

Prompt files live under:

```text
config/prompts/metadata_extractor/
|-- meta_pass.txt
`-- category_guide.txt
```

Rules:

- `meta_pass.txt` is the extraction prompt template
- `category_guide.txt` is appended at runtime so category explanations stay close to the prompt during debugging
- prompts are loaded through `shared.prompt_loader`

Allowlists live under:

```text
config/schemas/vocab.yaml
```

`src/modules/metadata_extractor/vocab.py` reads the final metadata categories from YAML so the output categories stay centralized.

## 5. Directory Structure

```text
src/modules/metadata_extractor/
|-- __init__.py
|-- blueprint.py
|-- cli.py
|-- core.py
|-- parsing.py
|-- standalone.py
|-- vocab.py
|-- static/
|   `-- metadata_extractor.css
|-- templates/
|   `-- ui.html
`-- tests/
    |-- test_core.py
    |-- test_parsing.py
    `-- fixtures/
        |-- kidnapping_abuse.txt
        |-- repatriation.txt
        `-- certificate_grant.txt
```

## 6. Blueprint API

| Method | Path | Behavior |
|---|---|---|
| GET | `/meta/` | Standalone UI |
| GET | `/meta/docs` | Docs that already have extractable pages with names |
| GET | `/meta/pages/<doc_id>` | Extractable pages with names for one document |
| GET | `/meta/people/<doc_id>/<page>` | Named people available on one page |
| GET | `/meta/result/<doc_id>/<page>?name=...` | Current saved page payload for the UI |
| POST | `/meta/run-single/<doc_id>/<page>/<name>` | Extract or re-extract one named person |
| POST | `/meta/run-page/<doc_id>/<page>` | Extract all named people on the page |
| POST | `/meta/run-all/<doc_id>` | Run all eligible pages in the document asynchronously |
| GET | `/meta/jobs/<job_id>` | Poll background whole-doc status |

UI discovery behavior:

- the document selector is a dropdown, not a freeform input
- docs are discovered from `data/ocr_text/<doc_id>/`
- a page appears only when classifier kept it and names already exist

## 7. CLI

Whole eligible document:

```bash
python -m modules.metadata_extractor.cli \
  --in_dir /data/ocr_text/myDoc \
  --inter_dir /data/intermediate/myDoc \
  --out_dir /data/intermediate/myDoc \
  --model qwen2.5:14b-instruct
```

One page:

```bash
python -m modules.metadata_extractor.cli \
  --in_dir /data/ocr_text/myDoc \
  --inter_dir /data/intermediate/myDoc \
  --out_dir /data/intermediate/myDoc \
  --page 12
```

One named person on one page:

```bash
python -m modules.metadata_extractor.cli \
  --in_dir /data/ocr_text/myDoc \
  --inter_dir /data/intermediate/myDoc \
  --out_dir /data/intermediate/myDoc \
  --page 12 \
  --name "Mariam bint Yusuf"
```

## 8. Standalone UI

Open:

```text
http://127.0.0.1:5106/meta/
```

The UI shows:

- document/page/person selectors
- page summary metrics
- current `rows` table for the page
- OCR text with multi-color evidence highlighting
- one card per metadata field for the selected person
- validation table with clear `ok`, `empty`, `cleared_invalid`, `cleared_missing_evidence`, or `inherited` statuses
- rendered prompt and parsed response JSON

This is designed as a prompt-debug and review surface, not just a run button.

## 9. Docker

Uses shared `docker/ner.Dockerfile`.

Compose service:

- service: `metadata_extractor`
- profile: `meta`
- port: `127.0.0.1:5106`
- route: `/meta/`

Run it:

```bash
docker compose --profile meta up -d --build metadata_extractor
```

Health check:

```bash
curl http://127.0.0.1:5106/healthz
```

Expected:

```json
{"module":"metadata_extractor","status":"ok"}
```

## 10. Tests

Current test commands:

```bash
docker build -f docker/ner.Dockerfile -t manumission-ner:phase4_3 .
docker run --rm manumission-ner:phase4_3 python -m unittest discover -s /app/modules/metadata_extractor/tests -p "test_*.py"
```

Current coverage:

- `choose_allowed` respects YAML allowlists case-insensitively
- `choose_yes_no_blank` normalizes abuse flags
- `parse_meta` clears invalid values and missing-evidence values safely
- `run_page_file()` extracts all names on a page
- single-person reruns upsert into an existing `pNNN.meta.json`
- `run_folder()` skips pages without names

## 11. Build Checklist

- [x] Prompt is loaded from `config/prompts/metadata_extractor/`.
- [x] Human-readable category explanations live beside the prompt for debugging.
- [x] Final allowlists come from `config/schemas/vocab.yaml`.
- [x] Invalid enum values are cleared after parsing.
- [x] Non-empty values without evidence are cleared after parsing.
- [x] UI shows field cards, validation, prompt, and parsed response.
- [x] UI highlights evidence spans inside OCR text.
- [x] Single-person reruns do not drop previously extracted people on the page.
- [x] Unit tests cover parsing and core extraction flow.
