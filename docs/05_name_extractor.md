# Module 05 - name_extractor

> Extract current-page enslaved/manumission subject names with OCR-grounded candidates, context-bundled role labeling, deterministic scoring, and a full audit trail.

## 1. Purpose

Given one OCR page plus its `page_classifier` result, produce the list of people or anchored relation labels who are themselves the subject group on that page.

Subject definition:

- Include: enslaved person, refugee slave, fugitive slave, manumission applicant, certificate recipient, kidnapped/recovered/repatriated victim, slave-status investigation subject, and anchored relation-labeled subjects such as `Salem's sister` when that relation person is the subject.
- Exclude: owner, buyer, seller, master, broker, trafficker, kidnapper, witness, official, correspondent, signatory, papers source, freeborn-not-slave person, and unanchored generic labels.

The module treats each page independently. It does not inherit names from previous or following pages.

## 2. Input / Output

**Input**

- `data/ocr_text/<doc_id>/pNNN.txt`
- `data/intermediate/<doc_id>/pNNN.classify.json`
- Only pages with `should_extract=true` are processed.

**Output**

- `data/intermediate/<doc_id>/pNNN.names.json`

The public output shape remains compatible with downstream modules:

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
    {"name": "Mariam bint Yusuf", "evidence": "Statement of slave Mariam bint Yusuf"}
  ],
  "passes": {
    "candidate_mining": {"candidates": []},
    "span_gate": {"candidates": [], "removed": []},
    "context_bundle": {"candidates": []},
    "role_label": {"candidates": []},
    "decision": {"candidates": [], "removed": []},
    "review_queue": {"candidates": []}
  },
  "removed_candidates": [],
  "final_reasons": [],
  "model_calls": 2,
  "repair_calls": 0,
  "elapsed_seconds": 4.2
}
```

The `named_people[]` contract is unchanged for `metadata_extractor`, `place_extractor`, `aggregator`, and `orchestrator`.

## 3. Core Pipeline

Implemented V2.1 pipeline:

```text
OCR + classify.json
  -> segments
  -> mention_scan
  -> candidate_mining
  -> span_gate
  -> merge
  -> context_bundle
  -> role_label
  -> escalation
  -> deterministic_signals
  -> decision
  -> review_queue
```

Key behavior:

- `candidate_mining` unions deterministic OCR patterns with a recall-only Mistral mention scan.
- `span_gate` requires every final candidate to be locatable in the current OCR page.
- LLM-provided quotes and role evidence must validate against OCR/context text; model evidence cannot prove a name exists.
- `context_bundle` preserves governing same-page context, such as subject-list introductions, numbered case segments, nearby paragraph context, and relation-label context.
- `role_label` asks Mistral to classify fixed candidate IDs into fixed role enums. It cannot add new names.
- `decision` combines deterministic signals and model role labels into a scored keep/drop/review decision.
- Ambiguous candidates stay in `passes.review_queue` and are excluded from final CSVs.

Legacy stage names such as `pass1`, `recall`, and `verify` may still be accepted as rerun aliases, but the default stored pipeline is V2.1.

## 4. Prompt Files

Current V2 prompts live under:

```text
config/prompts/name_extractor/v2/
|-- mention_scan.txt
|-- role_label.txt
`-- role_escalate.txt
```

Older prompt files remain in `config/prompts/name_extractor/` for compatibility and comparison, but the default implementation uses the V2 prompt folder.

## 5. Candidate And Context Rules

Candidate sources include:

- direct subject patterns such as `slave named X`, `negro by name X`, `Statement of X`, `Name. X`, and `certain X negro`
- subject lists such as `slaves whose names are` and `following refugee slaves`
- numbered memorandum case names
- anchored relation labels such as `Salem's sister`, `son of Salem`, and `wife of Abdulla`

Generic unanchored phrases are rejected:

- `the slave`
- `his sister`
- `three slaves`
- `a number of slaves`
- `three females with two children`

Context bundles prevent false negatives where the name itself is far from the governing subject signal. Examples:

- names listed below `slaves whose names are`
- victims in numbered memorandum case segments
- relation subjects whose sale/kidnapping context appears in the next sentence
- certificate-delivery lines where the action appears above the names

## 6. Decision Layer

The final decision is deterministic and audit-friendly.

Positive signals include:

- clear model subject role with validated evidence
- strong deterministic subject regex
- validated subject-list context
- validated numbered-case subject context
- direct relation-subject context

Negative signals include:

- buyer, seller, owner, master, broker, witness, official, correspondent, signatory
- `papers from X`
- `letter from X`
- `statement recorded by X`
- freeborn/not-slave evidence
- invalid model evidence quote

Default thresholds:

```text
score >= 2.5  -> keep
score <= -1.0 -> drop
otherwise     -> review_queue
```

Hard negative plus strong positive defaults to review instead of final output. The final CSV prioritizes precision.

## 7. Rerun Semantics

`POST /names/rerun-pass/<doc_id>/<page>/<pass_name>`

Primary V2 stage names:

- `mention_scan`
- `candidate_mining`
- `span_gate`
- `merge`
- `context_bundle`
- `role_label`
- `escalation`
- `decision`

Legacy aliases still accepted:

- `pass1`
- `pass1_filter`
- `recall`
- `recall_filter`
- `verify`

The current implementation recomputes the V2 page pipeline while preserving the same endpoint and stored `pNNN.names.json` shape.

## 8. UI

The standalone UI is for prompt/debug review, not just final preview.

Implemented UI features:

- document/page selector restricted to classifier-kept pages
- full OCR text with final and dropped names highlighted separately
- final names and dropped-candidate tables
- stage cards for V2 pipeline artifacts
- prompt and parsed response inspection for model stages
- decision score and signal trail in stage payloads
- rerun-stage control

The initial page payload is stored in a JSON script tag and parsed by JavaScript, avoiding direct Jinja JSON interpolation inside a JS assignment.

URL:

```text
http://127.0.0.1:5105/names/
```

## 9. CLI

Whole document:

```bash
python -m modules.name_extractor.cli \
  --in_dir /data/ocr_text/myDoc \
  --classify_dir /data/intermediate/myDoc \
  --out_dir /data/intermediate/myDoc \
  --model mistral-small3.1:latest
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
  --rerun-pass role_label
```

## 10. Docker

Uses the shared `docker/ner.Dockerfile` and is exposed through `compose.yaml` as:

- service: `name_extractor`
- profile: `names`
- port: `127.0.0.1:5105`

## 11. Orchestrator Compatibility

The orchestrator names stage imports `modules.name_extractor.core.run_folder` directly through `src/orchestrator/router.py`.

That means:

- In in-process orchestrator mode, the names stage uses the updated name extractor code after the orchestrator image/container is rebuilt or restarted.
- Existing `pNNN.names.json` artifacts are still reused when `resume=true`; rerun or clear stale names artifacts if you want V2.1 outputs for already-completed pages.
- Downstream metadata, places, and aggregation continue to read the same `named_people[]` shape.

## 12. Tests

Current regression coverage includes:

- candidate span validation
- subject-list governing context
- relation-label expanded same-page context
- invalid model role evidence handling
- owner/buyer/witness removal
- generic phrase rejection
- full-input regression fixtures
- downstream compatibility with normalizer, metadata, and place extractor tests

Run:

```bash
docker build -f docker/ner.Dockerfile -t manumission-ner:local .
docker run --rm manumission-ner:local python -m unittest discover -s /app/modules/name_extractor/tests -p "test_*.py"
docker run --rm manumission-ner:local python -m unittest modules.name_extractor.tests.test_rules modules.name_extractor.tests.test_core modules.name_extractor.tests.test_v2 modules.normalizer.tests.test_names modules.metadata_extractor.tests.test_core modules.place_extractor.tests.test_core
```
