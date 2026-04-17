# Module 05 - name_extractor

> Extract all **enslaved or manumitted named subjects** from one page of OCR text. This is the most complex LLM module in the pipeline: four LLM passes plus rule filtering.

## 1. Purpose

Given one page of OCR text, produce the list of "subject names" mentioned on that page. Each subject includes evidence.

Strict definition of "subject", inherited from the original code:

- Include: the enslaved person, refugee slave, fugitive slave, manumission applicant, certificate recipient, and family members clearly included in the subject group.
- Exclude: owner, buyer, seller, sheikh, captain, clerk, signatory, and free person.

This distinction is **highly context-dependent**. A single model judgment is unreliable, so this module uses four passes plus rule-based fallback filtering.

## 2. Input / Output

**Input**:

- `data/ocr_text/<doc_id>/p<N>.txt`
- `data/intermediate/<doc_id>/p<N>.classify.json`, only for pages where `should_extract=true`

**Output**: `data/intermediate/<doc_id>/p<N>.names.json`

```json
{
  "page": 12,
  "named_people": [
    {"name": "Mariam bint Yusuf", "evidence": "Statement of slave Mariam bint Yusuf..."},
    {"name": "Ahmad bin Said", "evidence": "refugee slave Ahmad bin Said requests repatriation"}
  ],
  "passes": {
    "pass1_raw": [...],
    "pass1_filtered": [...],
    "recall_raw": [...],
    "recall_filtered": [...],
    "merged": [...],
    "verified": [...],
    "rule_filtered": [...]
  },
  "model_calls": 6,
  "repair_calls": 1,
  "elapsed_seconds": 22.3
}
```

Keeping every intermediate pass is a key design decision. The UI needs them, and debugging extraction quality depends on them.

## 3. Core Algorithm (Inherited From `model_named_people`)

```text
+-- pass 1: NAME_PASS_PROMPT ----------+  High-precision subject extraction
|                                      |
|   LLM -> raw candidates              |
|   model_filter -> pass1_filtered     |  NAME_FILTER_PROMPT keeps true subjects
|                                      |
+-- pass 2: NAME_RECALL_PROMPT --------+  High-recall recovery of missed names
|                                      |
|   LLM -> raw candidates              |
|   model_filter -> recall_filtered    |
|                                      |
+-- merge(pass1_filtered, recall_f) ---+  Fuzzy merge same person, different spelling
|         -> merged                    |
|                                      |
+-- model_verify(merged) --------------+  NAME_VERIFY_PROMPT final decision
|         -> verified                  |
|                                      |
+-- rule filter_named_people(ocr) -----+  Regex removes ROLE_NEGATIVE_PATTERNS
          -> final                       Regex keeps ROLE_POSITIVE_PATTERNS
```

Each step is an LLM call. There are five baseline calls: pass1, recall, two filters, and verify. JSON repair retries can raise that to six or seven calls.

Rule filtering layer, pure Python with no LLM cost:

- `ROLE_NEGATIVE_PATTERNS`: remove names matched in contexts like `"sold to {name}"`, `"bought by {name}"`, or `"master {name}"`.
- `ROLE_POSITIVE_PATTERNS`: keep names matched in contexts like `"slave {name}"`, `"refugee slaves ... {name}"`, or `"statement of {name}"`.
- `is_freeborn_not_slave_name`: remove names where the context contains `"free born"` plus `"not a slave"`.
- Name validity rules: `is_valid_name`, requiring length >= 2, letters, and not in `NAME_STOPWORDS`.

## 4. Directory Structure

```text
src/modules/name_extractor/
|-- __init__.py
|-- core.py                # Orchestrates the four passes plus rules
|-- passes.py              # pass1 / recall / filter / verify wrappers
|-- rules.py               # Positive/negative patterns and NAME_STOPWORDS
|-- merging.py             # names_maybe_same_person / merge_named_people / choose_preferred_name
|-- blueprint.py
|-- standalone.py
|-- cli.py
|-- templates/
|   `-- ui.html
`-- tests/
    |-- test_rules.py
    |-- test_merging.py
    `-- fixtures/
        |-- single_subject.txt
        |-- grouped_list.txt
        |-- owner_vs_slave.txt    # Tests that negative patterns exclude owners
        `-- freeborn_page.txt
```

Note: `merge_named_people` and `names_maybe_same_person` belong to normalization. Their source code should live in `08 normalizer`, and this module should import them to avoid duplication.

## 5. Blueprint API

| Method | Path | Behavior |
|---|---|---|
| GET | `/names/` | Test UI |
| GET | `/names/pages/<doc_id>` | Extractable pages where `should_extract=true` |
| POST | `/names/run-single/<doc_id>/<page>` | Extract one page and return all intermediate artifacts |
| POST | `/names/run-all/<doc_id>` | Run the whole document asynchronously |
| GET | `/names/result/<doc_id>/<page>` | Existing result, including passes |
| POST | `/names/rerun-pass/<doc_id>/<page>/<pass_name>` | Rerun only one pass for prompt tuning |

`rerun-pass` lets you tune one prompt without rerunning the entire chain, which is very useful during debugging.

## 6. CLI

```bash
python -m modules.name_extractor.cli \
  --in_dir /data/ocr_text/myDoc \
  --classify_dir /data/intermediate/myDoc \
  --out_dir /data/intermediate/myDoc \
  --model qwen2.5:14b-instruct
```

## 7. Test UI Design

This is the largest visualization in the project:

```text
+--------------------------------------------------------------------+
| Doc: [ myDoc ]   Page: [ p012 ]   [ Run ]   [ Re-run verify ]      |
+--------------------------------------------------------------------+
|  Stages: click any stage to expand raw prompt and response          |
|  [Pass 1 raw: 5] -> [Pass 1 filtered: 4]                            |
|  [Recall raw: 6] -> [Recall filtered: 5]                            |
|                       |                                             |
|                       v                                             |
|                 [Merged: 6 people]                                  |
|                       v                                             |
|                 [Verified: 5 people]                                |
|                       v                                             |
|                 [Rule-filtered: 4 final]                            |
+--------------------------------------------------------------------+
| OCR text with final subjects highlighted and dropped candidates dim |
| Statement of slave [Mariam bint Yusuf] aged 20, native of Zanzibar. |
| She was kidnapped and sold to [Sheikh Rashid] of Dubai.             |
| Refugee slaves [Ahmad bin Said], [Fatima bint Ali], and             |
| [Zaid bin Omar] request repatriation...                             |
+--------------------------------------------------------------------+
| Final list (4 people)                                               |
| Mariam bint Yusuf  | "Statement of slave Mariam..."                 |
| Ahmad bin Said     | "Refugee slaves Ahmad bin Said..."             |
| Fatima bint Ali    | "Refugee slaves ... Fatima bint Ali..."        |
| Zaid bin Omar      | "Refugee slaves ... Zaid bin Omar..."          |
+--------------------------------------------------------------------+
| Dropped candidates with reasons                                     |
| Sheikh Rashid   | Rule: matched "sold to {name}" (negative)        |
| James Morrison  | Verify: not in subject group                     |
+--------------------------------------------------------------------+
```

Visualization goals:

1. **Clear flow diagram**: five stage cards plus arrows, each with a count and expandable prompt/response.
2. **Full-text highlighting**: final subjects highlighted, dropped candidates shown dimmed or struck through. This is the fastest way to see whether filtering is too strict or too loose.
3. **Final table**: one row per person plus evidence.
4. **Dropped-candidate explanation table**: every removed candidate must have a clear reason, including which pass removed it and whether the cause was a rule or a model judgment.
5. **Re-run verify button**: rerun only the verify prompt, saving a lot of time when tuning verify parameters.

## 8. Docker

Uses the shared `docker/ner.Dockerfile` described in module 04. The Compose fragment is similar to module 04, using port 5105 internally.

## 9. Tests

Unit tests, no LLM:

- `ROLE_NEGATIVE_PATTERNS` against "sold to X", "bought by X", and "master X".
- `ROLE_POSITIVE_PATTERNS` against "slave X" and "refugee slaves ... X".
- `is_freeborn_not_slave_name` behavior.
- `is_valid_name` for numbers, too-short names, and stopwords.

Integration tests requiring LLM:

- `single_subject.txt`: expect one person.
- `grouped_list.txt`: expect every person in the list.
- `owner_vs_slave.txt`: expect owner removed and enslaved subject retained.
- `freeborn_page.txt`: expect the "free born not slave" person removed.

## 10. Performance

Four LLM passes plus two filters plus one verify means at least five calls, often seven or more with JSON repairs. Expect about 15 to 40 seconds per page.

Optimization options outside the MVP:

- Run pass1 and recall in parallel.
- Merge filter into verify. This reduces one call but may reduce quality and needs experiments.

## 11. Build Checklist

- [ ] Five prompts are moved to files.
- [ ] Four passes, filters, verify, and rule filter are fully connected.
- [ ] UI visualizes all five stages.
- [ ] Full-text highlighting works, including fuzzy matching.
- [ ] Dropped-candidate reason table is complete.
- [ ] Four fixture tests produce reasonable results.
- [ ] Re-run verify endpoint works independently.
