# 模块 06 — metadata_extractor

> 对每个已识别的主体，从当前页抽取 5 个字段的案件元数据。输出 `Detailed info.csv` 的一行。

## 1. 目的

给定 `(ocr_text, name, page, report_type)`，通过**单次 LLM 调用** + evidence 要求 + 规则校验，产出一个 detail 行：

| 字段 | 类型 | 允许值 |
|---|---|---|
| Name | str | 主体名 |
| Page | int | 页号 |
| Report Type | enum | `statement` / `transport/admin` / `correspondence` |
| Crime Type | enum | `kidnapping` / `sale` / `trafficking` / `illegal detention` / `forced transfer` / `debt-claim transfer` / "" |
| Whether abuse | enum | `yes` / `no` / "" |
| Conflict Type | enum | `manumission dispute` / `ownership dispute` / `debt dispute` / `free-status dispute` / `forced-transfer dispute` / `repatriation dispute` / `kidnapping case` / "" |
| Trial | enum | `manumission requested` / `manumission certificate requested` / `manumission recommended` / `manumission granted` / `free status confirmed` / `released` / `repatriation arranged` / `certificate delivered` / "" |
| Amount paid | str | 字面金额字符串 或 "" |

**关键约束**：模型必须为每个非空字段给出 evidence（≤25 词的原文引用）。没有 evidence 的推断一律丢弃。

## 2. 输入 / 输出

**输入**：
- `data/ocr_text/<doc_id>/p<N>.txt`
- `data/intermediate/<doc_id>/p<N>.classify.json`
- `data/intermediate/<doc_id>/p<N>.names.json`

**输出**：`data/intermediate/<doc_id>/p<N>.meta.json`
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

## 3. 核心算法（继承原代码 `model_meta_for_name`）

```python
def extract(ocr, name, page, report_type, stats) -> DetailRow:
    schema = '{"name":"...","page":0,"report_type":"...","crime_type":null,"whether_abuse":"",...}'
    obj = client.generate_json(
        render(META_PASS_PROMPT, name=name, page=page, report_type=report_type, ocr=ocr),
        schema, stats, num_predict=1000)
    return parse_meta(obj, name, page, report_type)
```

`parse_meta` 的职责：
- `choose_allowed(value, CRIME_TYPES)` — 不在白名单的值强制为 ""
- `choose_yes_no_blank` — whether_abuse 必须是 yes/no/""
- amount_paid 过滤 "null" / "none" 字面串为 ""

Prompt 从 `config/prompts/meta_pass.txt` 加载（原 `META_PASS_PROMPT` 原样搬）。

## 4. 目录结构

```
src/modules/metadata_extractor/
├── __init__.py
├── core.py              # extract()
├── vocab.py             # CRIME_TYPES / CONFLICT_TYPES / TRIAL_TYPES 等枚举
├── parsing.py           # parse_meta() + choose_allowed
├── blueprint.py
├── standalone.py
├── cli.py
├── templates/
│   └── ui.html
└── tests/
    ├── test_parsing.py
    └── fixtures/
        ├── kidnapping_abuse.txt
        ├── repatriation.txt
        └── certificate_grant.txt
```

枚举值放 `vocab.py` 并从 `config/schemas/vocab.yaml` 生成（YAML 是 source of truth，方便非程序员改）。

## 5. Blueprint API

| 方法 | 路径 | 行为 |
|---|---|---|
| GET  | `/meta/` | 测试 UI |
| GET  | `/meta/pages/<doc_id>` | 可抽 meta 的页（已有 names） |
| GET  | `/meta/people/<doc_id>/<page>` | 该页所有已识别主体 |
| POST | `/meta/run-single/<doc_id>/<page>/<name>` | 单人 meta 抽取 |
| POST | `/meta/run-page/<doc_id>/<page>` | 该页所有主体 |
| POST | `/meta/run-all/<doc_id>` | 整 doc 异步 |

## 6. CLI

```bash
python -m modules.metadata_extractor.cli \
  --in_dir /data/ocr_text/myDoc \
  --inter_dir /data/intermediate/myDoc \
  --out_dir /data/intermediate/myDoc \
  --model qwen2.5:14b-instruct
```

## 7. 测试 UI 设计

```
┌────────────────────────────────────────────────────────────────────┐
│ Doc: [ myDoc ▼ ]   Page: [ p012 ▼ ]   Person: [ Mariam b. Y. ▼ ]   │
│ [ Extract meta for this person ]    [ Extract for all on page ]    │
├────────────────────────────────────────────────────────────────────┤
│ Detail row for "Mariam bint Yusuf" on page 12                       │
│ ┌────────────────────────┐  ┌────────────────────────────────────┐ │
│ │ 🏷 Report Type          │  │ Evidence                           │ │
│ │    statement            │  │ (from page classifier)             │ │
│ ├────────────────────────┤  ├────────────────────────────────────┤ │
│ │ ⚖ Crime Type            │  │ "kidnapped when I was about 10    │ │
│ │    kidnapping           │  │  years old from my native place"   │ │
│ │                         │  │  [jump to in text]                 │ │
│ ├────────────────────────┤  ├────────────────────────────────────┤ │
│ │ 🚨 Whether abuse        │  │ "beaten severely by her owner"     │ │
│ │    yes                  │  │  [jump to in text]                 │ │
│ ├────────────────────────┤  ├────────────────────────────────────┤ │
│ │ ⚔ Conflict Type         │  │ —                                  │ │
│ │    (empty)              │  │                                    │ │
│ ├────────────────────────┤  ├────────────────────────────────────┤ │
│ │ 🏛 Trial                │  │ "requests manumission certificate" │ │
│ │    manumission requested│  │  [jump to in text]                 │ │
│ ├────────────────────────┤  ├────────────────────────────────────┤ │
│ │ 💰 Amount paid          │  │ —                                  │ │
│ │    (empty)              │  │                                    │ │
│ └────────────────────────┘  └────────────────────────────────────┘ │
├────────────────────────────────────────────────────────────────────┤
│ OCR text with all evidence spans highlighted in different colors    │
│ ┌────────────────────────────────────────────────────────────────┐ │
│ │ Statement of slave Mariam bint Yusuf, aged 20, native of       │ │
│ │ Zanzibar. She was 🟥kidnapped when I was about 10 years old    │ │
│ │ from my native place🟥 and sold to Sheikh Rashid of Dubai. She │ │
│ │ was 🟧beaten severely by her owner🟧 ... She now 🟩requests    │ │
│ │ manumission certificate🟩.                                      │ │
│ └────────────────────────────────────────────────────────────────┘ │
├────────────────────────────────────────────────────────────────────┤
│ Validation                                                          │
│ ✓ Crime Type is in allowed set                                      │
│ ✓ Whether abuse ∈ {yes,no,""}                                       │
│ ✓ Trial is in allowed set                                           │
│ ─ Conflict Type empty (no evidence)                                 │
│ ─ Amount paid empty (no evidence)                                   │
└────────────────────────────────────────────────────────────────────┘
```

**可视化要点**：
1. **字段卡片 + 配对 evidence**：左右对齐展示，一眼看到字段+证据对应关系
2. **不同字段 evidence 用不同颜色在原文高亮**：红=crime, 橙=abuse, 紫=conflict, 绿=trial, 金=amount
3. **跳转定位**：点 "[jump to in text]" 滚动到原文对应位置
4. **Validation 面板**：显示每个字段是否通过白名单校验
5. **空字段明确显示"—"**：区分 `""` 和 `null`

## 8. Docker

共用 `docker/ner.Dockerfile`。Compose 端口 5106。

## 9. 测试

**单元测试**：
- `choose_allowed` 白名单外的值清空
- `choose_yes_no_blank` 对各种输入的行为
- `parse_meta` 对缺字段、字段类型错的 JSON 的兜底

**集成测试**：
- 3 个 fixture 各测一个 meta 组合，期望输出特定字段

## 10. 构建检查清单

- [ ] Prompt 抽到文件
- [ ] Vocab 从 YAML 生成，避免硬编码
- [ ] `parse_meta` 白名单校验严格
- [ ] UI 字段卡片 + evidence 配对展示
- [ ] 原文多色高亮
- [ ] Validation 面板显示校验结果
- [ ] 3 个 fixture 测试通过
