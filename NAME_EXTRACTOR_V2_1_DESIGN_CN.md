# Name Extractor V2.1 设计方案：面向 Mistral、Span-Grounded、适合批量 PDF

## Summary

这个方案综合了两类思路：

- Claude 方案中更系统的部分：NER 和 role classification 分离、evidence span 校验、score-based arbitration、calibration/holdout、side-by-side validation。
- 我们针对当前项目和 `mistral-small3.1:latest` 做的工程化收敛：不让模型自由生成最终姓名，不让每个 candidate 默认都单独调用一次 LLM，避免过早引入跨页推理。

核心原则是：

```text
候选必须来自当前页 OCR。
Mistral 只负责局部/段落级 role labeling。
Python 负责 span gate、score arbitration 和 final keep/drop。
Ambiguous candidate 不进最终 CSV，但保留在 review queue。
```

重要修正：不能只给模型固定 200 字的 candidate-local window。很多 subject 的身份需要结合上文的列表引导、编号案件段、段落主题或前后句。因此 Stage 5 从 `Snippet Builder` 升级为 **Context Bundle Builder**。

## Pipeline

```text
OCR + classify.json
  -> Stage 0 Preflight
  -> Stage 1 Segmentation
  -> Stage 2 Hybrid Candidate Mining
  -> Stage 3 Span + Evidence Gate
  -> Stage 4 Merge / Canonicalization
  -> Stage 5 Context Bundle Builder
  -> Stage 6 Context-Batched Role Labeling
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

Internal `passes` 会更丰富，但不改变下游 metadata/place/aggregator 使用的 public schema。

## Stage 0 — Preflight

输入保持不变：

- `data/ocr_text/<doc_id>/pNNN.txt`
- `data/intermediate/<doc_id>/pNNN.classify.json`

流程：

- 如果 `classify.should_extract=false`，`name_extractor` 不运行。
- 如果 `should_extract=true`，读取当前页 OCR 和 classify context。
- `report_type` 只作为辅助上下文，不直接决定 candidate keep/drop。
- 每页独立处理，不为了自动 final names 做跨页继承。
- OCR text 默认是当前页的 source of truth。

## Stage 1 — Segmentation

目标：在 candidate extraction 和 role labeling 前，先构造可复用的页面结构。

Segment 类型：

- `header`：页面前 200-300 个字符，用来捕捉 `Statement of...`、`Memorandum...` 等页面 framing。
- `paragraph`：按空行切分。
- `numbered_case`：按 `1.`, `2.`, `(1)`, `(2)` 等编号案件切分。
- `subject_list_block`：从 `slaves whose names are`、`following refugee slaves`、`for delivery to` 等引导语开始，到列表结束。
- `line`：适合 `Name. X`、列表、证书行。
- `window`：candidate span 周围 fallback context。

Candidate role context 优先级：

```text
numbered_case > subject_list_block > paragraph > expanded_window > line
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

这一步的关键是保留“支配 candidate 的上下文”，而不是只保留 candidate 附近几十个词。

## Stage 2 — Hybrid Candidate Mining

目标：高 recall，暂时不做 role judgment。

候选来源取并集：

- Deterministic regex mining：
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
  - 任务只列出页面可见的人名和可见的 relation-label phrases。
  - 必须返回包含每个 candidate 的 verbatim quote。
  - 不允许分类 subject / non-subject。
- Optional dictionary hint：
  - v1 不用于 automatic keep。
  - 如果 prior confirmed name 在当前页出现，只能存成 `review_hint`。

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

目标：彻底阻止 hallucinated name 和 hallucinated evidence。

必须检查：

- Candidate canonical name 必须能用 OCR-tolerant regex 在当前 OCR 中找到。
- LLM 提供的 `span_quote` 必须在 whitespace normalization 后是 OCR 的 substring。
- 后续 Mistral 产出的 role evidence 必须能在 context bundle 中验证。

立即删除：

- name absent from OCR -> `absent_from_ocr`
- quote absent from OCR -> `evidence_absent_from_ocr`
- generic unanchored phrase -> `generic_unanchored`

会在这里删除的例子：

- 当前页 OCR 中不存在 `Mubarak` 时的 `Mubarak`。
- `The Slave`
- `his sister`
- `three slaves`
- `a number of slaves`

Relation-label rule：

- `Salem's sister` 本身必须 OCR-visible。
- 不把 `his sister` 改写成 `Salem's sister`。
- Anchor 只需要是有效的、OCR-visible 的人名；anchor 不需要是 subject。

## Stage 4 — Merge / Canonicalization

目标：合并重复候选，但保留不同 subject。

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

## Stage 5 — Context Bundle Builder

目标：避免窄窗口导致需要上下文的名字被漏掉。

不是给每个 candidate 一个固定短 snippet，而是构造一个 **context bundle**。Bundle 包含 candidate 自身、局部窗口、支配它的段落或列表引导，以及必要的前后短上下文。

Context bundle 示例：

```json
{
  "candidate_id": "cand_001",
  "name": "Sa'idah",
  "candidate_span": [1234, 1241],
  "bundle_type": "paragraph_with_governing_context",
  "primary_context": "... Khaimullah and his sister Sa'idah ...",
  "governing_context": "Statement says the sister was kidnapped and sold...",
  "subject_intro": "",
  "previous_context": "...",
  "next_context": "... kept as a slave ...",
  "context_span": [980, 1510],
  "context_reason": "candidate appears in paragraph with subject sale/kidnapping context"
}
```

Context bundle 选择规则：

- 如果 candidate 在 numbered case 中，使用完整 numbered case；如果太长，再围绕 candidate 裁剪，但保留案件开头和 subject action。
- 如果 candidate 在 subject list 中，必须包含 list intro，例如 `slaves whose names are` 或 `following refugee slaves`。
- 如果 candidate 出现在普通 prose 中，使用 paragraph，并附加前后各一个短 segment，避免 subject 动作在前一句或后一句。
- 如果 candidate 是 `Name. X` 表格式字段，必须包含同一表格块内的 `Statement`、`Age`、`Caste`、`master`、`sold` 等字段。
- 如果 candidate 是 relation label，必须包含 relation phrase 本身和说明 relation person 是否被 kidnapped/sold/kept/recovered/released 的上下文。

Context budget：

- 默认 bundle 控制在模型可稳定处理的范围内。
- 优先保留：candidate phrase、subject-intro line、编号案件开头、含 slave/sold/kidnapped/manumission/recovered 的句子。
- 删除低价值内容：页脚、签名、重复行政套话。

这一步要解决的典型漏抽风险：

- `slaves whose names are` 在列表上方，名字附近没有 slave signal。
- Memorandum 案件中 subject action 在编号段开头，名字在后面。
- `his sister Sa'idah` 的 subject 身份来自下一句。
- `grant certificate to the following...` 在上一行，名字在下一行。
- 先给名字，后面用 `the woman` / `said girl` / `this person` 描述被卖或释放。

## Stage 6 — Context-Batched Role Labeling

默认 Mistral call 既不是整页级别，也不是每个 candidate 一次；而是按 context bundle 批量。

原因：

- 整页会导致 role mixing。
- 每个 candidate 一次最干净，但面对几十个 PDF 成本太高。
- Context batch 是最佳折中：模型看到足够 governing context，同时不会被整页干扰。

Batching 规则：

- 同一个 numbered case 内的 candidates 可以一起判断。
- 同一个 subject list block 内的 candidates 可以一起判断。
- 不同编号案件不能混在同一个 role-label call。
- 如果一个 bundle 中 roles 复杂或 candidate 太多，拆成更小 batch。

Prompt contract：

- 输入：一个 context bundle 或同一 segment 的多个 bundles，以及其中的 candidate IDs。
- Mistral 只能给提供的 candidate IDs 分配 roles。
- Mistral 不能新增 names。
- Evidence quote 必须来自 context bundle。

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
- Evidence quote 不在 context bundle 中 -> `role_evidence_invalid=true`。
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
- Candidate 来自 subject-list、numbered-case、relation-label seed，但 context-batched role labeling 没给出清晰 positive/negative。

默认 escalation choice：

- 使用第二个 Mistral prompt，temperature 仍为 0。
- 使用 expanded same-page context，而不是只用短 window。
- 要求从 context bundle 中识别 role cues，并输出 final enum label。
- 不要求模型自由输出 final names。
- v1 不使用 stochastic self-consistency。
- v1 不使用 qwen cross-check，除非后续 calibration 证明有必要。

Escalation output 只有在以下条件满足时才覆盖 Stage 6：

- role valid，
- evidence quote validates，
- confidence 是 medium/high。

否则 candidate 进入 deterministic scoring，并很可能进入 review/drop。

## Stage 8 — Deterministic Signals + Score Arbitration

这一阶段结合 regex signals、context bundle signals 和 Mistral role labels。

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
- candidate appears inside a validated subject-list block
- candidate appears inside a numbered case segment with subject action governing the segment

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
role_subject_high                  +3.0
role_subject_medium                +2.0
role_subject_low                   +1.0
strong_positive_regex              +2.0 each
weak_positive_regex                +0.75 each
validated_subject_list_context     +2.0
validated_numbered_case_context    +1.5
relation_subject_signal            +2.0
negative_role                      -3.0
hard_negative_regex                -4.0 each
soft_negative_regex                -1.5 each
invalid_role_evidence              -2.0
dictionary_review_hint             +0.25, review only
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
- Validated subject-list context 可以保留列表里的名字，即使 candidate local window 没有 slave signal。
- Validated numbered-case context 可以保留案件 victim，即使名字附近没有完整 subject phrase。
- Final CSV precision 优先于 recall。

## Stage 9 — Relation-Label Resolution

Relation labels 是 first-class candidates。

Keep relation label 的条件：

- phrase 本身 OCR-visible，
- anchor 是 valid OCR-visible personal name，
- relation label 自己的 context bundle 显示 relation person 是 subject，
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
    "context_bundle": {},
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
- context bundle reason，
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
  - context-dependent names where governing context is outside the local name window

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
- context-dependent subject recall
- ambiguous review queue rate

Acceptance criteria：

- Existing regression fixtures 不退化。
- Holdout F1 优于 current pipeline。
- Owner/witness/buyer false positives 下降。
- Relation-label cases 不被静默删除。
- 需要同页上下文才能判断的名字不因窄 window 被系统性漏掉。
- 每个 final name 都有 OCR span 和 validated evidence。

## Implementation Plan

Phase 1: Side-by-side V2 scaffold

- 新增 V2 modules，不删除 current pipeline。
- 保持当前 `extract_names()` contract。
- 增加可选 internal switch 启用 V2，但旧行为保留。
- 新增 prompt folder：`config/prompts/name_extractor/v2/`。

Phase 2: Candidate + span + context foundation

- 实现 segmentation、candidate mining、span gate、canonical merge。
- 实现 context bundle builder，覆盖 subject-list、numbered-case、paragraph、relation-label 等场景。
- 用现有 fixtures 确保旧的 known fixes 不回退。
- 暂时不需要 Mistral role labeling。

Phase 3: Role labeling + escalation

- 新增 context-batched role-label prompt。
- 新增 evidence quote validation。
- 只对 hard cases 做 expanded-context escalation。

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

- 默认使用 context-batched role labeling，不使用 per-candidate calls。
- hard cases 使用一个 deterministic escalation prompt，不在 v1 使用 self-consistency。
- v1 不把 cross-page dictionary 用于 automatic final decisions。
- 不要求 relation anchor 是 subject。
- Ambiguous candidates 不进入 final CSV，只保留在 review。
- Output schema 保持与当前 metadata/place/aggregator modules 兼容。
- 不使用固定 200 字局部窗口作为唯一上下文；必须保留支配 candidate 的同页上下文。

## Assumptions

- `mistral-small3.1:latest` 是 active text model。
- OCR text 被视为足够可信的 source of truth。
- Extraction boundary 仍然是 current page。
- Correctness 比 latency 更重要，但因为有很多同格式 PDF，batch cost 仍然需要控制。
- 本文档是未来 implementation 的设计方案；这里不包含代码修改。
