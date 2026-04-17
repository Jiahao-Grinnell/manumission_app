# 模块 09 — aggregator

> 把所有页面的中间 JSON 合并成最终的三份 CSV。纯 Python，无 LLM。

## 1. 目的

遍历 `data/intermediate/<doc_id>/p*.meta.json` 和 `p*.places.json`，合并成：
- `Detailed info.csv`（每个人每页一行）
- `name place.csv`（每个人每页每地一行）
- `run_status.csv`（每页一行，记录处理状态）

并**在过程中应用跨页级别的清洗**（同一人不同页可能写法略有不同，需要在聚合时合并名字）。

## 2. 输入 / 输出

**输入**：
```
data/intermediate/<doc_id>/
├── p001.classify.json
├── p001.names.json
├── p001.meta.json
├── p001.places.json
├── p002.*.json
├── ...
```

**输出**：`data/output/<doc_id>/`
```
├── Detailed info.csv     # 匹配原 DETAIL_COLUMNS
├── name place.csv        # 匹配原 PLACE_COLUMNS
└── run_status.csv        # 匹配原 STATUS_COLUMNS
```

CSV 列定义继承原代码（见 `shared/schemas.py`）：

```python
DETAIL_COLUMNS = ["Name","Page","Report Type","Crime Type","Whether abuse","Conflict Type","Trial","Amount paid"]
PLACE_COLUMNS  = ["Name","Page","Place","Order","Arrival Date","Date Confidence","Time Info"]
STATUS_COLUMNS = ["page","filename","status","named_people","detail_rows","place_rows","model_calls","repair_calls","elapsed_seconds","note"]
```

## 3. 核心算法

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

    # 跨页清洗
    detail_rows = cleanup_detail_rows(detail_rows)
    place_rows = cleanup_place_rows(place_rows)

    # 原子写
    write_csv_atomic(paths.output_dir / "Detailed info.csv", detail_rows, DETAIL_COLUMNS)
    write_csv_atomic(paths.output_dir / "name place.csv",   place_rows,  PLACE_COLUMNS)
    write_csv_atomic(paths.output_dir / "run_status.csv",   status_rows, STATUS_COLUMNS)

    return AggregationResult(...)
```

**跨页清洗**（聚合时新增逻辑，原系统没有）：
- 同一 `doc_id` 内不同页出现的 "Mariam bint Yusuf" 和 "Marium bint Yusuf"，按 `names_maybe_same_person` 合并统一写法
- `name place.csv` 按 `(Name, Page)` 内 `dedupe_place_rows`
- 缺失字段统一为 ""（不留 None）

## 4. 目录结构

```
src/modules/aggregator/
├── __init__.py
├── core.py              # aggregate()
├── cleanup.py           # cleanup_detail_rows / cleanup_place_rows / 跨页合名
├── stats.py             # 统计面板用的指标计算
├── blueprint.py
├── standalone.py
├── cli.py
├── templates/
│   └── ui.html
└── tests/
    ├── test_core.py
    └── fixtures/
        └── mock_intermediate/   # 假的 page*.json
```

## 5. Blueprint API

| 方法 | 路径 | 行为 |
|---|---|---|
| GET  | `/aggregate/` | 测试 UI |
| GET  | `/aggregate/docs` | 所有有 intermediate 数据的 doc_id |
| POST | `/aggregate/run/<doc_id>` | 触发聚合 |
| GET  | `/aggregate/result/<doc_id>` | 当前 CSV 内容（JSON 返回前 100 行 + 统计） |
| GET  | `/aggregate/download/<doc_id>/<name>.csv` | 下载 CSV 文件 |
| GET  | `/aggregate/download/<doc_id>.zip` | 打包三份 CSV |
| GET  | `/aggregate/stats/<doc_id>` | 统计摘要 |

## 6. CLI

```bash
python -m modules.aggregator.cli \
  --inter_dir /data/intermediate/myDoc \
  --out_dir /data/output/myDoc
```

## 7. 测试 UI 设计

```
┌──────────────────────────────────────────────────────────────────────┐
│  Doc: [ myDoc ▼ ]       [ Re-aggregate ]   [ Download all (.zip) ]   │
├──────────────────────────────────────────────────────────────────────┤
│  Summary                                                              │
│  ┌──────────────────┬──────────────────┬──────────────────┐          │
│  │ Pages processed  │  Unique people   │  Detail rows     │          │
│  │       137        │       82         │      142         │          │
│  ├──────────────────┼──────────────────┼──────────────────┤          │
│  │ Unique places    │  Place rows      │  Skip rate       │          │
│  │       34         │      267         │      12%         │          │
│  └──────────────────┴──────────────────┴──────────────────┘          │
│                                                                        │
│  Report type distribution    Crime type distribution                  │
│  statement         ▓▓▓▓▓ 68  kidnapping       ▓▓▓▓ 54                 │
│  transport/admin   ▓▓▓ 41    trafficking      ▓▓ 23                   │
│  correspondence    ▓▓ 28     illegal detent.  ▓ 12                    │
│                              (empty)          ▓▓▓ 53                  │
├──────────────────────────────────────────────────────────────────────┤
│  [ Detailed info.csv ] [ name place.csv ] [ run_status.csv ]         │
│  (tabs switch the table below)                                        │
│                                                                        │
│  Filter: [ _________________________ ]   (filters current table)      │
│                                                                        │
│  ┌──────────────────────────────────────────────────────────────────┐ │
│  │ Name              │Page│Report    │Crime     │Abuse│Trial │Amt  │ │
│  ├──────────────────────────────────────────────────────────────────┤ │
│  │ Mariam bint Yusuf │ 12 │statement │kidnapping│yes  │manu..│    │ │
│  │ Ahmad bin Said    │ 14 │statement │sale      │     │released│   │ │
│  │ ...               │    │          │          │     │        │   │ │
│  └──────────────────────────────────────────────────────────────────┘ │
│  ← prev 25 | showing 1-25 of 142 | next 25 →                         │
├──────────────────────────────────────────────────────────────────────┤
│  Cross-page cleanup actions (applied)                                 │
│  • Merged 3 name variants: "Marium" → "Mariam bint Yusuf" (p14)      │
│  • Merged 2 name variants: "Ahmed" → "Ahmad bin Said" (p17, p19)     │
│  • Normalized 7 place variants via PLACE_MAP                          │
└──────────────────────────────────────────────────────────────────────┘
```

**可视化要点**：
1. **Summary 卡片**：最常看的数字（人、页、地、行数）一眼看全
2. **分布条形图**：report_type 和 crime_type 的分布快速质检
3. **三 tab 表格预览**：不下载也能看 CSV 内容
4. **过滤器**：前端 filter 方便临时查某人某页
5. **跨页清洗动作面板**：让你知道聚合做了哪些合并，出问题时能追溯

## 8. Docker

虽然不耗 LLM，还是给它一个独立容器以便 CI 和独立调用：

```yaml
  aggregator:
    build:
      context: .
      dockerfile: docker/ner.Dockerfile     # 和 NER 共用镜像
    networks: [ llm_internal ]
    volumes:
      - ./data/intermediate:/data/intermediate:ro
      - ./data/output:/data/output
    profiles: [ "standalone", "all" ]
    command: >
      gunicorn -b 0.0.0.0:5109 -w 2 --timeout 300
      'modules.aggregator.standalone:create_app()'
```

## 9. 测试

**单元测试**（最容易测的模块）：
- `fixtures/mock_intermediate/` 放 3 页的假 JSON（含 skip 页、含多人页、含地点冲突）
- `test_aggregate_small_doc()` 验证输出 CSV 的行数和关键字段
- `test_cross_page_name_merge()` 验证同人不同拼写被合并
- `test_atomic_write()` 验证半写入不会破坏旧文件（mock IO 异常）
- `test_empty_doc()` 边界：`intermediate/` 为空时也要产出 3 份空 CSV（带表头）

## 10. 构建检查清单

- [ ] `aggregate()` 读全所有 intermediate JSON
- [ ] CSV 列和原系统一致
- [ ] 跨页同人合并启用
- [ ] 原子写（tmp + rename）
- [ ] 空数据也产出空 CSV（保留表头）
- [ ] UI 三 tab + 过滤器 + 分页
- [ ] 统计卡片 + 分布条形图
- [ ] zip 下载
- [ ] 单测覆盖 4 个典型场景
