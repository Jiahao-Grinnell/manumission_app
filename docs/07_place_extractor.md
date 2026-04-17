# 模块 07 — place_extractor

> 对每个主体抽取其页面级地点路径（出生地、被掳处、到达地等）和对应时间。输出 `name place.csv` 的多行。

## 1. 目的

给定 `(ocr_text, name, page)`，产出该人在**本页内**涉及的所有地点，每个地点附带：
- `order`（0 表示背景关联，1/2/3... 表示路径序号）
- `arrival_date`（ISO `YYYY-MM-DD` 或空）
- `date_confidence`（`explicit` / `derived_from_doc` / `unknown` / ""）
- `time_info`（非 ISO 的原始时间表述）
- `evidence`（≤25 词引用）

这是继 name_extractor 之后第二复杂的模块（3 轮 LLM + 日期 enrich + 规则 reconcile）。

## 2. 输入 / 输出

**输入**：OCR 文本 + 已识别主体列表

**输出**：`data/intermediate/<doc_id>/p<N>.places.json`
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

## 3. 核心算法（继承原代码 `model_places_for_name`）

```
┌── pass 1: PLACE_PASS_PROMPT (candidate 高召回)  ──┐
│                                                   │
│   LLM → candidates (允许混入噪声)                  │
│                                                   │
│   parse_places → 规范化、去重                      │
│                                                   │
├── pass 2: PLACE_VERIFY_PROMPT (最终裁决)          │
│                                                   │
│   LLM 输入：OCR + candidates + 之前轮次 issues    │
│        → verified                                  │
│                                                   │
│   verify_place_rows_need_retry 校验：             │
│     - order 必须连续 1..n                         │
│     - 无重复 place                                │
│     - date_conf 与 arrival_date 一致              │
│     - 日期升序符合 order                          │
│                                                   │
│   如果校验失败，带 issues 重发一次                 │
│                                                   │
├── 规则层：reconcile_place_rows                    │
│     - infer_forwarding_transport_rows             │  （从 "from X, arriving Y" 推路由）
│     - is_confident_place_text / is_uncertain...   │  （正则判断是否 confident）
│     - 重算 order                                  │
│                                                   │
└── dedupe_place_rows → 最终输出                    │
```

关键设计点：
- **允许 verifier 失败 2 次**后用 candidates fallback（safety net，别丢数据）
- **order 语义**：1..n 是真正的路径序号；0 是"提到了但不在路径里"（背景关联、行政提及）
- **日期置信度分层**：
  - `explicit`：文中直接写了日期
  - `derived_from_doc`：靠页面顶部的文档日期推导
  - `unknown` / "" 未知

Prompt 从 `config/prompts/place_pass.txt`、`place_verify.txt`、`place_date_enrich.txt` 加载。

## 4. 目录结构

```
src/modules/place_extractor/
├── __init__.py
├── core.py              # extract_for_name()
├── passes.py            # candidate_pass / verify_pass
├── reconcile.py         # reconcile_place_rows / infer_forwarding_transport_rows
├── parsing.py           # parse_places
├── validation.py        # verify_place_rows_need_retry
├── blueprint.py
├── standalone.py
├── cli.py
├── templates/
│   └── ui.html
└── tests/
    ├── test_reconcile.py
    ├── test_validation.py
    └── fixtures/
        ├── single_place.txt
        ├── multi_route.txt     # 有 from X arriving Y
        └── ambiguous.txt       # 出现 owner place、ship name 等噪声
```

**依赖**：地名规范化（`normalize_place` / `PLACE_MAP`）、日期解析（`to_iso_date`）、去重（`dedupe_place_rows`）都在 08 normalizer。

## 5. Blueprint API

| 方法 | 路径 | 行为 |
|---|---|---|
| GET  | `/places/` | 测试 UI |
| GET  | `/places/people/<doc_id>/<page>` | 该页已识别主体 |
| POST | `/places/run-single/<doc_id>/<page>/<n>` | 单人 places 抽取 |
| POST | `/places/run-page/<doc_id>/<page>` | 该页所有主体 |
| POST | `/places/run-all/<doc_id>` | 整 doc 异步 |

## 6. CLI

```bash
python -m modules.place_extractor.cli \
  --in_dir /data/ocr_text/myDoc \
  --inter_dir /data/intermediate/myDoc \
  --out_dir /data/intermediate/myDoc \
  --model qwen2.5:14b-instruct
```

## 7. 测试 UI 设计

```
┌──────────────────────────────────────────────────────────────────────┐
│ Doc: [ myDoc ▼ ]  Page: [ p012 ▼ ]  Person: [ Mariam bint Y. ▼ ]     │
│ [ Extract places for this person ]                                    │
├──────────────────────────────────────────────────────────────────────┤
│  Route visualization (ordered cards with arrows)                       │
│                                                                        │
│   ┌──────────────┐  →  ┌──────────────┐  →  ┌──────────────┐          │
│   │ 1. Zanzibar  │     │ 2. Mekran    │     │ 3. Dubai     │          │
│   │ (birthplace) │     │ 1931-02      │     │ 🟢 1931-05-17│          │
│   │ ░ ░ ░        │     │ ⚠ derived    │     │ ✓ explicit   │          │
│   │ "native of   │     │ "taken to    │     │ "arriving    │          │
│   │  Zanzibar"   │     │  Mekran"     │     │  Dubai about │          │
│   │              │     │              │     │  17th May"   │          │
│   └──────────────┘     └──────────────┘     └──────────────┘          │
│                                                                        │
│  ─── Background mentions (order=0) ───                                │
│   ┌──────────────┐                                                    │
│   │ 0. Bushehr   │  "forwarded from Bushehr Agency"                   │
│   │              │                                                    │
│   └──────────────┘                                                    │
├──────────────────────────────────────────────────────────────────────┤
│ Stage results (tabbed)                                                │
│  [ Candidates (6) ] [ Verified (4) ] [ Reconciled (4) ]               │
│   → 每 tab 是表格，显示 place / order / date / evidence                │
├──────────────────────────────────────────────────────────────────────┤
│ OCR text with all extracted places highlighted (color by date conf)   │
│ ┌──────────────────────────────────────────────────────────────────┐ │
│ │ Statement of slave Mariam bint Yusuf, native of 🟩Zanzibar.       │ │
│ │ Kidnapped at age 10 and taken to 🟨Mekran for about five years.   │ │
│ │ Arrived at 🟩Dubai about the 17th May 1931, having been forwarded │ │
│ │ from ⬜Bushehr Agency. H.M.S. Shoreham transported her...          │ │  ← ship not highlighted
│ └──────────────────────────────────────────────────────────────────┘ │
│ 🟩 explicit date   🟨 derived_from_doc   ⬜ no date / background      │
├──────────────────────────────────────────────────────────────────────┤
│ Validation                                                            │
│ ✓ Positive orders form 1..3 consecutive                               │
│ ✓ No duplicate places                                                 │
│ ✓ Dates ascending with order                                          │
│ ✓ No ships / office generic words                                     │
└──────────────────────────────────────────────────────────────────────┘
```

**可视化要点**（这是本模块最重要的 UI）：

1. **有序路径卡片链**：用箭头连起来表达迁移路径，背景关联单独放下方。肉眼看路径最直观。
2. **日期置信度色彩编码**：explicit=绿、derived=黄、unknown=灰。一眼看数据可信度。
3. **三轮 tab**：能切到 candidates 看噪声、切到 verified 看裁决效果、切到 reconciled 看最终。
4. **原文按地点高亮**：同色编码。船名、"office" 等不应抽的词不高亮 —— 如果高亮了就说明模块 bug。
5. **Validation 面板**：校验规则显式展示，失败项显红色。

## 8. Docker

共用 `docker/ner.Dockerfile`。Compose 端口 5107。

## 9. 测试

**单元测试**：
- `reconcile_place_rows` 对各种 candidates 的排序
- `verify_place_rows_need_retry` 对各种校验失败情况
- `infer_forwarding_transport_rows` 对 "from X, arriving Y" 模式
- `dedupe_place_rows` 合并逻辑

**集成测试**：
- `single_place.txt`：期望 1 地点 order=1
- `multi_route.txt`：期望多地点顺序正确
- `ambiguous.txt`：期望噪声（船名、owner 地）被剔除

## 10. 性能

3 轮 LLM + 可能 1 次 verifier retry + 可选 date enrich = 每人 4-6 次调用 × 页面人数。一页多人会很慢，按需考虑并发。

## 11. 构建检查清单

- [ ] 3 个 prompt 抽到文件
- [ ] `reconcile_place_rows` 规则完整
- [ ] `verify_place_rows_need_retry` 校验全面
- [ ] UI 路径卡片链可视化
- [ ] 日期置信度色彩编码
- [ ] 三轮 tab 切换
- [ ] 原文 place 高亮（不含 ship、office）
- [ ] Validation 面板显示
- [ ] 3 个 fixture 通过
