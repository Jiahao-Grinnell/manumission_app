# Module 07 - place_extractor

> Extract each subject's page-level place path, such as birthplace, place of capture, and arrival place, along with related time information. Outputs multiple rows for `name place.csv`.

## 1. Purpose

Given `(ocr_text, name, page)`, produce every place associated with that person **within the current page**. Each place includes:

- `order`: `0` means background association; `1`, `2`, `3`, and so on are route order.
- `arrival_date`: ISO `YYYY-MM-DD` or empty.
- `date_confidence`: `explicit` / `derived_from_doc` / `unknown` / "".
- `time_info`: original non-ISO time wording.
- `evidence`: quote of 25 words or fewer.

This is the second most complex module after `name_extractor`: three LLM passes, date enrichment, and rule-based reconciliation.

## 2. Input / Output

**Input**: OCR text plus identified subject list.

**Output**: `data/intermediate/<doc_id>/p<N>.places.json`

```json
{
  "page": 12,
  "people": [
    {
      "name": "Mariam bint Yusuf",
      "rows": [
        {"Name":"...","Page":12,"Place":"Zanzibar","Order":1,"Arrival Date":"","Date Confidence":"","Time Info":"","_evidence":"native of Zanzibar"},
        {"Name":"...","Page":12,"Place":"Dubai","Order":2,"Arrival Date":"1931-05-17","Date Confidence":"explicit","Time Info":"17th May 1931","_evidence":"arrived at Dubai about the 17th May 1931"}
      ],
      "passes": {
        "candidates": [...],
        "verified": [...],
        "reconciled": [...]
      }
    }
  ],
  "model_calls": 8,
  "repair_calls": 1,
  "elapsed_seconds": 28.5
}
```

## 3. Core Algorithm (Inherited From `model_places_for_name`)

```text
+-- pass 1: PLACE_PASS_PROMPT, high-recall candidates --------+
|                                                            |
|   LLM -> candidates, noise is allowed                       |
|   parse_places -> normalize and deduplicate                 |
|                                                            |
+-- pass 2: PLACE_VERIFY_PROMPT, final decision -------------+
|                                                            |
|   Input: OCR + candidates + issues from previous round      |
|   LLM -> verified                                           |
|                                                            |
|   verify_place_rows_need_retry checks:                      |
|     - order must be consecutive 1..n                        |
|     - no duplicate place                                    |
|     - date_conf is consistent with arrival_date             |
|     - dates are ascending by order                          |
|                                                            |
|   If validation fails, resend once with issues              |
|                                                            |
+-- rule layer: reconcile_place_rows -------------------------+
|     - infer_forwarding_transport_rows                       |
|       from patterns like "from X, arriving Y"               |
|     - is_confident_place_text / is_uncertain...             |
|       uses regexes to judge confidence                      |
|     - recompute order                                       |
|                                                            |
+-- dedupe_place_rows -> final output ------------------------+
```

Key design points:

- Allow the verifier to fail twice, then fall back to candidates as a safety net so data is not lost.
- `order` semantics: positive orders `1..n` are the actual route; `0` means a place was mentioned but is not in the route, such as background or administrative mentions.
- Date-confidence levels:
  - `explicit`: date appears directly in the text.
  - `derived_from_doc`: derived from the document date at the top of the page.
  - `unknown` / "" means unknown.

Load prompts from `config/prompts/place_extractor/place_pass.txt`, `place_verify.txt`, and `place_date_enrich.txt`.

## 4. Directory Structure

```text
src/modules/place_extractor/
|-- __init__.py
|-- core.py              # extract_for_name()
|-- passes.py            # candidate_pass / verify_pass
|-- reconcile.py         # reconcile_place_rows / infer_forwarding_transport_rows
|-- parsing.py           # parse_places
|-- validation.py        # verify_place_rows_need_retry
|-- blueprint.py
|-- standalone.py
|-- cli.py
|-- templates/
|   `-- ui.html
`-- tests/
    |-- test_reconcile.py
    |-- test_validation.py
    `-- fixtures/
        |-- single_place.txt
        |-- multi_route.txt     # Has "from X arriving Y"
        `-- ambiguous.txt       # Includes owner places, ship names, and other noise
```

Dependencies: place normalization (`normalize_place` / `PLACE_MAP`), date parsing (`to_iso_date`), and deduplication (`dedupe_place_rows`) live in `08 normalizer`.

## 5. Blueprint API

| Method | Path | Behavior |
|---|---|---|
| GET | `/places/` | Test UI |
| GET | `/places/people/<doc_id>/<page>` | Identified subjects on the page |
| POST | `/places/run-single/<doc_id>/<page>/<n>` | Extract places for one person |
| POST | `/places/run-page/<doc_id>/<page>` | Extract places for all subjects on the page |
| POST | `/places/run-all/<doc_id>` | Run the whole document asynchronously |

## 6. CLI

```bash
python -m modules.place_extractor.cli \
  --in_dir /data/ocr_text/myDoc \
  --inter_dir /data/intermediate/myDoc \
  --out_dir /data/intermediate/myDoc \
  --model qwen2.5:14b-instruct
```

## 7. Test UI Design

```text
+----------------------------------------------------------------------+
| Doc: [ myDoc ]  Page: [ p012 ]  Person: [ Mariam bint Y. ]           |
| [ Extract places for this person ]                                   |
+----------------------------------------------------------------------+
| Route visualization (ordered cards with arrows)                       |
|                                                                      |
| [1. Zanzibar] -> [2. Mekran, 1931-02, derived] -> [3. Dubai, 1931-05-17 explicit] |
|   "native of Zanzibar"  "taken to Mekran"  "arriving Dubai about 17th May" |
|                                                                      |
| Background mentions (order=0):                                       |
| [0. Bushehr] "forwarded from Bushehr Agency"                         |
+----------------------------------------------------------------------+
| Stage results                                                        |
| [ Candidates (6) ] [ Verified (4) ] [ Reconciled (4) ]               |
| Each tab is a table with place / order / date / evidence.            |
+----------------------------------------------------------------------+
| OCR text with extracted places highlighted by date confidence         |
| Statement of slave Mariam bint Yusuf, native of Zanzibar.            |
| Kidnapped at age 10 and taken to Mekran for about five years.        |
| Arrived at Dubai about the 17th May 1931, forwarded from Bushehr.    |
| H.M.S. Shoreham transported her...                                   |
+----------------------------------------------------------------------+
| Validation                                                           |
| OK Positive orders form 1..3 consecutively                           |
| OK No duplicate places                                               |
| OK Dates are ascending with order                                    |
| OK No ships or generic office words                                  |
+----------------------------------------------------------------------+
```

Visualization goals:

1. **Ordered route cards** connected by arrows make migration paths readable at a glance. Background associations appear separately.
2. **Date-confidence colors**: explicit = green, derived = yellow, unknown = gray.
3. **Three-stage tabs**: candidates reveal noise, verified shows model decisions, and reconciled shows final rule-adjusted output.
4. **Source-text place highlighting** uses the same colors. Ship names and generic office words should not be highlighted; if they are, the module has a bug.
5. **Validation panel** explicitly shows every rule, with failures in red.

## 8. Docker

Uses shared `docker/ner.Dockerfile`. Internal Compose port is 5107.

## 9. Tests

Unit tests:

- `reconcile_place_rows` sorting behavior for varied candidates.
- `verify_place_rows_need_retry` for each validation failure mode.
- `infer_forwarding_transport_rows` for "from X, arriving Y" patterns.
- `dedupe_place_rows` merge behavior.

Integration tests:

- `single_place.txt`: expect one place with `order=1`.
- `multi_route.txt`: expect multiple places in the correct order.
- `ambiguous.txt`: expect noise such as ship names and owner places to be removed.

## 10. Performance

Three LLM passes plus a possible verifier retry plus optional date enrichment equals about four to six calls per person times the number of people on the page. Pages with many people will be slow; consider concurrency if needed.

## 11. Build Checklist

- [ ] Three prompts are moved to files.
- [ ] `reconcile_place_rows` rules are complete.
- [ ] `verify_place_rows_need_retry` validation is comprehensive.
- [ ] UI renders route cards.
- [ ] Date-confidence color coding is implemented.
- [ ] Three-stage tabs work.
- [ ] Source text highlights places without highlighting ships or offices.
- [ ] Validation panel is visible.
- [ ] Three fixtures pass.
