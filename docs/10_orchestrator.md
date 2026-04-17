# 模块 10 — orchestrator

> 流水线编排器。把 02~09 所有模块串成一条端到端的线，跟踪每页进度，支持断点续跑。

## 1. 目的

前面的模块各自能工作，但**各自工作**不等于**整条流水线能工作**。这个模块负责：

1. 对一个 `doc_id`，按顺序触发 02→03→04→05→06→07→09 的各阶段
2. 每阶段按**页粒度**调度（一页 OCR 完就可以开始 classify，不必等全部 OCR 完）
3. 状态持久化到 `data/logs/<doc_id>/job.json`，进程重启能恢复
4. 幂等：已有产物的步骤直接跳过
5. 给 web_app 的 Dashboard 提供实时状态 + 日志流
6. 不做业务逻辑 —— 所有处理由 02~09 完成，它只是调度

## 2. 输入 / 输出

**输入**：`data/input_pdfs/<doc_id>.pdf`（或已经拆完页的 `data/pages/<doc_id>/`）

**输出**：最终的 `data/output/<doc_id>/*.csv`（实际由 aggregator 写，编排器只是触发）

**中间产物**：`data/logs/<doc_id>/`
```
├── job.json           # 当前 job 状态
├── pipeline.log       # 人读日志
└── events.jsonl       # 事件流（dashboard 订阅）
```

## 3. Job 状态模型

```python
class Job(BaseModel):
    job_id: str                    # uuid4
    doc_id: str
    status: Literal["pending","running","paused","done","failed"]
    created_at: datetime
    updated_at: datetime
    total_pages: int
    pages: List[PageState]         # 每页一项
    counters: dict                 # model_calls / elapsed / ...

class PageState(BaseModel):
    page: int
    ingest:   StageStatus
    ocr:      StageStatus
    classify: StageStatus
    names:    StageStatus
    meta:     StageStatus
    places:   StageStatus

class StageStatus(BaseModel):
    state: Literal["pending","running","done","skipped","failed"]
    started_at: Optional[datetime]
    finished_at: Optional[datetime]
    error: Optional[str]
    elapsed_seconds: float = 0
```

`aggregate` 不在页级，是 job 级的最后一步单独字段：

```python
class Job(BaseModel):
    ...
    aggregate: StageStatus
```

## 4. 核心调度算法

**原则**：文件系统是 source of truth。编排器每次决定"下一步做什么"时，只看文件是否存在，不信任内存状态（进程可能重启）。

```python
def run_document(doc_id: str, resume: bool = True) -> Job:
    job = load_or_create_job(doc_id)
    paths = doc_paths(doc_id)

    # Stage 1: ingest (整 doc 一次)
    if not paths.manifest().exists():
        call_module("pdf_ingest", {"doc_id": doc_id})
    job.total_pages = read_manifest(paths)["page_count"]

    # Stages 2-6: 按页处理（串行，后续可并行）
    for p in range(1, job.total_pages + 1):
        run_page(doc_id, p, job)
        save_job(job)               # 每页后持久化
        emit_event(job_id, "page_updated", p)

    # Stage 7: aggregate (整 doc 一次)
    if any_intermediate_exists(paths):
        call_module("aggregator", {"doc_id": doc_id})
        job.aggregate.state = "done"

    job.status = "done"
    save_job(job)
    return job


def run_page(doc_id, p, job):
    paths = doc_paths(doc_id)
    state = job.pages[p-1]

    # OCR
    if not paths.ocr_text(p).exists():
        state.ocr.state = "running"; emit_stage_start(job, p, "ocr")
        try:
            call_module("ocr", {"doc_id": doc_id, "page": p})
            state.ocr.state = "done"
        except Exception as e:
            state.ocr.state = "failed"; state.ocr.error = str(e); raise
        emit_stage_end(job, p, "ocr")
    else:
        state.ocr.state = "done"        # skipped

    # classify
    if not paths.classify(p).exists():
        ...

    # 若 classify.should_extract==false → 跳过 names/meta/places
    decision = read_json(paths.classify(p))
    if not decision["should_extract"]:
        state.names.state = "skipped"
        state.meta.state = "skipped"
        state.places.state = "skipped"
        return

    # names
    if not paths.names(p).exists():
        ...

    # meta + places 可并行（两者都只依赖 ocr_text 和 names）
    run_parallel([
        ("meta",   lambda: call_module("meta",   {"doc_id":doc_id,"page":p})),
        ("places", lambda: call_module("places", {"doc_id":doc_id,"page":p})),
    ])
```

**模块间通信**：`call_module` 是一层抽象。**首选 HTTP**（每个模块的 blueprint），**次选直接函数调用**（在主 web_app 模式下所有模块在同一进程）。这个抽象让同一套调度既能在独立容器部署下工作，也能在单体进程下工作。

```python
# orchestrator/router.py
def call_module(name: str, payload: dict):
    if settings.ORCH_MODE == "http":
        url = MODULE_URLS[name]      # http://ocr:5103/ocr/run-single/...
        r = requests.post(url, json=payload, timeout=3600)
        r.raise_for_status()
        return r.json()
    else:
        # 直接调 core，适合单体模式（更快，没有 http 序列化开销）
        return DISPATCH[name](payload)
```

## 5. 目录结构

```
src/orchestrator/
├── __init__.py
├── pipeline.py          # run_document / run_page
├── job_store.py         # load_job / save_job（JSON 文件，原子写）
├── router.py            # call_module 抽象
├── events.py            # emit_event 到 events.jsonl，供 SSE 读
├── blueprint.py         # /orchestrate/* 路由
├── templates/
│   ├── dashboard.html   # 主 dashboard 页
│   └── _partials/
│       ├── status_grid.html
│       └── log_tail.html
├── static/
│   ├── dashboard.css
│   └── dashboard.js     # SSE + DOM 更新
└── tests/
    ├── test_pipeline_mocked.py
    └── test_job_store.py
```

## 6. Blueprint API

| 方法 | 路径 | 行为 |
|---|---|---|
| GET  | `/orchestrate/` | Dashboard UI |
| GET  | `/orchestrate/jobs` | 所有 job 列表 |
| POST | `/orchestrate/run` | `{"doc_id":"..."}` → 启动 job（异步），返回 `job_id` |
| POST | `/orchestrate/resume/<doc_id>` | 恢复之前断掉的 job |
| POST | `/orchestrate/cancel/<job_id>` | 软取消（当前阶段跑完后停） |
| GET  | `/orchestrate/status/<doc_id>` | 当前 job 状态（`Job` JSON） |
| GET  | `/orchestrate/stream/<doc_id>` | **SSE 事件流**：`page_updated` / `log` / `done` |
| GET  | `/orchestrate/log/<doc_id>` | 返回最近 N 行 pipeline.log |

**SSE 设计**：前端 `new EventSource('/orchestrate/stream/myDoc')` 订阅，服务端 `tail -f events.jsonl` 转成 SSE。简单、可靠、不需要 WebSocket。

## 7. Dashboard UI（**这是整个项目最重要的 UI**）

```
┌──────────────────────────────────────────────────────────────────────┐
│  Pipeline dashboard — myDoc                      Status: 🟢 running   │
│  [ Cancel ]   [ Pause ]   [ View logs ]                              │
├──────────────────────────────────────────────────────────────────────┤
│  Overall progress                                                     │
│  ingest  ████████████████████ 100%                                    │
│  ocr     ██████████████▒▒▒▒▒▒  72% (99/137)                           │
│  classi. ████████████▒▒▒▒▒▒▒▒  60% (82/137)                           │
│  names   █████████▒▒▒▒▒▒▒▒▒▒▒  45% (62/137)                           │
│  meta    ████████▒▒▒▒▒▒▒▒▒▒▒▒  40% (55/137)                           │
│  places  ███████▒▒▒▒▒▒▒▒▒▒▒▒▒  35% (48/137)                           │
│  aggreg. ▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒   0%                                    │
│                                                                        │
│  Counters: 412 model calls, 11 repair calls, elapsed 00:18:42          │
├──────────────────────────────────────────────────────────────────────┤
│  Per-page status (click any row to open that page in module UIs)      │
│  ┌──┬────┬──────┬──────┬────┬──────┬──────┬──────────────────────────┐│
│  │p │ing │ ocr  │ cls  │nm  │meta  │place │ notes                   ││
│  ├──┼────┼──────┼──────┼────┼──────┼──────┼──────────────────────────┤│
│  │ 1│ 🟢 │  🟢  │  🟢  │ ─  │  ─   │  ─   │ skipped: index page      ││
│  │ 2│ 🟢 │  🟢  │  🟢  │ 🟢 │  🟢  │  🟢  │ 2 ppl, 5 places          ││
│  │..│    │      │      │    │      │      │                          ││
│  │12│ 🟢 │  🟢  │  🟢  │ 🟢 │  🟡  │  🟡  │ running...               ││
│  │13│ 🟢 │  🟢  │  🟡  │ ⚫ │  ⚫  │  ⚫  │ queued                   ││
│  │..│    │      │      │    │      │      │                          ││
│  │75│ 🟢 │  🔴  │  ⚫  │ ⚫ │  ⚫  │  ⚫  │ OCR failed: timeout      ││
│  └──┴────┴──────┴──────┴────┴──────┴──────┴──────────────────────────┘│
│  🟢 done  🟡 running  ⚫ queued  ─ skipped  🔴 failed                   │
├──────────────────────────────────────────────────────────────────────┤
│  Live log tail                                                        │
│  ┌──────────────────────────────────────────────────────────────────┐ │
│  │ [18:42:13] page 12 meta done (4.1s, 1 call)                      │ │
│  │ [18:42:15] page 13 classify start                                │ │
│  │ [18:42:17] page 12 places running (person 1/2)                   │ │
│  │ ...                                                              │ │
│  └──────────────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────────┘
```

**可视化要点**：

1. **总进度条分阶段**：一眼看哪个阶段最慢（通常是 names_extractor）
2. **每页每阶段状态格**：137 页 × 6 阶段的点阵图最有冲击力，哪页卡住一目了然
3. **点行跳转**：点 p12 那行的 "meta" 格子跳到 metadata_extractor UI 并自动选中 p12
4. **颜色语义统一**：🟢 done / 🟡 running / ⚫ queued / ─ skipped / 🔴 failed
5. **SSE 实时更新**：不刷新页面，状态格实时变色、log 自动滚屏
6. **失败不停车**：某页某阶段失败后，其他页继续跑；failed 页在最后汇总报告

## 8. Docker

```yaml
  orchestrator:
    build:
      context: .
      dockerfile: docker/web.Dockerfile
    depends_on:
      ollama:
        condition: service_healthy
      pdf_ingest: { condition: service_started }
      ocr:        { condition: service_started }
      page_classifier: { condition: service_started }
      name_extractor:  { condition: service_started }
      metadata_extractor: { condition: service_started }
      place_extractor: { condition: service_started }
      aggregator: { condition: service_started }
    networks: [ llm_internal ]
    volumes:
      - ./data:/data
      - ./config:/app/config:ro
    environment:
      - ORCH_MODE=http
      - ORCH_MODULE_URLS_JSON={"ocr":"http://ocr:5103",...}
    profiles: [ "all" ]
    # 注意：没有 ports，仅内部可见，通过 web_app 反代
```

**单体模式下**不需要启这个独立容器：`ORCH_MODE=inproc` + 挂到 web_app 里，直接调 Python 函数。

## 9. 测试

**单元测试**（mock 所有 call_module）：
- `test_pipeline_happy_path`：3 页全成功
- `test_pipeline_one_page_fails`：一页 OCR 失败，其他页继续
- `test_resume_skips_done_pages`：第二次运行跳过已完成
- `test_skip_reason_propagates`：classify 判定 `should_extract=false` 时跳过下游

**集成测试**（用最小真实 PDF）：
- 2 页 PDF 跑通全链路，CSV 行数正确

## 10. 故障排查

| 症状 | 原因 | 对策 |
|---|---|---|
| Dashboard 所有格子一直灰 | SSE 没连上 | 检查 web_app 反代 `/orchestrate/stream` 是否透传 `text/event-stream` |
| 某页卡 `running` 很久 | 对应模块 HTTP 超时 | 看该模块容器日志；调大 `requests.post(timeout=)` |
| resume 后又重跑了 done 页 | 产物文件缺失或损坏 | 幂等判断是看**文件存在 + 非空 + 能 parse**；坏文件会触发重跑 |
| job.json 损坏 | 半写入 | job_store 用原子写；实在坏了删了重跑 |

## 11. 构建检查清单

- [ ] `Job` / `PageState` / `StageStatus` 模型定义
- [ ] `run_document` / `run_page` 串通所有阶段
- [ ] `call_module` 支持 http / inproc 两种 mode
- [ ] `job_store` 原子写
- [ ] `events.jsonl` + SSE 流
- [ ] 幂等：每阶段按文件存在性跳过
- [ ] `classify.should_extract=false` 正确传播（跳 names/meta/places）
- [ ] meta + places 并行
- [ ] Dashboard UI：总进度 + 状态格 + 日志 + SSE
- [ ] 状态格可点跳转对应模块 UI
- [ ] resume / cancel 按钮可用
- [ ] 单测 4 个典型场景
- [ ] 2 页真实 PDF 集成测试通过
