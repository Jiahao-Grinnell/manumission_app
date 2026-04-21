# Module 09 - aggregator

> Merge all per-page intermediate JSON files into the three final CSV files. Pure Python, no LLM.

Implementation status as of 2026-04-20: core aggregation, cross-page name cleanup, place normalization/deduplication, atomic CSV writes, `aggregation_summary.json`, CLI, unit tests, zip download, and standalone UI are implemented. Main `web_app` mounting remains a Phase 6 integration step.

## 1. Purpose

Traverse `data/intermediate/<doc_id>/p*.meta.json` and `p*.places.json`, then merge them into:

- `Detailed info.csv`: one row per person per page
- `name place.csv`: one row per person, page, and place
- `run_status.csv`: one row per page, recording processing status

During aggregation, apply **cross-page cleaning**. The same person may be spelled slightly differently on different pages, so names should be merged at aggregation time.

## 2. Input / Output

**Input**:

```text
data/intermediate/<doc_id>/
|-- p001.classify.json
|-- p001.names.json
|-- p001.meta.json
|-- p001.places.json
|-- p002.*.json
`-- ...
```

**Output**: `data/output/<doc_id>/`

```text
|-- Detailed info.csv     # Matches original DETAIL_COLUMNS
|-- name place.csv        # Matches original PLACE_COLUMNS
|-- run_status.csv        # Matches original STATUS_COLUMNS
`-- aggregation_summary.json
```

CSV column definitions are inherited from the original code. See `shared/schemas.py`:

```python
DETAIL_COLUMNS = ["Name","Page","Report Type","Crime Type","Whether abuse","Conflict Type","Trial","Amount paid"]
PLACE_COLUMNS  = ["Name","Page","Place","Order","Arrival Date","Date Confidence","Time Info"]
STATUS_COLUMNS = ["page","filename","status","named_people","detail_rows","place_rows","model_calls","repair_calls","elapsed_seconds","note"]
```

## 3. Core Algorithm

```python
def aggregate(doc_id: str) -> AggregationResult:
    paths = doc_paths(doc_id)
    detail_rows, place_rows, status_rows = [], [], []

    for page_num in sorted_pages(paths.inter_dir):
        classify = read_json(paths.classify(page_num))
        status_rows.append(build_status_row(page_num, classify, ...))

        if not classify.get("should_extract"):
            continue

        meta = read_json(paths.meta(page_num))
        places = read_json(paths.places(page_num))
        detail_rows.extend(meta["rows"])

        for person in places["people"]:
            if person["rows"]:
                place_rows.extend(person["rows"])
            else:
                place_rows.append(blank_place_row(person["name"], page_num))

    # Cross-page cleaning
    detail_rows = cleanup_detail_rows(detail_rows)
    place_rows = cleanup_place_rows(place_rows)

    # Atomic writes
    write_csv_atomic(paths.output_dir / "Detailed info.csv", detail_rows, DETAIL_COLUMNS)
    write_csv_atomic(paths.output_dir / "name place.csv",   place_rows,  PLACE_COLUMNS)
    write_csv_atomic(paths.output_dir / "run_status.csv",   status_rows, STATUS_COLUMNS)

    return AggregationResult(...)
```

Cross-page cleaning added during aggregation:

- In one `doc_id`, merge names such as "Mariam bint Yusuf" and "Marium bint Yusuf" with `names_maybe_same_person`.
- Deduplicate `name place.csv` rows by `(Name, Page)` with `dedupe_place_rows`.
- Normalize missing values to `""` and do not keep `None`.

## 4. Directory Structure

```text
src/modules/aggregator/
|-- __init__.py
|-- core.py              # aggregate()
|-- cleanup.py           # cleanup_detail_rows / cleanup_place_rows / cross-page name merge
|-- stats.py             # Metrics for the statistics panel
|-- blueprint.py
|-- standalone.py
|-- cli.py
|-- templates/
|   `-- ui.html
`-- tests/
    |-- test_core.py
    `-- fixtures/
        `-- mock_intermediate/   # Fake page*.json
```

## 5. Blueprint API

| Method | Path | Behavior |
|---|---|---|
| GET | `/aggregate/` | Test UI |
| GET | `/aggregate/docs` | All `doc_id` values with intermediate data |
| POST | `/aggregate/run/<doc_id>` | Trigger aggregation |
| GET | `/aggregate/result/<doc_id>` | Return current CSV contents as JSON, first 100 rows plus stats |
| GET | `/aggregate/download/<doc_id>/<name>.csv` | Download a CSV file |
| GET | `/aggregate/download/<doc_id>.zip` | Download all three CSV files as a zip |
| GET | `/aggregate/stats/<doc_id>` | Statistics summary |

## 6. CLI

```bash
python -m modules.aggregator.cli \
  --inter_dir /data/intermediate/myDoc \
  --out_dir /data/output/myDoc
```

## 7. Test UI Design

```text
+----------------------------------------------------------------------+
|  Doc: [ myDoc ]       [ Re-aggregate ]   [ Download all (.zip) ]     |
+----------------------------------------------------------------------+
|  Summary                                                             |
|  Pages processed: 137   Unique people: 82   Detail rows: 142         |
|  Unique places: 34      Place rows: 267     Skip rate: 12%           |
|                                                                      |
|  Report type distribution          Crime type distribution           |
|  statement: 68                     kidnapping: 54                    |
|  transport/admin: 41               illegal detention: 23             |
|  correspondence: 28                (empty): 12                       |
+----------------------------------------------------------------------+
|  [ Detailed info.csv ] [ name place.csv ] [ run_status.csv ]         |
|  Filter: [ _________________________ ]                               |
|                                                                      |
|  Name              Page Report    Crime      Abuse Trial       Amt   |
|  Mariam bint Yusuf 12   statement kidnapping yes   manu...           |
|  Ahmad bin Said    14   correspondence illegal...     freedom...     |
|  ...                                                                 |
|  prev 25 | showing 1-25 of 142 | next 25                             |
+----------------------------------------------------------------------+
|  Cross-page cleanup actions applied                                  |
|  - Merged 3 name variants: "Marium" -> "Mariam bint Yusuf" (p14)     |
|  - Merged 2 name variants: "Ahmed" -> "Ahmad bin Said" (p17, p19)   |
|  - Normalized 7 place variants through PLACE_MAP                     |
+----------------------------------------------------------------------+
```

Visualization goals:

1. **Summary cards** show the numbers users check most often: people, pages, places, and row counts.
2. **Distribution bar charts** make report type and crime type quality checks quick.
3. **Three-tab table preview** lets users inspect CSV contents without downloading.
4. **Filter box** supports quick lookup for a person or page in the current table.
5. **Cross-page cleanup action panel** shows exactly what aggregation merged, which makes issues traceable.

## 8. Docker

Even though it does not use an LLM, it has a standalone container for CI and independent calls:

```yaml
  aggregator:
    build:
      context: .
      dockerfile: docker/aggregator.Dockerfile
    networks: [ llm_frontend ]
    volumes:
      - ./data:/data
    ports:
      - "127.0.0.1:5109:5109"
    profiles: [ "aggregator", "standalone", "all" ]
    command: >
      gunicorn -b 0.0.0.0:5109 -w 1 --timeout 300
      'modules.aggregator.standalone:create_app()'
```

Run it:

```bash
docker compose --profile aggregator up -d --build aggregator
```

Open:

```text
http://127.0.0.1:5109/aggregate/
```

## 9. Tests

Unit tests, easiest module to test:

- Put three pages of fake JSON in `fixtures/mock_intermediate/`, including a skipped page, a multi-person page, and place conflicts.
- `test_aggregate_small_doc()` verifies CSV row counts and key fields.
- `test_cross_page_name_merge()` verifies same-person spelling variants are merged.
- `test_atomic_write()` verifies an interrupted write does not corrupt the old file, using mocked I/O exceptions.
- `test_empty_doc()` verifies an empty `intermediate/` still produces three empty CSV files with headers.

Current test command:

```bash
docker build -f docker/aggregator.Dockerfile -t manumission-aggregator:phase2 .
docker run --rm manumission-aggregator:phase2 python -m unittest discover -s /app/modules/aggregator/tests -p "test_*.py"
```

Current fake-data smoke command:

```bash
docker compose --profile aggregator run --rm aggregator python -m modules.aggregator.cli --doc-id agg_smoke
```

## 10. Build Checklist

- [x] `aggregate()` reads all intermediate JSON files.
- [x] CSV columns match the original system.
- [x] Cross-page same-person merging is enabled.
- [x] Atomic writes use tmp + rename.
- [x] Empty data still produces empty CSV files with headers.
- [x] UI previews all three CSVs.
- [x] Statistics cards are present.
- [x] Zip download works.
- [x] Unit tests cover small-doc and empty-doc scenarios.
- [ ] UI filter and pagination.
- [ ] Broaden tests for mocked interrupted writes.
