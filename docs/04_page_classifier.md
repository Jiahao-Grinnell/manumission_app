# 模块 04 — page_classifier

> 决定一页该不该抽、抽的话是什么报告类型。流水线里最简单的 LLM 模块，但决定了下游处理路径。

## 1. 目的

给定一页 OCR 文本，一次 LLM 调用输出：
- `should_extract: bool` — 抽 or 跳
- `skip_reason: "index" | "record_metadata" | "bad_ocr" | null`
- `report_type: "statement" | "transport/admin" | "correspondence"`
- `evidence: str` — 一段不超过 25 词的引用

这是所有下游模块的"门禁"。判错 extract/skip 会导致要么漏抽要么白抽。

## 2. 输入 / 输出

**输入**：`data/ocr_text/<doc_id>/p*.txt`

**输出**：`data/intermediate/<doc_id>/p*.classify.json`
```json
{
  "page": 12,
  "should_extract": true,
  "skip_reason": null,
  "report_type": "statement",
  "evidence": "Statement of slave Mariam bint Yusuf, aged about 20",
  "model_calls": 1,
  "repair_calls": 0,
  "elapsed_seconds": 3.4
}
```

## 3. 核心算法

继承原代码 `model_page_decision`：

```python
def classify(ocr: str, stats: CallStats, *, report_type_override=None) -> PageDecision:
    if report_type_override:
        return PageDecision(True, None, choose_report_type(report_type_override), "override")
    schema = '{"should_extract":true,"skip_reason":null,"report_type":"statement","evidence":"..."}'
    obj = client.generate_json(render(PAGE_CLASSIFY_PROMPT, ocr=ocr), schema, stats, num_predict=500)
    decision = parse_page_decision(obj)
    # 后置正则校正：某些强信号不容模型弄错
    decision.report_type = override_report_type_from_ocr(ocr, decision.report_type)
    return decision
```

`override_report_type_from_ocr` 是重要的兜底：当原文明显匹配 `"Statement of"` / `"repatriation"` / `"certificate delivered"` 等关键 pattern 时，强制覆盖模型判断（模型偶尔会把明显是 statement 的页判成 correspondence）。

Prompt 从 `config/prompts/page_classify.txt` 加载（原 `PAGE_CLASSIFY_PROMPT` 原样搬）。

## 4. 目录结构

```
src/modules/page_classifier/
├── __init__.py
├── core.py              # classify()
├── rules.py             # STATEMENT_REPORT_PAT / TRANSPORT_ADMIN_REPORT_PAT 等正则
├── parsing.py           # parse_page_decision()
├── blueprint.py
├── standalone.py
├── cli.py
├── templates/
│   └── ui.html
└── tests/
    ├── test_rules.py
    ├── test_parsing.py
    └── fixtures/
        ├── statement_page.txt
        ├── transport_page.txt
        ├── index_page.txt
        └── bad_ocr_page.txt
```

## 5. Blueprint API

| 方法 | 路径 | 行为 |
|---|---|---|
| GET  | `/classify/` | 测试 UI |
| GET  | `/classify/docs` | 可分类的 doc_id 列表（已完成 OCR 的） |
| GET  | `/classify/pages/<doc_id>` | 该 doc 所有 OCR 完成的页 |
| POST | `/classify/run-single/<doc_id>/<page>` | 单页分类 + 返回结果 |
| POST | `/classify/run-all/<doc_id>` | 整 doc 异步批跑 |
| GET  | `/classify/result/<doc_id>/<page>` | 已有结果 |

## 6. CLI

```bash
python -m modules.page_classifier.cli \
  --in_dir /data/ocr_text/myDoc \
  --out_dir /data/intermediate/myDoc \
  --model qwen2.5:14b-instruct \
  [--report-type statement]      # 强制覆盖（调试用）
```

## 7. 测试 UI 设计

```
┌─────────────────────────────────────────────────────────────────┐
│  Doc: [ myDoc ▼ ]   Page: [ p012 ▼ ]                            │
│  [ Classify this page ]                                          │
├─────────────────────────────────────────────────────────────────┤
│  ┌─ Verdict ───────────────────────────────────────────────────┐│
│  │ should_extract:  ✅ YES                                       ││
│  │ report_type:     ┃ STATEMENT ┃  (badge)                      ││
│  │ skip_reason:     —                                           ││
│  │ evidence:        "Statement of slave Mariam bint Yusuf..."   ││
│  │ model_calls:     1   repair_calls: 0   elapsed: 3.4s         ││
│  └──────────────────────────────────────────────────────────────┘│
├─────────────────────────────────────────────────────────────────┤
│  Regex override check                                            │
│  STATEMENT pattern:     ✓ matched  → would override → no change  │
│  TRANSPORT/ADMIN:       ✗ not matched                            │
├─────────────────────────────────────────────────────────────────┤
│  ┌─ OCR text ──────────────────────────────────────────────────┐│
│  │ Statement of slave Mariam bint Yusuf, aged about 20 years, ▓││  ← evidence 高亮
│  │ a native of Zanzibar, who states:—                           ││
│  │ I was kidnapped when I was about 10 years old from my...     ││
│  │ ...                                                          ││
│  └──────────────────────────────────────────────────────────────┘│
├─────────────────────────────────────────────────────────────────┤
│  [ Raw model response ▼ ]                                        │
│  {                                                               │
│    "should_extract": true,                                       │
│    "skip_reason": null,                                          │
│    ...                                                           │
│  }                                                               │
└─────────────────────────────────────────────────────────────────┘
```

**可视化要点**：
- **Verdict 徽章**：不同 report_type 不同颜色（statement 绿 / transport 蓝 / correspondence 灰）
- **evidence 在原文里高亮**：用字符串模糊匹配定位，找不到时提示 "evidence not located in text"
- **Regex override 对比**：如果规则和模型判断不一致要显眼提示
- **Raw response 折叠**：偶尔需要看原始 JSON debug

## 8. Docker

`docker/ner.Dockerfile`（所有 NER 模块共用一个镜像）：
```dockerfile
FROM llm-pipeline-base:latest
COPY src/modules/page_classifier /app/modules/page_classifier
COPY src/modules/name_extractor /app/modules/name_extractor
COPY src/modules/metadata_extractor /app/modules/metadata_extractor
COPY src/modules/place_extractor /app/modules/place_extractor
COPY src/modules/normalizer /app/modules/normalizer
COPY config/prompts /app/config/prompts
USER 10001:10001
```

Compose：
```yaml
  page_classifier:
    build:
      context: .
      dockerfile: docker/ner.Dockerfile
    depends_on:
      ollama:
        condition: service_healthy
    networks: [ llm_internal ]
    volumes:
      - ./data/ocr_text:/data/ocr_text:ro
      - ./data/intermediate:/data/intermediate
    profiles: [ "standalone", "all" ]
    command: >
      gunicorn -b 0.0.0.0:5104 -w 1 --timeout 600
      'modules.page_classifier.standalone:create_app()'
```

## 9. 测试

**单元测试**：
- `parse_page_decision` 对正确 JSON、缺字段、非法 report_type 的行为
- `override_report_type_from_ocr` 三组 fixture 分别匹配 STATEMENT / TRANSPORT / 无匹配

**集成测试**：
- 4 个 fixture（statement / transport / index / bad_ocr）各自跑 `classify()`，断言输出类别

## 10. 构建检查清单

- [ ] Prompt 抽到文件、`render_prompt` 正确注入
- [ ] `choose_report_type` 支持 `LEGACY_REPORT_TYPE_MAP` 回退
- [ ] `override_report_type_from_ocr` 覆盖逻辑正确
- [ ] 支持 `--report-type` 强制覆盖（调试用）
- [ ] 测试 UI 能看到 verdict + evidence 高亮 + 规则对比
- [ ] 4 个 fixture 测试都通过
