# 模块 08 — normalizer

> 纯 Python 的规范化 / 验证 / 去重工具集。**被 05/06/07/09 依赖，不依赖任何 LLM**。

## 1. 目的

把原 `ner_extract.py` 散落在各处的"数据清洗"逻辑收口在一个模块里：

- **名字**规范化（大小写、accent、`bin`/`bint`/`ibn` 连接词处理）
- **地名**规范化 + 历史拼写映射（"shargah" → "Sharjah"）+ 船名剥离
- **日期**解析（多种写法 → ISO 8601）
- **名字同一性**判断（模糊匹配，同人不同拼法）
- **去重**（place rows 合并，保留最佳 order/date 信息）
- **Evidence 清洗**（截断到 25 词、normalize）

这些逻辑独立于 LLM、独立于 Flask、纯函数。测试极容易写，也是整条流水线最**确定性**的部分。

## 2. 为什么单独列成模块

有两个原因：
1. **被 4+ 个模块共用**，必须抽出来。
2. **可视化价值大**：名字/地名/日期的规范化规则复杂（原代码 `normalize_name` 几十行，`to_iso_date` 应付 5 种日期写法），需要一个交互式 UI 让使用者随时试规则。

所以它同时是**库**（被 import）和**服务**（有自己的 UI），但是**没有独立容器**（只在主 web_app 里挂 blueprint）。

## 3. 目录结构

```
src/modules/normalizer/
├── __init__.py
├── names.py            # normalize_name / is_valid_name / names_maybe_same_person
│                       # merge_named_people / choose_preferred_name
│                       # name_compare_tokens / build_name_regex
├── places.py           # normalize_place / is_valid_place / PLACE_MAP
│                       # dedupe_place_rows / merge_place_date_enrichment
├── dates.py            # to_iso_date / parse_day_month / parse_first_date_in_text
│                       # extract_doc_year / MONTHS / ISO_DATE_PAT
├── evidence.py         # clean_evidence / normalize_for_match
├── vocabulary.py       # NAME_STOPWORDS / PLACE_STOPWORDS (从 config/schemas/vocab.yaml 加载)
├── blueprint.py        # /normalizer/ UI（只在主 web_app 挂）
├── templates/
│   └── ui.html
└── tests/
    ├── test_names.py
    ├── test_places.py
    ├── test_dates.py
    └── test_evidence.py
```

## 4. 关键 API

### 4.1 `names.py`
```python
normalize_name(s: str) -> str
# "  mariam   Bint   YUSUF  " → "Mariam bint Yusuf"
# strip accents, strip stopword prefix ("the slave X"), merge ws,
# title-case, preserve bin/bint/ibn→bin/al/el/ul lowercase

is_valid_name(name: str) -> bool
# 拒绝：含数字、长度<2、仅 stopword、无字母

names_maybe_same_person(a: str, b: str) -> bool
# 多策略：字面相等 / 首 token 匹配 + SequenceMatcher / token 重叠率 / 包含关系

merge_named_people(*groups) -> List[dict]
# 将多轮候选合并成唯一人员列表，每个人选最优写法

choose_preferred_name(items) -> dict
# 在同一人的多个写法中选字段最全的

build_name_regex(name: str) -> re.Pattern
# 构造允许中间标点/空白的匹配 pattern（供 "highlight in text" 用）
```

### 4.2 `places.py`
```python
normalize_place(s: str) -> str
# "ras ul khaimah" → "Ras al Khaimah"
# 用 PLACE_MAP 查历史拼写映射 + title case

is_valid_place(place: str) -> bool
# 拒绝：ship names (H.M.S., dhow, steamship), 办公室泛指, 含数字, 过长

dedupe_place_rows(rows, *, drop_internal=True) -> List[dict]
# 按 (Name, Place) 合并，保留最佳 Order/Arrival Date/Time Info
```

`PLACE_MAP` 字典继承原代码，也可从 `config/schemas/vocab.yaml` 加载以便运维修改：
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
# 返回 (iso_date, confidence)
# 支持：
#   "1931-05-17"               → ("1931-05-17", "explicit")
#   "17-5-1931"                → ("1931-05-17", "explicit")
#   "May 17, 1931"             → ("1931-05-17", "explicit")
#   "17th May 1931"            → ("1931-05-17", "explicit")
#   "17th May" + doc_year=1931 → ("1931-05-17", "derived_from_doc")
#   "some random text"         → ("", "")

parse_first_date_in_text(text, doc_year) -> (iso, conf, raw)
extract_doc_year(text) -> Optional[int]
```

### 4.4 `evidence.py`
```python
clean_evidence(s: str) -> str
# normalize whitespace + 截断到 25 词

normalize_for_match(s: str) -> str
# 全小写、accent 剥离、非字母数字 → 空格；用于模糊匹配 evidence 在原文中的位置
```

## 5. Blueprint（UI 专用）

| 方法 | 路径 | 行为 |
|---|---|---|
| GET  | `/normalizer/` | 测试 UI |
| POST | `/normalizer/normalize/name` | `{"raw":"..."}` → `{"normalized":"...","valid":true,"reason":""}` |
| POST | `/normalizer/normalize/place` | 同上 |
| POST | `/normalizer/normalize/date` | `{"raw":"...","doc_year":1931}` → `{"iso":"1931-05-17","confidence":"explicit","raw_matched":"17th May"}` |
| POST | `/normalizer/compare-names` | `{"a":"...","b":"..."}` → `{"same":true,"reason":"token overlap 0.83"}` |
| POST | `/normalizer/dedupe-places` | 贴一堆 rows → 返回去重后结果 |

## 6. 测试 UI 设计

一个单页应用，四个 tab：

```
┌────────────────────────────────────────────────────────────────┐
│  [Names] [Places] [Dates] [Compare names] [Dedupe places]      │
├────────────────────────────────────────────────────────────────┤
│  Names tab                                                     │
│                                                                │
│  Input:          Normalized:                                   │
│  ┌────────────┐  ┌────────────┐                                │
│  │Mariam BINT │  │Mariam bint │                                │
│  │  YUSUF     │→ │  Yusuf     │                                │
│  └────────────┘  └────────────┘                                │
│                                                                │
│  Valid: ✅ yes                                                  │
│  Transformations applied:                                      │
│   • strip accents                                              │
│   • merge whitespace                                           │
│   • strip "the slave" prefix: not matched                      │
│   • title case                                                 │
│   • keep connector "bint" lowercase                            │
│                                                                │
├────────────────────────────────────────────────────────────────┤
│  Dates tab                                                     │
│                                                                │
│  Input:            doc_year (optional):                        │
│  ┌────────────────┐ ┌──────┐                                   │
│  │ 17th May       │ │ 1931 │    [ Parse ]                      │
│  └────────────────┘ └──────┘                                   │
│                                                                │
│  Result:                                                       │
│   ISO:        1931-05-17                                       │
│   Confidence: derived_from_doc                                 │
│   Matched:    "17th May" (pattern #3)                          │
│                                                                │
│  Tried patterns:                                               │
│   ✗ ISO_DATE_PAT:       no match                               │
│   ✗ slash/dash:         no match                               │
│   ✗ "Month D, YYYY":    no match                               │
│   ✓ "D{ord} Month YYYY":match → fallback to doc_year           │
│   ✓ parse_day_month + doc_year: 17, 5                          │
└────────────────────────────────────────────────────────────────┘
```

**可视化要点**：
- **即时反馈**：输入框 `oninput` 触发，300ms debounce
- **规则命中可视化**：对 date parser 特别有用（它有 5 个 fallback）
- **Compare names** tab：两栏输入 + 对称显示 tokens + 重叠度进度条
- **Dedupe places** tab：粘贴 JSON/CSV → 展示去重前后的行数变化和合并对

这个 UI 对领域研究者（非开发者）特别有用 —— 他们可以自己验证"Bushire / Busheir / Bushehr 会被合并"。

## 7. Docker

**不独立存在**。这个模块作为 Python 包被 `docker/ner.Dockerfile` 里的 `COPY` 带进所有 NER 相关镜像；blueprint 在 `docker/web.Dockerfile` 的主 app 里挂载。

## 8. 测试

**单元测试覆盖率目标 ≥ 90%**（这层是可以做到的，无 LLM 无网络）：

- `test_names.py`：30+ 条 edge case（accent、connectors、OCR 错、纯数字）
- `test_places.py`：PLACE_MAP 每条一个测 + ship name 拒绝 + 泛指拒绝
- `test_dates.py`：每种日期格式一个测 + doc_year 回退
- `test_evidence.py`：超长截断 + 空白合并

```bash
pytest src/modules/normalizer/tests/ --cov=modules.normalizer
```

## 9. 重要约定

- **不抛异常**：所有函数对垃圾输入返回空字符串或 `None`，不让上层 try-except
- **纯函数**：无副作用、无 I/O
- **可 import**：被其他模块 `from modules.normalizer.names import normalize_name` 直接用
- **Config 可变**：PLACE_MAP 等查表从 YAML 加载，运维改 YAML 不用改代码重启

## 10. 构建检查清单

- [ ] 原代码所有规范化函数都搬过来
- [ ] `config/schemas/vocab.yaml` 作为 PLACE_MAP / stopwords 的 source of truth
- [ ] 单元测试覆盖率 ≥ 90%
- [ ] 4 tab UI 齐全
- [ ] 日期 UI 显示 pattern 命中轨迹
- [ ] 名字对比 UI 显示 token 重叠
- [ ] Blueprint 挂在主 web_app 的 `/normalizer/`
- [ ] 被其他模块 import 使用
