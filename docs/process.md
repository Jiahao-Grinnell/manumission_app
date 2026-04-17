# Process — 构建顺序与步骤

这份文档讲**按什么顺序搭这个系统**，以及每一步该产出什么、怎么验证。严格按顺序走能让每一阶段都有东西可跑、可看、可测，不会一口气写完再调试。

## 指导原则

1. **从下往上搭**：先搭共享库和基础设施，再搭依赖它们的处理模块，最后搭编排层。
2. **先简后繁**：先做没有 LLM 依赖的模块（拆页、规范化、聚合），它们最容易写和测。
3. **每阶段都可 demo**：每完成一个阶段都能打开浏览器看点东西，不攒到最后。
4. **测试 UI 和核心逻辑同步写**：不要先写核心逻辑再回头补 UI，两者一起出。
5. **Docker 化从第一天就有**：不要"先裸跑、最后装进容器"。头一个模块就进 Docker。

---

## Phase 0：工程骨架（约 0.5 天）

在写任何业务逻辑之前，先把壳搭好。

### 要做的事

| # | 任务 | 产出 |
|---|------|------|
| 0.1 | 创建目录树 | 按 `overview.md` 里的文件结构 `mkdir -p` 全部目录 |
| 0.2 | `.dockerignore` / `.gitignore` | 忽略 `data/`、`volumes/`、`__pycache__` |
| 0.3 | `requirements/base.txt` | `requests`、`flask`、`jinja2`、`pydantic`、`gunicorn`、`pyyaml` |
| 0.4 | `docker/base.Dockerfile` | `python:3.11-slim` + 非 root 用户 + 装 base 依赖 |
| 0.5 | 空 `compose.yaml` + `compose.seed.yaml` | 先只定义 `ollama` 服务和两个 network |
| 0.6 | `.env.example` 和 `config/` 配置骨架 | 列清楚环境变量 |
| 0.7 | `src/shared/config.py` | 把所有路径、模型名、超时参数收口 |
| 0.8 | `src/shared/logging_setup.py` | 统一日志格式 |

### 验证

```bash
docker compose -f compose.yaml config        # 语法通过
docker build -f docker/base.Dockerfile .     # base 镜像能构建
```

**不要**跳过这阶段。后面每个模块都会继承 base 镜像、用 `shared/config.py`、按统一目录约定工作。地基不稳后面会返工。

---

## Phase 1：共享核心库（模块 00）+ Ollama 网关（模块 01）

这俩都不是可独立运行的"服务"，但是**所有后续模块都依赖它们**，必须先有。

### 1.1 模块 00 shared

把原 `ner_extract.py` 和 `glm_ocr_ollama.py` 里的以下东西抽到 `src/shared/`：

- `OllamaClient`（带重试、超时、JSON 提取、修复 prompt 的那一套）
- JSON 提取工具 `extract_json`
- 文本清洗 `clean_ocr` / `normalize_ws` / `strip_accents`
- 路径约定 `paths.py`（给定 `doc_id` 返回所有相关目录）
- 原子写入 `write_csv_atomic`

**怎么验证**：只写单元测试。这阶段还没 UI。
```bash
pytest src/shared/tests/
```

### 1.2 模块 01 ollama_gateway

这个模块"就是" Ollama 容器本身 + `OllamaClient` 的文档化使用约定。不写新代码，只写：

- `compose.yaml` 里 `ollama` 服务的完整定义（GPU、内部网络、无 ports、非 root、cap drop）
- `compose.seed.yaml` 里 `ollama_seed` 服务（临时 `127.0.0.1:11434` 绑定，下载模型用）
- `scripts/seed_model.sh`：一键拉模型
- 健康检查：`GET /api/version`

**怎么验证**：
```bash
./scripts/seed_model.sh qwen2.5:14b-instruct
docker compose up -d ollama
# 验证内部可达、主机不可达
docker run --rm --network llm-pipeline_llm_internal curlimages/curl http://ollama:11434/api/version
curl http://127.0.0.1:11434/api/tags   # 应当 connection refused
```

**里程碑 M1**：此刻你有一个跑着的 Ollama，内部可用、主机打不到。

---

## Phase 2：不依赖 LLM 的模块（最容易写的先做）

这几个模块不碰 LLM，逻辑简单，但是整个流水线的入口和出口，先搞定它们意义很大。

### 2.1 模块 02 pdf_ingest（**先做这个**）

- 功能：PDF → `data/pages/<doc_id>/p001.png, p002.png, ...` + `manifest.json`
- 实现：PyMuPDF，每页渲染到 300 DPI PNG
- **独立 UI**：上传 PDF 表单 → 拆页后的缩略图网格
- Blueprint 路径：`/ingest/*`
- CLI：`python -m modules.pdf_ingest.cli --pdf path/to.pdf --doc-id myDoc`

**验证**：找一份小 PDF（几页），上传，看到缩略图，`data/pages/myDoc/` 里有对应 PNG。

### 2.2 模块 08 normalizer（纯 Python，易测）

把 `ner_extract.py` 里所有规范化函数搬过来并分文件：

- `names.py`：`normalize_name`、`is_valid_name`、`names_maybe_same_person`、`merge_named_people`、`name_compare_tokens`
- `places.py`：`normalize_place`、`is_valid_place`、`PLACE_MAP` 规则
- `dates.py`：`to_iso_date`、`parse_first_date_in_text`、`extract_doc_year`
- `evidence.py`：`clean_evidence`、`normalize_for_match`

**独立 UI**：一个单页 form，四个输入区（名字 / 地名 / 日期字符串 / 任意文本），右侧实时显示规范化结果 + 哪条规则命中了。这对调试极其有用。

**验证**：单测用原代码里各种 edge case（"shargah" → "Sharjah"、"17th May 1931" → ISO 等）。

### 2.3 模块 09 aggregator

- 功能：从 `data/intermediate/<doc_id>/*.json` 读取每页产出，合并、去重、写最终 CSV
- 复用 `dedupe_place_rows`、`merge_named_people` 等（从 normalizer 导）
- **独立 UI**：当前 CSV 表格预览 + 三列统计（总行数 / 唯一人名 / 唯一地点）+ 简单下载按钮

**验证**：塞一批假的 `.json` 进 `data/intermediate/demo/`，跑聚合，看 CSV 是否符合预期。

**里程碑 M2**：到这里你有三个能独立跑的模块。各自 UI 能看。Ollama 还没用上。

---

## Phase 3：OCR 模块（模块 03）

OCR 是第一个碰 LLM 的模块，也是流水线里最重的视觉处理环节。单独作为一个 phase。

### 要做的事

- 把 `glm_ocr_ollama.py` 的图像处理拆成 `preprocessing.py`（`enhance_gray` / `deskew` / `crop_foreground` / `split_vertical_with_overlap`）
- `core.py` 调 `OllamaClient` 的 vision 接口
- Dockerfile 用 `docker/ocr.Dockerfile`，装 opencv
- 断点续跑逻辑（`should_skip_existing`）从 `core.py` 复用
- **独立 UI**（重要）：
  - 左栏列当前 `data/pages/<doc_id>/` 所有 PNG
  - 选一张后右栏显示预处理 5 连图（原图 / 增强 / 去歪斜 / 裁剪 / 切片）
  - 下面显示每个切片的 OCR 响应
  - 顶部有"OCR 全部"按钮，触发整目录跑
- 蓝图：`/ocr/*`

### 验证

```bash
# 先用已拆页的图（Phase 2 产物）
docker compose run --rm ocr python -m modules.ocr.cli \
  --in_dir /data/pages/myDoc --out_dir /data/ocr_text/myDoc \
  --model glm-ocr:latest
```

然后打开 OCR 模块 UI，点任一页看预处理产物是否符合预期，OCR 文本是否看起来对。

**里程碑 M3**：你能从 PDF 一路跑到 OCR 文本（前三个模块接起来）。

---

## Phase 4：NER 抽取模块（模块 04/05/06/07）

这是核心业务逻辑。按照依赖顺序逐个写，每个都带 UI。

### 4.1 模块 04 page_classifier（先做这个）

最简单的 LLM 模块：一个 prompt、一个 JSON、一个决策。

- `core.py`：一个 `classify(ocr_text) -> PageDecision`
- prompt 从 `config/prompts/page_classify.txt` 读
- **UI**：文本选择器（选一个 `.txt`）→ 左栏显示全文 → 右栏显示 JSON 响应和分类徽章 → 下方显示 evidence 在文本里的高亮位置

**验证**：对已知是 statement/transport-admin/correspondence 的几个测试页跑一遍，看分类是否对。

### 4.2 模块 05 name_extractor

这是流水线里最复杂的模块（四轮 LLM + 规则过滤）。

- 分成四步，每步都是独立函数：`pass1_extract` / `pass2_recall` / `filter_candidates` / `verify_final`
- 每步独立可测、独立有 UI
- 最后的规则过滤 `keep_subject_name`（正负 pattern）放 `core.py`
- **UI**：
  - 顶部文本选择器
  - 四个并列 panel，显示四轮输出（相同格式：高亮的人名 + evidence）
  - 最下面一个 panel 显示"最终输出"，被剔除的候选显示灰色删除线 + tooltip 说明原因（"匹配到 ROLE_NEGATIVE_PATTERNS"、"不在候选集里" 等）

### 4.3 模块 06 metadata_extractor

- 输入 `(ocr_text, name, page, report_type)` → 输出一个 detail 行
- 单次 LLM 调用，schema 固定
- **UI**：
  - 选页面 + 选名字
  - 展示 5 个字段卡片（crime_type / whether_abuse / conflict_type / trial / amount_paid）
  - 每个字段旁挂 evidence 片段，evidence 文字点击后原文对应位置高亮滚动到

### 4.4 模块 07 place_extractor

- 三轮 LLM（candidate / recall / verify）+ 日期 enrich + 规则 reconcile
- 比 name_extractor 还重，但结构类似
- **UI**：
  - 选页面 + 选名字
  - 显示地点路径的**有序卡片链**（order 1 → 2 → 3 ...），order=0 的放旁边
  - 每个地点显示 evidence、日期、置信度（explicit/derived_from_doc 用不同颜色徽章）
  - 能切换查看各轮候选

### 验证（Phase 4 整体）

每个模块单独在其 UI 上跑过 3-5 个测试页，输出合理。最后串起来：

```bash
# 用 CLI 串 04/05/06/07 跑一个 doc
docker compose run --rm classifier python -m modules.page_classifier.cli ...
docker compose run --rm names python -m modules.name_extractor.cli ...
# 等等
```

**里程碑 M4**：整条流水线所有业务模块都各自能跑能看。

---

## Phase 5：编排层（模块 10 orchestrator）

到这步所有"干活"的模块都有了，现在把它们串起来。

### 要做的事

- `pipeline.py`：`run_document(doc_id)` 函数，顺序调用各模块（文件系统传数据）
- `job_store.py`：简单的作业状态管理（不用数据库，JSON 文件就够）
- **per-page 粒度**的进度跟踪
- 幂等：每个模块产物都有，跳过重跑
- Blueprint：`/orchestrate/*`
  - `POST /orchestrate/run`：启动一个 job
  - `GET /orchestrate/status/<job_id>`：当前状态
  - `GET /orchestrate/stream/<job_id>`：SSE 事件流（实时日志）
- **Dashboard UI**：
  - 每页一行
  - 每行 6 个状态灯（ingest / ocr / classify / names / meta+places / aggregate）
  - 点状态灯能跳到对应模块 UI 看该页详情
  - 右下角实时 tail 日志

### 验证

上传真实 PDF，一键运行，全程盯着 dashboard 看，每个状态灯按顺序变绿。最终 CSV 写出。

**里程碑 M5**：端到端完成。此时可以宣布功能可用。

---

## Phase 6：主 Web App（模块 11）

把所有东西包进一个用户面向的 Flask 主程序。

### 要做的事

- `create_app()` factory：挂所有 blueprint，注册错误处理、日志
- 共享的 `base.html`：顶部导航栏链接所有模块
- Dashboard 页（继承 orchestrator dashboard）作为首页
- 文件上传页（调 pdf_ingest 的 API）
- 作业列表页：列出 `data/output/` 下所有已完成的 doc
- 结果下载页：每个 doc 的 3 份 CSV 直接下载
- **生产容器**：gunicorn 多 worker，只绑 `127.0.0.1:5000`
- 简单的本地访问控制（可选）：检查请求来自 `127.0.0.1`，否则 403

### 验证

```bash
docker compose up -d      # 所有服务起来
# 浏览器打开 http://127.0.0.1:5000
# 从陌生机器 ping 不到 5000
```

**里程碑 M6**：一个打磨过的、可交付的系统。

---

## Phase 7：打磨（可选）

这些是锦上添花，视时间做。

- **性能**：OCR 并发（同时处理 N 张图）、LLM 批处理
- **恢复性**：跑到一半 kill，再启动能从断点续跑
- **审计日志**：每次 LLM 调用记录 prompt+response 到 jsonl
- **导出格式**：除了 CSV，加 JSON Lines、Parquet
- **对比工具**：新旧 CSV 对比 UI，validator 模块
- **回归测试**：把原脚本对某批测试数据的输出 freeze 下来作 golden，重构版必须匹配

---

## 汇总构建顺序（一行表）

```
M0: 骨架   → M1: Ollama   → M2: 3 个非 LLM 模块   → M3: OCR
  → M4: 4 个 NER 模块   → M5: 编排器   → M6: 主 Web App   → M7: 打磨
```

把项目切成 7 段，每段有明确的可 demo 成果，推进时心里就不慌。

---

## 每阶段的验收清单（给自己 checklist）

### M1 Ollama
- [ ] `docker compose up -d ollama` 成功
- [ ] `curl http://127.0.0.1:11434/api/tags` **失败**
- [ ] 内部 curl 能通
- [ ] 至少有一个模型已 pulled

### M2 非 LLM 模块
- [ ] pdf_ingest UI 能上传 PDF 看到缩略图
- [ ] normalizer UI 能实时演示规则
- [ ] aggregator UI 能展示/下载假数据合成的 CSV
- [ ] 三个模块各自 `docker compose run --rm <service> ...` 跑通

### M3 OCR
- [ ] OCR UI 能对选中页面看 5 连预处理图
- [ ] 整目录 OCR 跑通、产物写入 `data/ocr_text/`
- [ ] 断点续跑生效

### M4 NER 四模块
- [ ] 每个模块都有自己的 UI，能对选中页面看结果
- [ ] 测试页 5 个以上，人工验证结果合理
- [ ] 各模块 CLI 能单独跑

### M5 编排
- [ ] Dashboard 上传 PDF 能端到端跑完
- [ ] 每页 6 个状态灯按序亮绿
- [ ] 中途 kill 再启动能续跑

### M6 主 Web App
- [ ] 首页即 Dashboard
- [ ] LAN 访问失败、127.0.0.1 访问成功
- [ ] 能下载 3 份 CSV
- [ ] gunicorn 跑在多 worker 上

---

## 常见陷阱预警

1. **不要在 Phase 1 之前写业务代码**。基础打不牢后面一直返工。
2. **OCR 的 prompt 不稳定**。vision 模型有时返回 markdown fence 或附加说明，原代码有 `cleanup_ocr_text` 专门处理，别忘了保留。
3. **LLM 返回的 JSON 常缺字段或多字段**。`OllamaClient.generate_json` 的"坏 → 修复 prompt"兜底一定要保留。
4. **路径在容器里都是 `/data/...`**。别把主机路径硬编码进 Python。全走 `shared/config.py`。
5. **Windows 行尾**。原项目的 `compose.yaml` 是 `\r\n`，迁移时统一 LF，别让 docker-compose 报怪异错误。
6. **非 root + 写文件**：容器里的 uid 10001 要对 `data/` 下目录有写权限。`docker compose` 启动前要 `chown -R 10001:10001 data/`（或起个 init 容器帮忙）。
7. **`internal: true` 网络没有 DNS 查外网**。这意味着 `pip install` 阶段必须在 base 镜像里完成，运行时不能再下载。

---

接下来去看每个模块自己的设计文档：`docs/modules/*.md`。
