# Name Extractor V2.1 — 面向 Mistral 的、Span-Grounded 的、适合批量 PDF 的重设计方案

## Summary

Claude 的方案里最值得借鉴的是：**NER 和 role classification 分离、evidence span 校验、score-based arbitration、calibration/holdout、side-by-side validation**。这些点比我之前的方案更系统。

但我不会完全照搬。主要调整如下：

- 不把“每个 candidate 都一次 LLM call”作为默认流程；几十个同类 PDF 会太重。改成**按 segment 批量 role labeling，疑难 candidate 再单独 escalation**。
- 不要求 relation label 的 anchor 本人也是 subject。`Salem's sister` 只要求 `Salem` 是 OCR 中可定位的人名锚点，且 sister 本人是 subject；否则会误删合法 relation subject。
- 不过早依赖学习出来的权重。先用 deterministic gates + 可配置 score，后续再用 labeled calibration set 调权。
- 不引入 per-PDF subject dictionary 作为 v1 final 依据，因为当前原则是每页独立。它最多作为 review hint，不用于自动 keep。

最终设计是：**候选由 OCR span 约束，Mistral 只做局部角色标签，Python 用可审计的 score/decision table 最终裁决。**

## Pipeline

```text
OCR + classify.json
  -> Stage 0 Preflight
  -> Stage 1 Segmentation
  -> Stage 2 Hybrid Candidate Mining
  -> Stage 3 Span + Evidence Gate
  -> Stage 4 Merge / Canonicalization
  -> Stage 5 Snippet Builder
  -> Stage 6 Segment-Batched Role Labeling
  -> Stage 7 Hard-Case Escalation
  -> Stage 8 Deterministic Signals + Score Arbitration
  -> Stage 9 Relation-Label Resolution
  -> Stage 10 Page Consolidation
  -> names.json
```

External output 保持兼容：

```json
{
  "named_people": [
    {"name": "Sa'idah", "evidence": "his sister Sa'idah had been sold"}
  ],
  "removed_candidates": [],
  "final_reasons": [],
  "passes": {}
}
```

## Stage 0 — Preflight

- 输入保持不变：
  - `data/ocr_text/<doc_id>/pNNN.txt`
  - `data/intermediate/<doc_id>/pNNN.classify.json`
- 如果 `classify.should_extract=false`，`name_extractor` 不运行。
- 如果 `should_extract=true`，读取 OCR 和 classify context。
- `report_type` 只作为辅助上下文；它不能强行把 candidate 放进 final output。
- 每页独立处理。不为了自动 final names 做跨页继承。

## Stage 1 — Segmentation

在 candidate extraction 之前先构造稳定的局部上下文。

Segment 类型：

- `header`：页面前 200-300 个字符。
- `paragraph`：按空行切分。
- `numbered_case`：按 `1.`, `2.`, `(1)`, `(2)` 这类案件编号切分。
- `line`：适合 `Name. X`、列表、证书行。
- `window`：candidate span 周围的 fallback context。

Candidate role context 的优先级：

```text
numbered_case > paragraph > window > line
```

每个 segment 存储：

```json
{
  "segment_id": "case_3",
  "type": "numbered_case",
  "start": 1210,
  "end": 1850,
  "text": "..."
}
```

## Stage 2 — Hybrid Candidate Mining

目标：高 recall，但暂时不做 role judgment。

候选来源取并集：

- 从当前 `rules.py` seed 逻辑发展来的 deterministic regex mining：
  - `slave named X`
  - `negro by name X`
  - `Statement of X`
  - `Name. X`
  - `certain X negro`
  - `slaves whose names are`
  - numbered subject lists
  - `boy X, original name ...`
  - relation labels，例如 `Salem's sister`、`son of Salem`
- LLM recall-only mention scan：
  - 一次短的 page-level Mistral call。
  - 任务：只列出页面可见的人名和可见的 relation-label phrases。
  - 必须返回包含每个 candidate 的 verbatim quote。
  - 不允许分类 subject / non-subject。
- Optional dictionary hint：
  - v1 不用于 automatic keep。
  - 如果之前 confirmed 的 name 在当前页再次出现，可以存成 `review_hint`。

Candidate object：

```json
{
  "candidate_id": "cand_001",
  "raw_name": "Sa'idah",
  "canonical_name": "Sa'idah",
  "kind": "person",
  "sources": ["regex:name_field", "llm_mention_scan"],
  "span_quote": "his sister Sa'idah had been sold",
  "source_segment_id": "para_4"
}
```

Relation candidate object：

```json
{
  "candidate_id": "cand_010",
  "raw_name": "Salem's sister",
  "canonical_name": "Salem's sister",
  "kind": "relation_label",
  "anchor": "Salem",
  "relation": "sister",
  "sources": ["regex:possessive_relation"]
}
```

## Stage 3 — Span + Evidence Gate

这是主要的 hallucination defense。

必须检查：

- Candidate canonical name 必须能用 OCR-tolerant regex 在当前 OCR 中找到。
- LLM 提供的 `span_quote` 必须在 whitespace normalization 后是 OCR 的 substring。
- 之后 Mistral 产出的 role evidence 也必须能在 candidate snippet 中验证。

立即删除：

- name absent from OCR -> `absent_from_ocr`
- quote absent from OCR -> `evidence_absent_from_ocr`
- generic unanchored phrase -> `generic_unanchored`

会在这里删除的例子：

- 页面 OCR 中不存在 `Mubarak` 时的 `Mubarak`。
- `The Slave`
- `his sister`
- `three slaves`
- `a number of slaves`

Relation-label rule：

- `Salem's sister` 本身必须 OCR-visible。
- 不把 `his sister` 改写成 `Salem's sister`。
- Anchor 只需要是有效的、OCR-visible 的人名；anchor 不需要是 subject。

## Stage 4 — Merge / Canonicalization

合并重复候选，但保留不同 subject。

Canonicalization：

- `Salem slave of Mohamed...` -> `Salem`
- `Abdulla, son of` -> `Abdulla`
- `Nuru, original name Gangaram...` -> `Nuru`
- 保留 `Abdulla's son`
- 保留 `son of Salem`

Merge rules：

- 只在 normalized names 很可能指向同一 subject，且 spans 接近或重叠时合并。
- 优先保留更完整的人名：`Sarpor bin Salim` 优先于 `Sarpor`。
- 不合并 `Salem` 和 `Salem's sister`。
- 不合并 person 和 relation label，除非同一局部 segment 明确出现 `his sister Sa'idah`；这种情况下优先保留完整人名 `Sa'idah`。

## Stage 5 — Snippet Builder

为 role labeling 构造聚焦上下文。

对每个 candidate：

```json
{
  "candidate_id": "cand_001",
  "name": "Sa'idah",
  "snippet": "... Khaimullah and his sister Sa'idah ... sold ... kept as a slave ...",
  "snippet_span": [1100, 1500],
  "candidate_span_in_snippet": [72, 79],
  "segment_type": "paragraph"
}
```

Snippet construction：

- Memorandum 页面用 numbered case segment。
- 普通 prose 用 paragraph segment。
- List/table-like 页面用 line + nearby lines。
- 如果 segment 太长，就围绕 candidate 截断。
- 如果 candidate 出现多次，为每个 mention 生成一个 snippet，之后再聚合 labels。

## Stage 6 — Segment-Batched Role Labeling

默认 Mistral call 既不是整页级别，也不是每个 candidate 一次；而是**按 segment batch**。

原因：

- 整页会导致 role mixing。
- 每个 candidate 一次最干净，但面对几十个 PDF 成本太高。
- Segment batch 是最佳折中：上下文短，多个 candidate 只有在共享同一段局部文本时才一起判断。

Prompt contract：

- 输入：一个 segment/snippet 和其中的 candidate IDs。
- Mistral 只能给提供的 candidate IDs 分配 roles。
- Mistral 不能新增 names。
- Evidence quote 必须来自 snippet。

Allowed positive roles：

```text
enslaved_subject
refugee_slave
fugitive_slave
manumission_applicant
certificate_recipient
kidnapped_victim
recovered_person
repatriated_person
slave_status_investigation_subject
relation_subject
```

Allowed negative roles：

```text
owner
master
buyer
seller
broker
kidnapper
witness
official
correspondent
signatory
papers_source
family_member_only
freeborn_not_slave
generic_unanchored
```

Neutral：

```text
ambiguous
```

Output：

```json
{
  "labels": [
    {
      "candidate_id": "cand_001",
      "role": "kidnapped_victim",
      "confidence": "high",
      "evidence_quote": "his sister Sa'idah had been sold"
    }
  ]
}
```

Validation：

- Unsupported role -> discarded。
- New candidate name -> ignored。
- Evidence quote 不在 snippet 中 -> `role_evidence_invalid=true`。
- Bad JSON -> 标记 batch failed，并依靠 deterministic signals 和必要的 escalation。

## Stage 7 — Hard-Case Escalation

Escalation 只对 extra compute 有价值的 candidate 运行。

触发条件：

- Mistral role 是 `ambiguous`。
- confidence 是 `low`。
- Mistral positive role 和 strong deterministic negative 冲突。
- Mistral negative role 和 strong deterministic positive 冲突。
- Evidence quote invalid。
- Candidate 是 relation label，且 relation subject 状态不清楚。

默认 escalation choice：

- 使用第二个 Mistral prompt，temperature 仍为 0。
- 要求从同一个 snippet 中识别 role cues，并输出 final enum label。
- 不要求模型自由输出 final names。
- v1 不使用 stochastic self-consistency。
- v1 不使用 qwen cross-check，除非后续 calibration 证明有必要。

Escalation output 只有在以下条件满足时才覆盖 Stage 6：

- role valid，
- evidence quote validates，
- confidence 是 medium/high。

否则 candidate 进入 deterministic scoring，并很可能进入 review/drop。

## Stage 8 — Deterministic Signals + Score Arbitration

这一阶段结合 regex signals 和 Mistral role labels。

Mandatory gates：

- `absent_from_ocr` -> drop
- `generic_unanchored` -> drop
- `freeborn_not_slave` with local direct evidence -> drop unless reviewed manually
- `role_evidence_invalid` 不自动 drop，但会强烈扣分

Deterministic positive signals：

- `statement of slave {name}`
- `slave named {name}`
- `{name}, the slave of ...`
- `case of the negro {name}`
- `slaves whose names are ... {name}`
- `{name} requests repatriation`
- `{name} ... manumission certificate`
- relation label + direct slavery/kidnapping/sale/recovery/release cue

Deterministic negative signals：

- `sold to {name}`
- `sold by {name}`
- `slave of a man named {name}`
- `owner/master named {name}`
- `papers from {name}`
- `witnesses ... {name}`
- `people who know ... {name}`
- `letter from {name}`
- `statement recorded by {name}`
- `servant/not a slave`

Initial scoring：

```text
role_subject_high        +3.0
role_subject_medium      +2.0
role_subject_low         +1.0
strong_positive_regex    +2.0 each
weak_positive_regex      +0.75 each
relation_subject_signal  +2.0
negative_role            -3.0
hard_negative_regex      -4.0 each
soft_negative_regex      -1.5 each
invalid_role_evidence    -2.0
dictionary_review_hint   +0.25, review only
```

Default thresholds：

```text
score >= 2.5       -> keep
score <= -1.0      -> drop
otherwise          -> review_queue, excluded from final CSV
```

Conflict policy：

- Hard negative + no strong direct positive -> drop。
- Hard negative + strong direct positive -> 默认进 review queue，不进 final CSV。
- Multiple independent subject positives + high Mistral subject role -> keep，除非 hard negative 是 direct and local。
- Final CSV precision 优先于 recall。

## Stage 9 — Relation-Label Resolution

Relation labels 是 first-class candidates。

Keep relation label 的条件：

- phrase 本身 OCR-visible，
- anchor 是 valid OCR-visible personal name，
- relation label 自己的 snippet 显示 relation person 是 subject，
- score 达到 keep threshold。

不要求 anchor 本身是 subject。

Examples：

- `Salem's sister was kidnapped and sold` -> keep `Salem's sister`
- `son of Salem was recovered` -> keep `son of Salem`
- `people who know Salem's sister` -> drop/review
- `his sister was sold` -> drop，因为没有 named anchor
- `Salem has a son named Matook` -> drop Matook，除非 Matook 本人明确是 subject

## Stage 10 — Page Consolidation

Scoring 之后：

- 只保留 final candidates。
- 对 same role 且 nearby spans 的 variants 做 dedupe。
- 保留同一页面上的多个不同 subjects。
- Review queue 留在 `passes.review_queue`，不进入 `named_people`。
- 在 audit trail 中保存每个 signal 和 score contribution。

Final kept reason：

```json
{
  "name": "Sa'idah",
  "stage": "decision",
  "reason_type": "kept_subject_score",
  "score": 5.0,
  "signals": [
    {"type": "role_subject_high", "weight": 3.0},
    {"type": "strong_positive_regex", "weight": 2.0}
  ],
  "excerpt": "his sister Sa'idah had been sold"
}
```

Final removed reason：

```json
{
  "name": "Rashid bin Muhammad",
  "stage": "decision",
  "reason_type": "hard_negative_role",
  "score": -4.5,
  "signals": [
    {"type": "hard_negative_regex:sold_to", "weight": -4.0},
    {"type": "negative_role:buyer", "weight": -3.0}
  ],
  "excerpt": "sold to Rashid bin Muhammad"
}
```

## Internal Artifact Shape

External schema 保持兼容，丰富 `passes`。

```json
{
  "passes": {
    "segments": {},
    "candidate_mining": {},
    "span_gate": {},
    "merge": {},
    "snippet_build": {},
    "role_label": {},
    "escalation": {},
    "deterministic_signals": {},
    "decision": {},
    "review_queue": {}
  }
}
```

现有 UI/debug 行为应该能展示：

- candidates by source，
- span validity，
- role label result，
- deterministic signals，
- score，
- final decision，
- review queue。

## Calibration And Holdout

在信任几十个 PDF 批量跑之前，必须做 calibration。

Calibration set：

- Existing fixtures from `name_extractor/tests/fixtures`。
- Current known full-input pages。
- 增加 labeled examples：
  - statement subject
  - grouped slave list
  - memorandum victims/traffickers
  - witness/proof pages
  - relation labels
  - generic unnamed groups
  - freeborn/not slave
  - owner/master/buyer transfer chains

Holdout set：

- 从其他同格式 PDF 中抽 60-80 页。
- 不用 holdout 调 thresholds。
- 只用它比较 current pipeline vs V2.1。

Metrics：

- subject precision
- subject recall
- per-page exact match
- false positive owner/witness/buyer count
- false negative named subject count
- relation-label precision/recall
- ambiguous review queue rate

Acceptance criteria：

- Existing regression fixtures 不退化。
- Holdout F1 优于 current pipeline。
- Owner/witness/buyer false positives 下降。
- Relation-label cases 不被静默删除。
- 每个 final name 都有 OCR span 和 validated evidence。

## Implementation Plan

Phase 1: Side-by-side V2 scaffold

- 新增 V2 modules，不删除 current pipeline。
- 保持当前 `extract_names()` contract。
- 增加可选 internal switch 启用 V2，但旧行为保留。
- 新增 prompt folder：`config/prompts/name_extractor/v2/`。

Phase 2: Candidate + span foundation

- 实现 segmentation、candidate mining、span gate、canonical merge。
- 用现有 fixtures 确保旧的 known fixes 不回退。
- 暂时不需要 Mistral role labeling。

Phase 3: Role labeling + escalation

- 新增 segment-batched role-label prompt。
- 新增 evidence quote validation。
- 只对 hard cases 做 escalation。

Phase 4: Scoring decision layer

- 新增 deterministic signal extraction。
- 新增 score config。
- 生成 final `named_people`、`removed_candidates`、`final_reasons` 和 `review_queue`。

Phase 5: Calibration

- 构建 calibration labels。
- 调整 weights/thresholds。
- Freeze config。
- 在 holdout 上和 current pipeline 做对比。

## Explicit Choices

- 默认使用 segment-batched role labeling，不使用 per-candidate calls。
- hard cases 使用一个 deterministic escalation prompt，不在 v1 使用 self-consistency。
- v1 不把 cross-page dictionary 用于 automatic final decisions。
- 不要求 relation anchor 是 subject。
- Ambiguous candidates 不进入 final CSV，只保留在 review。
- Output schema 保持与当前 metadata/place/aggregator modules 兼容。

## Assumptions

- `mistral-small3.1:latest` 是 active text model。
- OCR text 被视为足够可信的 source of truth。
- Extraction boundary 仍然是 current page。
- Correctness 比 latency 更重要，但因为有很多同格式 PDF，batch cost 仍然需要控制。
- 本文档是未来 implementation 的设计方案；这里不包含代码修改。
