# 模块 05 — name_extractor

> 从一页 OCR 文本里抽取所有**被奴役/解放的命名主体**。流水线里最复杂的 LLM 模块（4 轮 LLM + 规则过滤）。

## 1. 目的

给定一页 OCR 文本，产出该页涉及的"主体人名"列表。每个主体附 evidence。

"主体"的严格定义（继承原代码）：
- ✅ 包含：奴隶本人、refugee slave、fugitive slave、manumission 申请人、certificate 接收人、明确纳入主体组的家属
- ❌ 排除：主人、买家、卖家、酋长、船长、文书、签署人、自由人

这个区分**高度依赖上下文**，模型单次判断不可靠 → 用 4 轮 pass 加规则过滤兜底。

## 2. 输入 / 输出

**输入**：
- `data/ocr_text/<doc_id>/p<N>.txt`
- `data/intermediate/<doc_id>/p<N>.classify.json`（只对 `should_extract=true` 的页跑）

**输出**：`data/intermediate/<doc_id>/p<N>.names.json`
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

**把每一轮的中间结果都保留**是关键设计决策 —— UI 要展示它们，排查问题要它们。

## 3. 核心算法（继承原代码 `model_named_people`）

```
┌── pass 1: NAME_PASS_PROMPT       ──┐     高精度抽主体
│                                    │
│   LLM → raw candidates (pass1_raw) │
│                                    │
│   model_filter → pass1_filtered    │     filter 用 NAME_FILTER_PROMPT
│                                    │     从候选里保留真主体
├── pass 2: NAME_RECALL_PROMPT      ─┤     高召回捞漏掉的
│                                    │
│   LLM → raw candidates             │
│                                    │
│   model_filter → recall_filtered   │
│                                    │
├── merge(pass1_filtered, recall_f)  │     模糊匹配合并同人不同写法
│         → merged                   │
│                                    │
├── model_verify(merged)             │     NAME_VERIFY_PROMPT 最终裁决
│         → verified                 │
│                                    │
├── rule filter_named_people(ocr)    │     ROLE_NEGATIVE_PATTERNS 正则剔
│         → final                    │     ROLE_POSITIVE_PATTERNS 正则保
└────────────────────────────────────┘
```

每一步都是一次 LLM 调用（filter 两次、verify 一次，加上 pass1 和 recall 的原始两次，共 5 次基础调用；加上 JSON 修复重试可能到 6-7 次）。

规则过滤层（纯 Python，不耗 LLM）：
- `ROLE_NEGATIVE_PATTERNS`：匹配到 `"sold to {name}"` / `"bought by {name}"` / `"master {name}"` → 剔除
- `ROLE_POSITIVE_PATTERNS`：匹配到 `"slave {name}"` / `"refugee slaves ... {name}"` / `"statement of {name}"` → 保留
- `is_freeborn_not_slave_name`：上下文含 `"free born"` + `"not a slave"` → 剔除
- 名字本身规则：`is_valid_name`（长度 ≥2、含字母、不在 `NAME_STOPWORDS`）

## 4. 目录结构

```
src/modules/name_extractor/
├── __init__.py
├── core.py                # 编排 4 轮 + 规则
├── passes.py              # pass1 / recall / filter / verify 分别封装
├── rules.py               # 正负 pattern / NAME_STOPWORDS
├── merging.py             # names_maybe_same_person / merge_named_people / choose_preferred_name
├── blueprint.py
├── standalone.py
├── cli.py
├── templates/
│   └── ui.html
└── tests/
    ├── test_rules.py
    ├── test_merging.py
    └── fixtures/
        ├── single_subject.txt
        ├── grouped_list.txt
        ├── owner_vs_slave.txt    # 测负 pattern 能排除 owner
        └── freeborn_page.txt
```

**注意**：`merge_named_people` 和 `names_maybe_same_person` 属于规范化职责，**源代码放在 08 normalizer**，这个模块 import 使用。避免重复。

## 5. Blueprint API

| 方法 | 路径 | 行为 |
|---|---|---|
| GET  | `/names/` | 测试 UI |
| GET  | `/names/pages/<doc_id>` | 可抽取的页面（`should_extract=true`） |
| POST | `/names/run-single/<doc_id>/<page>` | 单页抽取，返回所有中间产物 |
| POST | `/names/run-all/<doc_id>` | 整 doc 异步 |
| GET  | `/names/result/<doc_id>/<page>` | 已有结果（含 passes） |
| POST | `/names/rerun-pass/<doc_id>/<page>/<pass_name>` | 只重跑某轮（调参用） |

`rerun-pass` 让你能在不动其他轮次的情况下单独优化某轮 prompt —— 对调试非常有用。

## 6. CLI

```bash
python -m modules.name_extractor.cli \
  --in_dir /data/ocr_text/myDoc \
  --classify_dir /data/intermediate/myDoc \
  --out_dir /data/intermediate/myDoc \
  --model qwen2.5:14b-instruct
```

## 7. 测试 UI 设计（**本项目里最大的可视化**）

```
┌────────────────────────────────────────────────────────────────────┐
│ Doc: [ myDoc ▼ ]   Page: [ p012 ▼ ]   [ Run ]   [ Re-run verify ]  │
├────────────────────────────────────────────────────────────────────┤
│  Stages (click any to expand raw prompt+response)                   │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌──────────────┐│
│  │ Pass 1 raw  │  │ Pass 1 filt │  │ Recall raw  │  │ Recall filt  ││
│  │  5 names    │→ │  4 names    │  │  6 names    │→ │  5 names     ││
│  └─────────────┘  └─────────────┘  └─────────────┘  └──────────────┘│
│           │                │             │                │         │
│           └────────────────┴─────┬───────┴────────────────┘         │
│                                  ▼                                   │
│                         ┌─────────────────┐                          │
│                         │  Merged: 6 ppl  │                          │
│                         └────────┬────────┘                          │
│                                  ▼                                   │
│                         ┌─────────────────┐                          │
│                         │ Verified: 5 ppl │                          │
│                         └────────┬────────┘                          │
│                                  ▼                                   │
│                         ┌─────────────────┐                          │
│                         │ Rule-filtered:  │                          │
│                         │  4 ppl final ✓  │                          │
│                         └─────────────────┘                          │
├────────────────────────────────────────────────────────────────────┤
│ OCR text (全文，主体高亮绿色，被剔除的候选高亮灰色带删除线)             │
│ ┌────────────────────────────────────────────────────────────────┐ │
│ │ Statement of slave ▓Mariam bint Yusuf▓ aged 20, native of      │ │
│ │ Zanzibar. She was kidnapped and sold to ░Sheikh Rashid░ of     │ │  ← grey strikethrough
│ │ Dubai. Refugee slaves ▓Ahmad bin Said▓, ▓Fatima bint Ali▓, and │ │
│ │ ▓Zaid bin Omar▓ request repatriation...                        │ │
│ └────────────────────────────────────────────────────────────────┘ │
├────────────────────────────────────────────────────────────────────┤
│ Final list (4 people)                                               │
│ ┌──────────────────────────────────────────────────────────────────┐│
│ │ Mariam bint Yusuf  │ "Statement of slave Mariam..."              ││
│ │ Ahmad bin Said     │ "Refugee slaves Ahmad bin Said..."          ││
│ │ Fatima bint Ali    │ "Refugee slaves ... Fatima bint Ali..."     ││
│ │ Zaid bin Omar      │ "Refugee slaves ... Zaid bin Omar..."       ││
│ └──────────────────────────────────────────────────────────────────┘│
├────────────────────────────────────────────────────────────────────┤
│ Dropped candidates with reasons                                     │
│ ┌──────────────────────────────────────────────────────────────────┐│
│ │ Sheikh Rashid     │ Rule: matched "sold to {name}" (negative)    ││
│ │ James Morrison    │ Verify: not in subject group                 ││
│ └──────────────────────────────────────────────────────────────────┘│
└────────────────────────────────────────────────────────────────────┘
```

**可视化要点**（**这些是本模块设计的灵魂**）：

1. **流程图一目了然**：5 个阶段卡片 + 箭头，每个卡片带计数、点击展开该轮的 prompt+response。
2. **全文高亮**：主体绿色高亮、被剔除候选灰色删除线。**这是 debug 过滤是否过严/过松的最快方法。**
3. **最终表格**：每人一行 + evidence。
4. **剔除解释表**：每个被剔除的候选必须配明确原因（哪轮剔的、规则名还是模型判断）。
5. **Re-run verify 按钮**：不重跑前面 4 轮，只重发 verify prompt —— 调 verify 的参数时省大量时间。

## 8. Docker

共用 `docker/ner.Dockerfile`（见 04 模块文档）。Compose 片段类似 04，端口 5105。

## 9. 测试

**单元测试**（纯规则，无 LLM）：
- `ROLE_NEGATIVE_PATTERNS` 对 "sold to X"、"bought by X"、"master X" 的匹配
- `ROLE_POSITIVE_PATTERNS` 对 "slave X"、"refugee slaves ... X" 的匹配
- `is_freeborn_not_slave_name` 的行为
- 名字合法性 `is_valid_name`（数字、太短、stopword）

**集成测试**（需 LLM）：
- `single_subject.txt`：期望输出 1 人
- `grouped_list.txt`：期望输出列表所有人
- `owner_vs_slave.txt`：期望 owner 被剔除、slave 被保留
- `freeborn_page.txt`：期望"free born not slave"的人被剔除

## 10. 性能

4 轮 LLM + 2 次 filter + 1 次 verify = 最少 5 次调用，常常 7+ 次（加 JSON 修复）。单页约 15-40 秒。

**优化空间**（不在 MVP 内）：
- pass1 和 recall 并行
- 把 filter 合并到 verify（少一次调用，但会降质量，需实验）

## 11. 构建检查清单

- [ ] 5 个 prompt 抽到文件
- [ ] 4 轮 + filter + verify + rule filter 全串通
- [ ] UI 5 个阶段都可视化
- [ ] 全文高亮工作正确（含模糊匹配）
- [ ] 剔除原因表完整
- [ ] 4 个 fixture 测试合理
- [ ] Re-run verify 单独端点可用
