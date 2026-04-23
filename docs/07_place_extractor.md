# Module 07 - place_extractor

> Extract page-local place rows for one named subject, keep route steps separate from background mentions, and preserve the full decision trail from candidate discovery through reconciliation.

Implementation status as of 2026-04-23: prompt-backed extraction, merged candidate stage, verifier retry, date enrichment, rule reconciliation, CLI, standalone UI, direct CSV download, in-UI clear-results action, Compose service, and unit tests are implemented. Main `web_app` mounting remains a Phase 6 integration step.

## 1. Purpose

Given one OCR page plus one already-identified subject name, produce the final rows used by `name place.csv`.

Each final row includes:

| Field | Type | Meaning |
|---|---|---|
| Name | str | Subject name |
| Page | int | Page number |
| Place | str | Normalized place text |
| Order | int | `1..n` route step, or `0` for background-only mention |
| Arrival Date | str | ISO `YYYY-MM-DD` or `""` |
| Date Confidence | enum | `explicit` / `derived_from_doc` / `unknown` / `""` |
| Time Info | str | Literal non-ISO timing text or `""` |
| `_evidence` | str | Page-local supporting quote, max 25 words |

Key constraints:

- use only this page and only the target person
- keep ship names and generic office words out of final places
- preserve relevant background mentions as `order=0` instead of discarding them
- if verifier adjudication fails, fall back to candidate rows rather than dropping all place information

## 2. Inputs and Output

**Inputs**:

- `data/ocr_text/<doc_id>/pNNN.txt`
- `data/intermediate/<doc_id>/pNNN.classify.json`
- `data/intermediate/<doc_id>/pNNN.names.json`

The page is eligible only when:

- `pNNN.classify.json` exists and has `should_extract=true`
- `pNNN.names.json` exists and has at least one `named_people[]` item

**Output**:

- `data/intermediate/<doc_id>/pNNN.places.json`

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
      "rows": [
        {
          "Name": "Mariam bint Yusuf",
          "Page": 12,
          "Place": "Bushehr",
          "Order": 1,
          "Arrival Date": "",
          "Date Confidence": "",
          "Time Info": "",
          "_evidence": "from - forwarded by the Political Agency, Bushire."
        },
        {
          "Name": "Mariam bint Yusuf",
          "Page": 12,
          "Place": "Dubai",
          "Order": 2,
          "Arrival Date": "1931-05-17",
          "Date Confidence": "explicit",
          "Time Info": "17th May 1931",
          "_evidence": "arriving Dubai about the 17th May 1931"
        }
      ],
      "passes": {
        "candidate": {
          "rows": [...],
          "runs": [
            {"stage": "pass1", "prompt_name": "place_pass.txt", "...": "..."},
            {"stage": "recall", "prompt_name": "place_recall.txt", "...": "..."}
          ]
        },
        "verified": {
          "rows": [...],
          "attempts": [
            {"stage": "verify", "prompt_name": "place_verify.txt", "...": "..."}
          ],
          "issue": "",
          "fallback_applied": false,
          "fallback_reason": ""
        },
        "date_enrich": {
          "rows": [...],
          "prompt_name": "place_date_enrich.txt"
        },
        "reconciled": {
          "rows": [...]
        }
      },
      "validation": [
        {"rule": "Positive orders", "status": "ok", "message": "Positive orders form 1..n consecutively."}
      ],
      "model_calls": 4,
      "repair_calls": 0,
      "elapsed_seconds": 7.8
    }
  ],
  "rows": [
    {
      "Name": "Mariam bint Yusuf",
      "Page": 12,
      "Place": "Bushehr",
      "Order": 1,
      "Arrival Date": "",
      "Date Confidence": "",
      "Time Info": "",
      "_evidence": "from - forwarded by the Political Agency, Bushire."
    }
  ],
  "model_calls": 4,
  "repair_calls": 0,
  "elapsed_seconds": 7.8
}
```

`aggregator` can read either the top-level `rows` array or the per-person `people[].rows` arrays from this file.

## 3. Core Algorithm

Implemented extraction flow in `core.py`:

```text
pass1 candidate discovery
  + recall discovery
  -> merged candidate rows
  -> verify (up to 2 attempts if route validation fails)
  -> date enrichment
  -> rule reconciliation
  -> final validation summary
```

Key post-processing rules:

- `parsing.py` normalizes place text through `08 normalizer`, filters invalid place-like text, coerces route order to integers, and converts date strings to ISO where possible.
- `validation.py` checks consecutive positive orders, duplicate places, date-confidence consistency, ascending dated route rows, and generic invalid place text.
- `reconcile.py` ports the old transport/forwarding heuristics, including `infer_forwarding_transport_rows`, route-promotion rules, and final order reassignment.
- `core.py` upserts single-person reruns into existing page JSON and preserves previously extracted people on the same page.

## 4. Prompt Layout

Prompt files live under:

```text
config/prompts/place_extractor/
|-- place_pass.txt
|-- place_recall.txt
|-- place_verify.txt
`-- place_date_enrich.txt
```

Rules:

- `place_pass.txt` is the first high-recall candidate pass
- `place_recall.txt` is the second discovery pass for route or date signals often missed
- `place_verify.txt` does final adjudication and route ordering
- `place_date_enrich.txt` can improve only date-related fields while keeping the same place list

Prompts are loaded through `shared.prompt_loader`.

## 5. Directory Structure

```text
src/modules/place_extractor/
|-- __init__.py
|-- blueprint.py
|-- cli.py
|-- core.py
|-- parsing.py
|-- passes.py
|-- reconcile.py
|-- standalone.py
|-- validation.py
|-- static/
|   `-- place_extractor.css
|-- templates/
|   `-- ui.html
`-- tests/
    |-- fixtures/
    |   |-- ambiguous.txt
    |   |-- multi_route.txt
    |   `-- single_place.txt
    |-- test_blueprint.py
    |-- test_core.py
    |-- test_parsing.py
    |-- test_reconcile.py
    `-- test_validation.py
```

## 6. Blueprint API

| Method | Path | Behavior |
|---|---|---|
| GET | `/places/` | Standalone UI |
| GET | `/places/docs` | Docs that already have extractable pages with names |
| GET | `/places/pages/<doc_id>` | Extractable pages with names for one document |
| GET | `/places/people/<doc_id>/<page>` | Named people available on one page |
| GET | `/places/result/<doc_id>/<page>?name=...` | Current saved page payload for the UI |
| GET | `/places/download/<doc_id>/<page>.csv?name=...` | Download the current page CSV, or only the selected person's rows when `name=` is provided |
| POST | `/places/clear-all/<doc_id>` | Delete every saved `pNNN.places.json` result for the current document |
| POST | `/places/run-single/<doc_id>/<page>/<name>` | Extract or re-extract one named person |
| POST | `/places/run-page/<doc_id>/<page>` | Extract all named people on the page |
| POST | `/places/run-all/<doc_id>` | Run all eligible pages in the document asynchronously |
| GET | `/places/jobs/<job_id>` | Poll background whole-doc status |

UI discovery behavior matches `metadata_extractor`:

- the document selector is a dropdown, not a freeform input
- docs are discovered from `data/ocr_text/<doc_id>/`
- a page appears only when classifier kept it and names already exist

## 7. CLI

Whole eligible document:

```bash
python -m modules.place_extractor.cli \
  --in_dir /data/ocr_text/myDoc \
  --inter_dir /data/intermediate/myDoc \
  --out_dir /data/intermediate/myDoc \
  --model qwen2.5:14b-instruct
```

One page:

```bash
python -m modules.place_extractor.cli \
  --in_dir /data/ocr_text/myDoc \
  --inter_dir /data/intermediate/myDoc \
  --out_dir /data/intermediate/myDoc \
  --page 12
```

One named person on one page:

```bash
python -m modules.place_extractor.cli \
  --in_dir /data/ocr_text/myDoc \
  --inter_dir /data/intermediate/myDoc \
  --out_dir /data/intermediate/myDoc \
  --page 12 \
  --name "Mariam bint Yusuf"
```

## 8. Standalone UI

Open:

```text
http://127.0.0.1:5107/places/
```

The UI shows:

- document/page/person selectors
- direct `Download Page CSV` and `Download Person CSV` actions for the current view
- a `Clear All Results` button that removes all saved `pNNN.places.json` outputs for the selected document after confirmation
- page summary metrics
- current page-level rows table
- route cards for positive-order steps plus a separate background block for `order=0`
- OCR text with evidence highlighting using `explicit`, `derived_from_doc`, and `unknown` color classes
- stage tabs for candidates, verified, date-enriched, and reconciled rows
- validation table
- prompt/response debug panels for every stored model run

This is designed as a route-debug and prompt-debug surface, not just a run button.

## 9. Docker

Uses shared `docker/ner.Dockerfile`.

Compose service:

- service: `place_extractor`
- profile: `places`
- port: `127.0.0.1:5107`
- route: `/places/`

Run it:

```bash
docker compose --profile places up -d --build place_extractor
```

Health check:

```bash
curl http://127.0.0.1:5107/healthz
```

Expected:

```json
{"module":"place_extractor","status":"ok"}
```

## 10. Tests

Current test commands:

```bash
docker build -f docker/ner.Dockerfile -t manumission-ner:phase4_4 .
docker run --rm manumission-ner:phase4_4 python -m unittest discover -s /app/modules/place_extractor/tests -p "test_*.py"
```

Current coverage:

- candidate parsing filters ship names and generic invalid place text
- date parsing derives ISO dates from document-year context when needed
- verifier validation catches route-shape and date-consistency failures
- forwarding heuristics recover source/destination rows from administrative wording
- page extraction handles all names on a page and upserts single-person reruns
- blueprint download routes export page-level and person-level CSVs without debug-only fields
- whole-folder run skips pages without names

## 11. Build Checklist

- [x] Prompts are loaded from `config/prompts/place_extractor/`.
- [x] Candidate discovery uses two prompt-backed passes.
- [x] Verifier retries once when route validation fails.
- [x] Date enrichment runs after verification.
- [x] Reconciliation ports the old forwarding and route-promotion heuristics.
- [x] UI shows route cards, stage tabs, evidence highlighting, validation, and CSV download actions.
- [x] Single-person reruns do not drop previously extracted people on the page.
- [x] Unit tests cover parsing, reconciliation, validation, page-level extraction flow, and CSV download routes.
