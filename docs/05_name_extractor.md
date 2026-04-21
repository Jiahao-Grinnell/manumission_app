# Module 05 - name_extractor

> Extract all named enslaved or manumission subjects from one OCR page, keep the full five-stage decision trail, and explain every dropped candidate.

## 1. Purpose

Given one page of OCR text plus its `page_classifier` decision, produce the list of named people who are themselves the subject group on that page.

Subject definition:

- Include: enslaved person, refugee slave, fugitive slave, manumission applicant, certificate recipient, recommended subject, and clearly named family members included in that same subject group.
- Exclude: owner, buyer, seller, master, sheikh, captain, clerk, witness, correspondent, signatory, and free person.

This boundary is context-sensitive, so the module uses multiple LLM stages plus a final Python rule filter.

## 2. Input / Output

**Input**

- `data/ocr_text/<doc_id>/pNNN.txt`
- `data/intermediate/<doc_id>/pNNN.classify.json`
- Only runs when `should_extract=true`

Discovery rules for the standalone UI:

- the document dropdown is populated from `data/ocr_text/<doc_id>/`
- a document is shown only if at least one page also has `data/intermediate/<doc_id>/pNNN.classify.json` with `should_extract=true`
- pages with missing classifier output or `should_extract=false` are hidden from the `name_extractor` UI
- if a document is missing from the UI, run `page_classifier` on the whole document first

**Output**

- `data/intermediate/<doc_id>/pNNN.names.json`

Stored shape:

```json
{
  "page": 12,
  "report_type": "statement",
  "classify": {
    "should_extract": true,
    "report_type": "statement",
    "evidence": "Statement of slave Mariam bint Yusuf"
  },
  "named_people": [
    {"name": "Ahmad bin Said", "evidence": "Ahmad bin Said requests repatriation"},
    {"name": "Mariam bint Yusuf", "evidence": "Statement of slave Mariam bint Yusuf"}
  ],
  "passes": {
    "pass1": {
      "label": "Pass 1 raw",
      "prompt_name": "name_pass.txt",
      "input_candidates": [],
      "candidates": [...]
    },
    "pass1_filter": {
      "label": "Pass 1 filter",
      "input_candidates": [...],
      "llm_candidates": [...],
      "candidates": [...],
      "fallback_applied": false,
      "removed": [...]
    },
    "recall": {...},
    "recall_filter": {...},
    "merged": {...},
    "verify": {...},
    "rule_filter": {
      "candidates": [...],
      "removed": [...],
      "kept_reasons": [...]
    }
  },
  "removed_candidates": [
    {
      "name": "Sheikh Rashid",
      "stage": "rule_filter",
      "reason_type": "negative_rule",
      "reason": "matched \"sold to {name}\""
    }
  ],
  "final_reasons": [
    {
      "name": "Mariam bint Yusuf",
      "stage": "rule_filter",
      "reason_type": "positive_rule",
      "reason": "matched \"statement of {name}\""
    }
  ],
  "model_calls": 5,
  "repair_calls": 0,
  "elapsed_seconds": 18.7
}
```

The debugging requirement is intentional: every stage is persisted, and every removal is attributable.

## 3. Prompt Files

Prompt text is no longer embedded in Python constants. The module loads from:

```text
config/prompts/name_extractor/
|-- name_pass.txt
|-- name_recall.txt
|-- name_filter.txt
`-- name_verify.txt
```

This keeps prompt tuning separate from code changes and makes `rerun-pass` debugging easier.

## 4. Core Pipeline

Implemented pipeline:

```text
pass1 raw
  -> pass1_filter
recall raw
  -> recall_filter
merge(pass1_filter, recall_filter)
  -> verify
  -> rule_filter
```

Important details:

- Baseline cost is five LLM calls: `pass1`, `pass1_filter`, `recall`, `recall_filter`, `verify`.
- `merged` and `rule_filter` are pure Python.
- JSON repair retries may add more model calls.
- The filter prompt is reused twice, once for `pass1_filter` and once for `recall_filter`.
- If a filter or verify stage returns an empty list, the module transparently falls back to the upstream candidates and records that fallback in the stored stage payload.

## 5. Rule Layer

The final rule layer exists so the module can explain late removals instead of only returning a black-box final list.

Implemented checks:

- `ROLE_POSITIVE_PATTERNS`: `statement of {name}`, `slave {name}`, `refugee slaves ... {name}`, `grant certificate ... to {name}`, `{name} requests repatriation`
- `ROLE_NEGATIVE_PATTERNS`: `sold to {name}`, `bought by {name}`, `belonging to {name}`, `statement recorded by {name}`, `letter from {name}`
- official-title context detection
- `free born` plus `not a slave`
- basic name validation using the shared normalizer

The merge step imports shared logic from `modules.normalizer.names` so name comparison heuristics stay consistent across modules.

## 6. Rerun Semantics

`POST /names/rerun-pass/<doc_id>/<page>/<pass_name>`

Allowed `pass_name` values:

- `pass1`
- `pass1_filter`
- `recall`
- `recall_filter`
- `verify`

Semantics:

- rerun the requested stage
- reuse unaffected upstream stages from the stored `pNNN.names.json`
- recompute every downstream stage
- overwrite the existing `pNNN.names.json`

Examples:

- rerun `verify`: reuse `pass1_filter`, `recall_filter`, and `merged`; recompute `verify` and `rule_filter`
- rerun `recall_filter`: reuse `pass1` and `pass1_filter`; recompute `recall_filter`, `merged`, `verify`, and `rule_filter`

## 7. Directory Structure

```text
src/modules/name_extractor/
|-- __init__.py
|-- blueprint.py
|-- cli.py
|-- core.py
|-- merging.py
|-- passes.py
|-- rules.py
|-- standalone.py
|-- static/
|   `-- name_extractor.css
|-- templates/
|   `-- ui.html
`-- tests/
    |-- fixtures/
    |   |-- freeborn_page.txt
    |   |-- grouped_list.txt
    |   |-- owner_vs_slave.txt
    |   `-- single_subject.txt
    |-- test_core.py
    `-- test_rules.py
```

## 8. Blueprint API

| Method | Path | Behavior |
|---|---|---|
| GET | `/names/` | Standalone UI |
| GET | `/names/docs` | Docs with at least one extractable page |
| GET | `/names/pages/<doc_id>` | Extractable pages only |
| POST | `/names/run-single/<doc_id>/<page>` | Run one page |
| POST | `/names/run-all/<doc_id>` | Whole-document background job |
| GET | `/names/result/<doc_id>/<page>` | Existing result payload |
| POST | `/names/rerun-pass/<doc_id>/<page>/<pass_name>` | Rerun one stage and downstream |
| GET | `/names/jobs/<job_id>` | Poll background job |

## 9. CLI

Whole document:

```bash
python -m modules.name_extractor.cli \
  --in_dir /data/ocr_text/myDoc \
  --classify_dir /data/intermediate/myDoc \
  --out_dir /data/intermediate/myDoc \
  --model qwen2.5:14b-instruct
```

Single page:

```bash
python -m modules.name_extractor.cli \
  --in_dir /data/ocr_text/myDoc \
  --classify_dir /data/intermediate/myDoc \
  --out_dir /data/intermediate/myDoc \
  --page 12
```

Rerun one stage on one page:

```bash
python -m modules.name_extractor.cli \
  --in_dir /data/ocr_text/myDoc \
  --classify_dir /data/intermediate/myDoc \
  --out_dir /data/intermediate/myDoc \
  --page 12 \
  --rerun-pass verify
```

## 10. UI

The standalone UI is meant for prompt/debug work, not just final preview.

Implemented UI features:

- document/page selector restricted to extractable pages
- document list discovered from `data/ocr_text/` and filtered by classifier output, not entered manually
- full OCR text with final names highlighted separately from dropped names
- summary metrics for final names, dropped names, model calls, repair calls, elapsed time, and classifier evidence
- one card per stage showing input/output counts, removed counts, fallback notes, parsed response JSON, and rendered prompt text
- final table of kept names and evidence
- dropped-candidate table with stage, reason, and excerpt
- rerun-stage control

URL:

```text
http://127.0.0.1:5105/names/
```

If a known OCR document does not appear in the dropdown, check these files first:

```text
data/ocr_text/<doc_id>/pNNN.txt
data/intermediate/<doc_id>/pNNN.classify.json
```

The most common cause is that the classifier has only written metadata/index skips so far, which means there are still zero extractable pages for `name_extractor` to show.

## 11. Docker

Uses the shared `docker/ner.Dockerfile` and is exposed through `compose.yaml` as:

- service: `name_extractor`
- profile: `names`
- port: `127.0.0.1:5105`

## 12. Tests

Deterministic unit and mocked-integration tests:

- positive subject rule detection
- negative-role rule detection
- `free born / not a slave` removal
- model-stage removal tracking
- rule-stage removal tracking
- `rerun-pass` reusing upstream stored stages
- whole-folder run limited to `should_extract=true` pages

Run:

```bash
docker build -f docker/ner.Dockerfile -t manumission-ner:phase4_2 .
docker run --rm manumission-ner:phase4_2 python -m unittest discover -s /app/modules/name_extractor/tests -p "test_*.py"
docker compose --profile names up -d --build name_extractor
curl http://127.0.0.1:5105/healthz
```

## 13. Performance

Expect a slower first request when the text model is not already loaded in Ollama.

Typical per-page behavior:

- first call can spend noticeable time loading the model
- warm calls are faster while the model stays resident
- total stage cost is still dominated by the five LLM calls

The current implementation optimizes for debuggability and determinism, not minimum latency.
