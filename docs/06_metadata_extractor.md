# Module 06 - metadata_extractor

> For each identified subject, extract five case metadata fields from the current page. Outputs one row for `Detailed info.csv`.

## 1. Purpose

Given `(ocr_text, name, page, report_type)`, produce one detail row through **a single LLM call**, evidence requirements, and rule validation:

| Field | Type | Allowed Values |
|---|---|---|
| Name | str | Subject name |
| Page | int | Page number |
| Report Type | enum | `statement` / `transport/admin` / `correspondence` |
| Crime Type | enum | `kidnapping` / `sale` / `trafficking` / `illegal detention` / `forced transfer` / `debt-claim transfer` / "" |
| Whether abuse | enum | `yes` / `no` / "" |
| Conflict Type | enum | `manumission dispute` / `ownership dispute` / `debt dispute` / `free-status dispute` / `forced-transfer dispute` / `repatriation dispute` / `kidnapping case` / "" |
| Trial | enum | `manumission requested` / `manumission certificate requested` / `manumission recommended` / `manumission granted` / `free status confirmed` / `released` / `repatriation arranged` / `certificate delivered` / "" |
| Amount paid | str | Literal amount string or "" |

Key constraint: the model must provide evidence for every non-empty field, using a quote of 25 words or fewer from the source text. Any inference without evidence is discarded.

## 2. Input / Output

**Input**:

- `data/ocr_text/<doc_id>/p<N>.txt`
- `data/intermediate/<doc_id>/p<N>.classify.json`
- `data/intermediate/<doc_id>/p<N>.names.json`

**Output**: `data/intermediate/<doc_id>/p<N>.meta.json`

```json
{
  "page": 12,
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
        "crime_type": "kidnapped when I was about 10 years old",
        "whether_abuse": "beaten severely by her owner",
        "trial": "requests manumission certificate"
      }
    }
  ],
  "model_calls": 1,
  "repair_calls": 0,
  "elapsed_seconds": 4.1
}
```

## 3. Core Algorithm (Inherited From `model_meta_for_name`)

```python
def extract(ocr, name, page, report_type, stats) -> DetailRow:
    schema = '{"name":"...","page":0,"report_type":"...","crime_type":null,"whether_abuse":"",...}'
    obj = client.generate_json(
        render(META_PASS_PROMPT, name=name, page=page, report_type=report_type, ocr=ocr),
        schema, stats, num_predict=1000)
    return parse_meta(obj, name, page, report_type)
```

Responsibilities of `parse_meta`:

- `choose_allowed(value, CRIME_TYPES)`: values outside the allowlist become `""`.
- `choose_yes_no_blank`: `whether_abuse` must be `yes`, `no`, or `""`.
- `amount_paid`: literal strings such as `"null"` or `"none"` are filtered to `""`.

Load the prompt from `config/prompts/meta_pass.txt`, moved from the original `META_PASS_PROMPT`.

## 4. Directory Structure

```text
src/modules/metadata_extractor/
|-- __init__.py
|-- core.py              # extract()
|-- vocab.py             # CRIME_TYPES / CONFLICT_TYPES / TRIAL_TYPES and other enums
|-- parsing.py           # parse_meta() + choose_allowed
|-- blueprint.py
|-- standalone.py
|-- cli.py
|-- templates/
|   `-- ui.html
`-- tests/
    |-- test_parsing.py
    `-- fixtures/
        |-- kidnapping_abuse.txt
        |-- repatriation.txt
        `-- certificate_grant.txt
```

Enum values should live in `vocab.py` and be generated from `config/schemas/vocab.yaml`. YAML is the source of truth so non-programmers can edit allowed values.

## 5. Blueprint API

| Method | Path | Behavior |
|---|---|---|
| GET | `/meta/` | Test UI |
| GET | `/meta/pages/<doc_id>` | Pages where metadata can be extracted, meaning names already exist |
| GET | `/meta/people/<doc_id>/<page>` | All identified subjects on the page |
| POST | `/meta/run-single/<doc_id>/<page>/<name>` | Extract metadata for one person |
| POST | `/meta/run-page/<doc_id>/<page>` | Extract metadata for all subjects on the page |
| POST | `/meta/run-all/<doc_id>` | Run the whole document asynchronously |

## 6. CLI

```bash
python -m modules.metadata_extractor.cli \
  --in_dir /data/ocr_text/myDoc \
  --inter_dir /data/intermediate/myDoc \
  --out_dir /data/intermediate/myDoc \
  --model qwen2.5:14b-instruct
```

## 7. Test UI Design

```text
+--------------------------------------------------------------------+
| Doc: [ myDoc ]   Page: [ p012 ]   Person: [ Mariam b. Y. ]         |
| [ Extract meta for this person ]    [ Extract for all on page ]    |
+--------------------------------------------------------------------+
| Detail row for "Mariam bint Yusuf" on page 12                      |
| Report Type     statement        Evidence: from page classifier    |
| Crime Type      kidnapping       "kidnapped when I was about..."   |
| Whether abuse   yes              "beaten severely by her owner"    |
| Conflict Type   (empty)          -                                 |
| Trial           manumission requested  "requests manumission..."   |
| Amount paid     (empty)          -                                 |
+--------------------------------------------------------------------+
| OCR text with all evidence spans highlighted in different colors    |
| Statement of slave Mariam bint Yusuf, aged 20, native of Zanzibar.  |
| She was [crime evidence] and [abuse evidence]. She now [trial].     |
+--------------------------------------------------------------------+
| Validation                                                         |
| OK Crime Type is in allowed set                                    |
| OK Whether abuse is one of {yes,no,""}                             |
| OK Trial is in allowed set                                         |
| - Conflict Type empty (no evidence)                                |
| - Amount paid empty (no evidence)                                  |
+--------------------------------------------------------------------+
```

Visualization goals:

1. **Field cards plus paired evidence**: show each field next to its evidence so the relationship is immediate.
2. **Different evidence colors in source text**: crime, abuse, conflict, trial, and amount should each have a distinct color.
3. **Jump-to-location**: clicking an evidence link scrolls to the matching source text span.
4. **Validation panel**: show whether each field passed allowlist validation.
5. **Explicit empty display**: show `-` for empty fields so `""` is not confused with `null`.

## 8. Docker

Uses shared `docker/ner.Dockerfile`. Internal Compose port is 5106.

## 9. Tests

Unit tests:

- `choose_allowed` clears values outside the allowlist.
- `choose_yes_no_blank` handles varied inputs correctly.
- `parse_meta` has safe fallback behavior for missing fields and wrong JSON types.

Integration tests:

- Three fixtures each cover a metadata combination and assert expected fields.

## 10. Build Checklist

- [ ] Prompt is moved to a file.
- [ ] Vocab is generated from YAML to avoid hard-coded values.
- [ ] `parse_meta` performs strict allowlist validation.
- [ ] UI shows field cards paired with evidence.
- [ ] Source text supports multi-color highlighting.
- [ ] Validation panel shows results.
- [ ] Three fixture tests pass.
