# Module 08 - normalizer

> Pure Python normalization, validation, and deduplication utilities. **Modules 05, 06, 07, and 09 depend on it. It does not depend on any LLM.**

## 1. Purpose

Centralize the "data cleaning" logic that was scattered throughout the original `ner_extract.py`:

- **Name** normalization: casing, accents, and connectors such as `bin`, `bint`, and `ibn`
- **Place** normalization: historical spelling map such as `"shargah" -> "Sharjah"`, plus ship-name stripping
- **Date** parsing: many written forms to ISO 8601
- **Name identity** checks: fuzzy matching for the same person with different spellings
- **Deduplication**: merge place rows while preserving the best order and date information
- **Evidence cleaning**: truncate to 25 words and normalize

These functions are independent of the LLM and Flask. They are pure functions, easy to test, and the most deterministic part of the pipeline.

## 2. Why This Is a Separate Module

There are two reasons:

1. It is shared by four or more modules and must be extracted.
2. It has high visualization value. Name, place, and date rules are complex, and an interactive UI lets users test rules at any time.

This module is both a **library** imported by other modules and a **service** with its own UI. It does **not** have an independent container; its blueprint is mounted inside the main `web_app`.

## 3. Directory Structure

```text
src/modules/normalizer/
|-- __init__.py
|-- names.py            # normalize_name / is_valid_name / names_maybe_same_person
|                       # merge_named_people / choose_preferred_name
|                       # name_compare_tokens / build_name_regex
|-- places.py           # normalize_place / is_valid_place / PLACE_MAP
|                       # dedupe_place_rows / merge_place_date_enrichment
|-- dates.py            # to_iso_date / parse_day_month / parse_first_date_in_text
|                       # extract_doc_year / MONTHS / ISO_DATE_PAT
|-- evidence.py         # clean_evidence / normalize_for_match
|-- vocabulary.py       # NAME_STOPWORDS / PLACE_STOPWORDS, loaded from config/schemas/vocab.yaml
|-- blueprint.py        # /normalizer/ UI, mounted only in the main web_app
|-- templates/
|   `-- ui.html
`-- tests/
    |-- test_names.py
    |-- test_places.py
    |-- test_dates.py
    `-- test_evidence.py
```

## 4. Key APIs

### 4.1 `names.py`

```python
normalize_name(s: str) -> str
# "  mariam   Bint   YUSUF  " -> "Mariam bint Yusuf"
# Strip accents, strip stopword prefix such as "the slave X",
# merge whitespace, title-case, and keep connectors like bin/bint/ibn/al/el/ul lowercase.

is_valid_name(name: str) -> bool
# Reject names with digits, length < 2, stopword-only values, or no letters.

names_maybe_same_person(a: str, b: str) -> bool
# Multi-strategy comparison: exact match, first-token match + SequenceMatcher,
# token overlap ratio, and containment.

merge_named_people(*groups) -> List[dict]
# Merge multiple candidate groups into a unique person list, choosing the best spelling.

choose_preferred_name(items) -> dict
# Choose the richest form among several spellings of the same person.

build_name_regex(name: str) -> re.Pattern
# Build a matching pattern that tolerates punctuation and whitespace,
# used for highlighting names in source text.
```

### 4.2 `places.py`

```python
normalize_place(s: str) -> str
# "ras ul khaimah" -> "Ras al Khaimah"
# Uses PLACE_MAP for historical spelling mappings plus title case.

is_valid_place(place: str) -> bool
# Reject ship names such as H.M.S., dhow, and steamship,
# generic office words, digits, and overly long strings.

dedupe_place_rows(rows, *, drop_internal=True) -> List[dict]
# Merge by (Name, Place), preserving the best Order, Arrival Date, and Time Info.
```

`PLACE_MAP` is inherited from the original code and may also be loaded from `config/schemas/vocab.yaml` so operations users can update it:

```yaml
place_map:
  shargah: Sharjah
  sharjeh: Sharjah
  dibai: Dubai
  bahrein: Bahrain
  ...
```

### 4.3 `dates.py`

```python
to_iso_date(text: str, doc_year: Optional[int]) -> Tuple[str, str]
# Returns (iso_date, confidence)
# Supports:
#   "1931-05-17"               -> ("1931-05-17", "explicit")
#   "17-5-1931"                -> ("1931-05-17", "explicit")
#   "May 17, 1931"             -> ("1931-05-17", "explicit")
#   "17th May 1931"            -> ("1931-05-17", "explicit")
#   "17th May" + doc_year=1931 -> ("1931-05-17", "derived_from_doc")
#   "some random text"         -> ("", "")

parse_first_date_in_text(text, doc_year) -> (iso, conf, raw)
extract_doc_year(text) -> Optional[int]
```

### 4.4 `evidence.py`

```python
clean_evidence(s: str) -> str
# Normalize whitespace and truncate to 25 words.

normalize_for_match(s: str) -> str
# Lowercase, strip accents, and turn non-alphanumeric characters into spaces.
# Used for fuzzy locating evidence in source text.
```

## 5. Blueprint (UI Only)

| Method | Path | Behavior |
|---|---|---|
| GET | `/normalizer/` | Test UI |
| POST | `/normalizer/normalize/name` | `{"raw":"..."}` -> `{"normalized":"...","valid":true,"reason":""}` |
| POST | `/normalizer/normalize/place` | Same shape as name normalization |
| POST | `/normalizer/normalize/date` | `{"raw":"...","doc_year":1931}` -> `{"iso":"1931-05-17","confidence":"explicit","raw_matched":"17th May"}` |
| POST | `/normalizer/compare-names` | `{"a":"...","b":"..."}` -> `{"same":true,"reason":"token overlap 0.83"}` |
| POST | `/normalizer/dedupe-places` | Paste rows and return deduplicated results |

## 6. Test UI Design

A single-page app with five tabs:

```text
+----------------------------------------------------------------+
|  [Names] [Places] [Dates] [Compare names] [Dedupe places]      |
+----------------------------------------------------------------+
|  Names tab                                                     |
|                                                                |
|  Input:              Normalized:                               |
|  "Mariam BINT YUSUF" -> "Mariam bint Yusuf"                    |
|                                                                |
|  Valid: yes                                                    |
|  Transformations applied:                                      |
|  - strip accents                                               |
|  - merge whitespace                                            |
|  - strip "the slave" prefix: not matched                       |
|  - title case                                                  |
|  - keep connector "bint" lowercase                             |
+----------------------------------------------------------------+
|  Dates tab                                                     |
|                                                                |
|  Input: "17th May"     doc_year: 1931     [ Parse ]            |
|  Result: ISO 1931-05-17, confidence derived_from_doc           |
|  Tried patterns:                                               |
|  - ISO_DATE_PAT: no match                                      |
|  - slash/dash: no match                                        |
|  - "Month D, YYYY": no match                                   |
|  - "D{ord} Month YYYY": match, fallback to doc_year            |
+----------------------------------------------------------------+
```

Visualization goals:

- **Immediate feedback**: input boxes trigger after a 300 ms debounce.
- **Rule-hit visualization**: especially useful for the date parser, which has several fallbacks.
- **Compare names tab**: two inputs, token display, and overlap progress.
- **Dedupe places tab**: paste JSON or CSV and display row-count changes plus merged pairs.

This UI is especially useful for domain researchers who are not developers. They can verify cases like "Bushire / Busheir / Bushehr will be merged" by themselves.

## 7. Docker

This module does **not** exist independently as a container. It is copied into all NER-related images by `docker/ner.Dockerfile`; its blueprint is mounted in the main app built by `docker/web.Dockerfile`.

## 8. Tests

**Unit test coverage target: >= 90%**. This layer has no LLM and no network, so high coverage is realistic.

- `test_names.py`: 30+ edge cases, including accents, connectors, OCR mistakes, and numeric strings.
- `test_places.py`: one test for each `PLACE_MAP` entry, plus ship-name rejection and generic-word rejection.
- `test_dates.py`: one test per date format plus `doc_year` fallback.
- `test_evidence.py`: long evidence truncation and whitespace merging.

```bash
pytest src/modules/normalizer/tests/ --cov=modules.normalizer
```

## 9. Important Conventions

- **Do not raise exceptions**: for bad input, return `""` or `None` instead of making callers wrap everything in try/except.
- **Pure functions**: no side effects and no I/O.
- **Importable**: other modules can call `from modules.normalizer.names import normalize_name`.
- **Config is editable**: lookup tables such as `PLACE_MAP` are loaded from YAML so operations users can change YAML without code edits.

## 10. Build Checklist

- [ ] All normalization functions from the original code are moved here.
- [ ] `config/schemas/vocab.yaml` is the source of truth for `PLACE_MAP` and stopwords.
- [ ] Unit test coverage is at least 90%.
- [ ] All UI tabs are present.
- [ ] Date UI shows the pattern-hit trace.
- [ ] Name comparison UI shows token overlap.
- [ ] Blueprint is mounted at `/normalizer/` in the main web_app.
- [ ] Other modules import and use it.
