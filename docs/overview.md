# Overview — PDF-OCR-NER 抽取流水线（重构版）

## 1. 项目目的

这是一个针对**历史奴隶 / 解放档案文献**的端到端抽取系统。给一份扫描版 PDF 文档，系统要做的事：

1. 把 PDF 拆成每页一张图像
2. 对每页图像做 OCR，得到文字
3. 对每页文字用 LLM 抽取：
   - 这页要不要抽（是否索引页/烂 OCR 页）、报告类型是什么
   - 这页涉及到的**被奴役/解放主体人名**
   - 每个人的**案件元数据**（罪名类型、是否受虐、冲突类型、审理结果、付款金额）
   - 每个人的**地点路径**（出生地、被掳处、到达地、转运地，附带时间）
4. 规范化、去重、验证
5. 写入最终 CSV（`Detailed info.csv`、`name place.csv`、`run_status.csv`）

原系统是两个单体 Python 脚本 + docker-compose。重构目标是把它拆成**模块化 Flask 应用**，每块都能独立跑、独立测试、可视化展示。

---

## 2. 核心设计目标

| 目标 | 具体含义 |
|---|---|
| **模块化** | 每个阶段是独立模块，有自己的目录、Dockerfile、测试、UI |
| **可独立运行** | 每个模块能单独启动一个容器，独立完成它那一段工作 |
| **可自由组合** | 模块之间约定通过文件系统 + HTTP 交换数据，不硬依赖 |
| **可视化测试** | 每个模块都带一个 Flask 页面，能直观看见输入、中间产物、输出 |
| **网络隔离** | Ollama 完全不对外；Web UI 只绑 `127.0.0.1`，不走 LAN |
| **离线可用** | Runtime 阶段所有容器都在 `internal: true` 网络，零外网 |
| **可恢复** | 每个模块幂等，中断后重跑能跳过已完成 |

---

## 3. 顶层架构

### 3.1 数据流视图

```
 ┌────────────┐
 │  PDF file  │  ← 用户上传到 data/input_pdfs/
 └─────┬──────┘
       ▼
┌──────────────────┐  拆页 + 基础元数据
│  02 pdf_ingest   │  → data/pages/<doc_id>/p001.png ...
└─────────┬────────┘
          ▼
┌──────────────────┐  视觉模型 OCR（调 Ollama）
│     03 ocr       │  → data/ocr_text/<doc_id>/p001.txt ...
└─────────┬────────┘
          ▼
┌──────────────────┐  分类：extract? report_type?
│04 page_classifier│  → data/intermediate/<doc_id>/p001.classify.json
└─────────┬────────┘
          ▼
┌──────────────────┐  多轮抽取命名主体
│05 name_extractor │  → data/intermediate/<doc_id>/p001.names.json
└─────────┬────────┘
          ├──────────────────────────┐
          ▼                          ▼
┌──────────────────┐     ┌──────────────────┐
│06 meta_extractor │     │07 place_extractor│
└─────────┬────────┘     └─────────┬────────┘
          │                        │
          ▼                        ▼
┌──────────────────────────────────────────┐
│           08 normalizer                  │  (库 + UI)
│  名字/地名/日期规范化、去重、验证           │
└─────────┬────────────────────────────────┘
          ▼
┌──────────────────────────────────────────┐
│           09 aggregator                  │
│  合并所有页，写三份最终 CSV               │
└─────────┬────────────────────────────────┘
          ▼
      data/output/
      ├── Detailed info.csv
      ├── name place.csv
      └── run_status.csv
```

旁路支持服务：

```
┌──────────────────────┐      ┌────────────────────────┐
│ 01 ollama_gateway    │◄─────┤ 所有需要 LLM 的模块       │
│ (Ollama 容器 + 客户端) │  HTTP │ (03/04/05/06/07)       │
└──────────────────────┘      └────────────────────────┘

┌──────────────────────┐
│ 10 orchestrator      │  调度整个流水线，跟踪每页进度
└──────────────────────┘

┌──────────────────────┐
│ 11 web_app           │  Flask 主程序，把所有模块的 blueprint
│ (127.0.0.1 only)     │  挂在一起；也是总控台和 dashboard
└──────────────────────┘

┌──────────────────────┐
│ 00 shared (库)        │  OllamaClient、schemas、config、IO
└──────────────────────┘
```

### 3.2 模块清单

| # | 模块 | 性质 | 有独立容器? | 有 Web UI? |
|---|------|------|------------|-----------|
| 00 | `shared` | Python 库，不是服务 | 否 | 否 |
| 01 | `ollama_gateway` | 基础设施 + 客户端封装 | 是（Ollama 自己） | 否 |
| 02 | `pdf_ingest` | 处理模块 | 是（可选） | 是 |
| 03 | `ocr` | 处理模块 | 是（可选） | 是 |
| 04 | `page_classifier` | 处理模块 | 是（可选） | 是 |
| 05 | `name_extractor` | 处理模块 | 是（可选） | 是 |
| 06 | `metadata_extractor` | 处理模块 | 是（可选） | 是 |
| 07 | `place_extractor` | 处理模块 | 是（可选） | 是 |
| 08 | `normalizer` | 库 + UI（纯 Python） | 否 | 是 |
| 09 | `aggregator` | 处理模块 | 是（可选） | 是 |
| 10 | `orchestrator` | 调度器 | 是 | 是 |
| 11 | `web_app` | Flask 主入口 | 是（唯一对本机暴露） | 是 |

### 3.3 模块间的契约

所有模块遵守**同一种约定**：

1. **通过文件系统交换数据**（主契约）：每个模块读某个目录，写某个目录，格式固定。这是默认集成方式。
2. **暴露 HTTP 蓝图**（辅助契约）：每个处理模块都实现一组 REST 端点，orchestrator 通过 HTTP 触发、查进度。这是可视化和分步调试用的。
3. **CLI 入口**（辅助契约）：每个模块可以 `python -m modules.<name> ...` 单跑，支持原有命令行参数。这是脱离 Flask 调试用的。

换句话说：你想脱离 Web 跑全链路，可以。你想只跑 OCR 模块，可以。你想用主 Web UI 串起来跑，也可以。三种用法走的是同一套核心代码。

---

## 4. 完整文件结构

```text
llm-pipeline/
│
├── README.md
├── .env.example
├── .dockerignore
├── .gitignore
│
├── compose.seed.yaml              # 在线下载 Ollama 模型（一次性）
├── compose.yaml                   # 运行时（离线，完整 stack）
├── compose.dev.yaml               # 开发用 overlay（热重载、挂源码）
│
├── docs/                          # ← 你正在看的这些文档
│   ├── overview.md
│   ├── process.md
│   └── modules/
│       ├── 00_shared.md
│       ├── 01_ollama_gateway.md
│       ├── 02_pdf_ingest.md
│       ├── 03_ocr.md
│       ├── 04_page_classifier.md
│       ├── 05_name_extractor.md
│       ├── 06_metadata_extractor.md
│       ├── 07_place_extractor.md
│       ├── 08_normalizer.md
│       ├── 09_aggregator.md
│       ├── 10_orchestrator.md
│       └── 11_web_app.md
│
├── docker/                        # 所有 Dockerfile
│   ├── base.Dockerfile            # 共享的基础镜像层
│   ├── ocr.Dockerfile             # 需要 opencv
│   ├── ner.Dockerfile             # 轻量，只要 requests
│   ├── ingest.Dockerfile          # 需要 pymupdf/pdf2image
│   └── web.Dockerfile             # Flask + gunicorn
│
├── requirements/
│   ├── base.txt                   # requests, flask, pydantic
│   ├── ocr.txt                    # opencv-python-headless, numpy
│   ├── ingest.txt                 # pymupdf
│   ├── ner.txt                    # 继承 base
│   └── web.txt                    # flask, jinja2, gunicorn
│
├── config/
│   ├── prompts/                   # 所有 prompt 模板抽成独立文件
│   │   ├── page_classify.txt
│   │   ├── name_pass.txt
│   │   ├── name_recall.txt
│   │   ├── name_filter.txt
│   │   ├── name_verify.txt
│   │   ├── meta_pass.txt
│   │   ├── place_pass.txt
│   │   ├── place_recall.txt
│   │   ├── place_verify.txt
│   │   ├── place_date_enrich.txt
│   │   ├── json_repair.txt
│   │   └── ocr.txt
│   ├── schemas/                   # CSV 列定义、枚举值定义
│   │   ├── detail.yaml
│   │   ├── place.yaml
│   │   ├── status.yaml
│   │   └── vocab.yaml             # CRIME_TYPES, CONFLICT_TYPES, PLACE_MAP ...
│   └── approved_model_tags.json
│
├── src/
│   ├── shared/                    # 00 共享库（无 Flask 依赖）
│   │   ├── __init__.py
│   │   ├── ollama_client.py
│   │   ├── schemas.py             # dataclass / pydantic
│   │   ├── text_utils.py
│   │   ├── storage.py             # 路径约定、原子写
│   │   ├── config.py              # 环境变量、默认值
│   │   └── logging_setup.py
│   │
│   ├── modules/
│   │   ├── pdf_ingest/            # 02
│   │   │   ├── __init__.py
│   │   │   ├── core.py
│   │   │   ├── blueprint.py
│   │   │   ├── standalone.py
│   │   │   ├── cli.py
│   │   │   ├── templates/ui.html
│   │   │   └── tests/
│   │   │
│   │   ├── ocr/                   # 03
│   │   │   ├── core.py            # 含原 glm_ocr_ollama.py 的图像处理
│   │   │   ├── preprocessing.py   # enhance_gray / deskew / crop / tile
│   │   │   ├── blueprint.py
│   │   │   ├── standalone.py
│   │   │   ├── cli.py
│   │   │   ├── templates/ui.html
│   │   │   └── tests/
│   │   │
│   │   ├── page_classifier/       # 04
│   │   │   └── ...
│   │   ├── name_extractor/        # 05
│   │   │   └── ...
│   │   ├── metadata_extractor/    # 06
│   │   │   └── ...
│   │   ├── place_extractor/       # 07
│   │   │   └── ...
│   │   ├── normalizer/            # 08
│   │   │   ├── names.py
│   │   │   ├── places.py
│   │   │   ├── dates.py
│   │   │   ├── blueprint.py
│   │   │   ├── templates/ui.html
│   │   │   └── tests/
│   │   └── aggregator/            # 09
│   │       └── ...
│   │
│   ├── orchestrator/              # 10
│   │   ├── pipeline.py
│   │   ├── job_store.py
│   │   ├── blueprint.py
│   │   ├── templates/
│   │   └── tests/
│   │
│   └── web_app/                   # 11 Flask 主程序
│       ├── __init__.py
│       ├── app.py                 # create_app() factory
│       ├── register.py            # 把所有 blueprint 挂上去
│       ├── auth.py                # 可选：本地鉴权中间件
│       ├── templates/
│       │   ├── base.html
│       │   └── dashboard.html
│       ├── static/
│       │   ├── pico.css
│       │   └── app.js
│       └── wsgi.py                # gunicorn 入口
│
├── data/                          # 所有运行时数据
│   ├── input_pdfs/                # ← 用户放 PDF 的地方
│   ├── pages/<doc_id>/            # 拆页后的 PNG
│   ├── ocr_text/<doc_id>/         # OCR 输出 .txt
│   ├── intermediate/<doc_id>/     # 各模块的中间 JSON
│   ├── output/<doc_id>/           # 最终三份 CSV
│   └── logs/<doc_id>/             # 运行日志
│
├── volumes/
│   └── ollama/                    # Ollama 模型持久化
│
└── scripts/
    ├── seed_model.sh              # 一键拉模型
    ├── run_pipeline.sh            # 一键跑完整 PDF
    └── dev_up.sh                  # 开发模式启动
```

---

## 5. 技术栈

| 层 | 选型 | 为什么 |
|---|---|---|
| Python | 3.11 | 稳定、符合原代码 |
| Web | Flask + Jinja2 | 你指定要 Flask；Jinja 够用，不引入前端框架复杂度 |
| 前端 | 无 SPA，用 Pico.css + vanilla JS | 简单、快、够可视化 |
| PDF 拆页 | PyMuPDF (`fitz`) | 纯 Python 轮子、不依赖外部可执行、快 |
| 图像 | opencv-python-headless | 原代码已在用 |
| LLM | Ollama（qwen2.5 / mistral-small3.1 / glm-ocr）| 继承原架构 |
| 容器 | Docker Compose v2 | 继承原架构 |
| 部署 | Compose profiles | 每个模块可独立拉起 |
| WSGI | gunicorn | Flask 生产用标准 |

---

## 6. 安全模型

这是整个项目的**硬约束**，不能违反：

1. **Ollama 绝不对外**：runtime 的 compose 里 `ollama` 服务**没有 `ports:` 字段**。它只在 `llm_internal` 这个 `internal: true` 网络里存在，其他容器通过 DNS 名 `http://ollama:11434` 访问它。
2. **Web UI 只对 127.0.0.1**：唯一需要给主机可访问的就是 `web_app`。它绑 `127.0.0.1:5000:5000`，本机浏览器能打开，LAN 和公网打不到。
3. **处理容器零外网**：所有 `modules/*` 服务都挂在 `internal: true` 网络，装完依赖后**没有外网出口**。
4. **非 root 用户**：所有容器 `user: "10001:10001"`。
5. **能力剥离**：`cap_drop: ALL` + `no-new-privileges:true`。
6. **数据卷只读**：PDF 输入卷挂 `:ro`。
7. **Seed 阶段的特殊处理**：只有 seed 阶段（临时下载模型）会有外网；seed 用完就关。日常运行不跑 seed。

两个网络拓扑：

```
      Seed 阶段（一次性）                     Runtime 阶段（日常）
   ┌────────────────────┐                ┌──────────────────────────┐
   │  ollama_seed       │                │  llm_internal (internal:true)│
   │  127.0.0.1:11434   │                │  ┌────────┐  ┌──────────┐ │
   │  外网能下载模型       │                │  │ ollama │  │ ocr / ner │ │
   └────────────────────┘                │  └────────┘  └──────────┘ │
                                         │           ...              │
                                         └──────────────────────────┘
                                                    ▲
                                          llm_frontend 网络
                                                    ▲
                                         ┌──────────────────┐
                                         │  web_app         │
                                         │ 127.0.0.1:5000   │
                                         └──────────────────┘
```

---

## 7. 三种运行方式

同一套代码支持三种方式跑，覆盖不同场景：

### 方式 A：完整 Web UI 模式（日常使用）
```bash
docker compose up -d
# 浏览器打开 http://127.0.0.1:5000
# 上传 PDF → 看进度 → 下载 CSV
```

### 方式 B：单模块独立容器（调试某一环）
```bash
# 只把 OCR 模块拉起来独立 UI
docker compose --profile ocr-only up -d ollama ocr
# 浏览器打开 http://127.0.0.1:5103（OCR 模块 standalone UI）
```

### 方式 C：CLI 单跑（脚本化、无 UI）
```bash
docker compose run --rm ocr python -m modules.ocr.cli \
  --in_dir /data/pages/<doc_id> \
  --out_dir /data/ocr_text/<doc_id> \
  --model glm-ocr:latest
```

三种方式用的是**同一份 `modules/ocr/core.py`**，只是入口不同。

---

## 8. 可视化测试策略

重构版的关键亮点。每个处理模块都有一个 `/test` 路由，提供专属的可视化测试 UI：

| 模块 | 可视化展示内容 |
|---|---|
| 02 pdf_ingest | PDF 缩略图网格，页数/尺寸/文件大小概览 |
| 03 ocr | 原图 → 灰度 → 去歪斜 → 裁剪 → 切片 的 5 连图；OCR 文本对齐显示；原始模型响应 JSON |
| 04 page_classifier | OCR 全文 + 分类结果徽章 + evidence 高亮 + 各轮次模型原始响应 |
| 05 name_extractor | 文本里所有人名高亮；四轮结果（pass1/recall/filter/verify）并排显示；被剔除候选的理由 |
| 06 metadata_extractor | 每人一张卡片，每个字段旁挂 evidence 句子并定位到原文 |
| 07 place_extractor | 地点路径图（有序节点）；每个地点的 evidence 高亮；日期置信度色块 |
| 08 normalizer | 输入框任意填名字/地名/日期，实时看规范化结果 + 命中的规则 |
| 09 aggregator | 当前 CSV 表格视图 + 差异视图（本次新增哪些行）+ 统计面板 |
| 10 orchestrator | 总 dashboard：每页一行，列出各模块状态灯；实时日志 tail |
| 11 web_app | 组合以上所有，加 PDF 上传、作业管理、结果下载 |

这些 UI 不是可选装饰，是**这次重构的第一等目标**。它们让你能用眼睛验证每个模块，不必靠只读打印或猜测。

---

## 9. 成功判据

重构后如果下面这些事都能做到，就算达成目标：

1. 上传一份 PDF，从头跑完，得到的 CSV 与原脚本运行结果等价（或更好）。
2. 单独拉起任何一个模块容器，它能独立工作（在有输入的前提下）。
3. 打开任何一个模块的 `/test` 页面，能看到这个模块在一个页面/一段文本/一个人物上的全部输入输出和中间产物。
4. Ollama 从主机和 LAN 都 ping 不到。
5. Web UI 从 LAN 打不到，只有本机浏览器能访问。
6. 全程运行时不需要外网。
7. 任何一轮失败，可以从断点续跑，不重跑已完成的页面。

---

详细的构建顺序看 [process.md](./process.md)；每个模块的详细设计看 [modules/](./modules/) 下对应的文档。
