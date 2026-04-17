# 模块 02 — pdf_ingest

> PDF 拆页模块。把一份扫描版 PDF 变成每页一张高清 PNG。**流水线的新入口**。

## 1. 目的

旧系统的入口是散装页面图或 `.txt`。新系统的输入升级为**一整份 PDF**。这个模块负责：

1. 接收用户上传的 PDF
2. 每页渲染为 PNG（默认 300 DPI）
3. 生成 `manifest.json` 记录基础元数据
4. 不做任何图像处理或 OCR — 那是下游 OCR 模块的事

## 2. 输入 / 输出

**输入**：`data/input_pdfs/<doc_id>.pdf`

**输出**：
```
data/pages/<doc_id>/
├── p001.png
├── p002.png
├── ...
├── p137.png
└── manifest.json
```

`manifest.json` 示例：
```json
{
  "doc_id": "historical_archive_vol3",
  "source_pdf": "historical_archive_vol3.pdf",
  "source_pdf_sha256": "3a7f...",
  "page_count": 137,
  "dpi": 300,
  "created_at": "2026-04-17T10:23:45Z",
  "pages": [
    {"page": 1,   "filename": "p001.png", "width": 2480, "height": 3508, "size_bytes": 1456789},
    {"page": 2,   "filename": "p002.png", "width": 2480, "height": 3508, "size_bytes": 1432109},
    ...
  ]
}
```

## 3. 核心算法

用 PyMuPDF（`fitz`）。它纯 Python，快，跨平台，不依赖外部可执行文件：

```python
# core.py 概要
import fitz
from pathlib import Path
import hashlib, json, datetime

def ingest(pdf_path: Path, out_dir: Path, dpi: int = 300) -> dict:
    doc = fitz.open(pdf_path)
    out_dir.mkdir(parents=True, exist_ok=True)
    sha = _sha256(pdf_path)
    pages_meta = []
    zoom = dpi / 72
    matrix = fitz.Matrix(zoom, zoom)
    for i, page in enumerate(doc, start=1):
        pix = page.get_pixmap(matrix=matrix, alpha=False)
        out_file = out_dir / f"p{i:03d}.png"
        pix.save(out_file)
        pages_meta.append({
            "page": i,
            "filename": out_file.name,
            "width": pix.width,
            "height": pix.height,
            "size_bytes": out_file.stat().st_size,
        })
    manifest = {
        "doc_id": out_dir.name,
        "source_pdf": pdf_path.name,
        "source_pdf_sha256": sha,
        "page_count": len(pages_meta),
        "dpi": dpi,
        "created_at": datetime.datetime.utcnow().isoformat() + "Z",
        "pages": pages_meta,
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
    return manifest
```

**设计决策**：
- **PNG 而非 JPEG**：OCR 质量优先，体积可以接受
- **300 DPI 默认**：OCR 经验值，低于 200 效果差、高于 400 回报递减
- **每页独立文件**：下游断点续跑简单
- **不压缩**：PyMuPDF 默认 PNG 压缩就够

## 4. 目录结构

```
src/modules/pdf_ingest/
├── __init__.py
├── core.py              # ingest() 核心
├── blueprint.py         # Flask blueprint，挂到主 app 或 standalone
├── standalone.py        # 单独跑时的 app factory
├── cli.py               # CLI 入口：python -m modules.pdf_ingest.cli
├── templates/
│   ├── ui.html          # 测试 UI
│   └── _partials/
│       └── thumb_grid.html
├── static/
│   └── ingest.css
└── tests/
    ├── test_core.py
    └── fixtures/
        └── tiny.pdf     # 2 页的测试 PDF
```

## 5. Blueprint（HTTP API）

| 方法 | 路径 | 行为 |
|---|---|---|
| GET  | `/ingest/` | 测试 UI 页面 |
| POST | `/ingest/upload` | 表单上传 PDF（`multipart/form-data`，字段 `pdf` + 可选 `doc_id`） |
| POST | `/ingest/run` | 对已存在的 `data/input_pdfs/<doc_id>.pdf` 触发拆页（JSON body: `{"doc_id":"xxx","dpi":300}`） |
| GET  | `/ingest/manifest/<doc_id>` | 返回已拆文档的 manifest |
| GET  | `/ingest/thumb/<doc_id>/<page>` | 返回指定页的缩略图（max-width 200px，动态生成） |
| GET  | `/ingest/page/<doc_id>/<page>` | 返回原图（动态） |

**只接受从 127.0.0.1 来的请求**（由主 web_app 的中间件保证，参见模块 11）。

## 6. CLI

```bash
python -m modules.pdf_ingest.cli \
  --pdf /data/input_pdfs/myDoc.pdf \
  --doc-id myDoc \
  --dpi 300 \
  --out /data/pages
```

输出：
```
[1/137] Rendering p001.png
[2/137] Rendering p002.png
...
Done. Wrote 137 pages to /data/pages/myDoc/
Manifest: /data/pages/myDoc/manifest.json
```

## 7. 测试 UI 设计

一个 Jinja 页面，三个区：

```
┌───────────────────────────────────────────────────────────────┐
│  [Upload form]                                                │
│  File: [ choose PDF ]    Doc ID: [ __________ ]   [Upload]   │
├───────────────────────────────────────────────────────────────┤
│  Select existing doc: [ dropdown: myDoc / demo / ... ]        │
├───────────────────────────────────────────────────────────────┤
│  Manifest summary: 137 pages @ 300 DPI, 425 MB, uploaded ...  │
│                                                               │
│  ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐        │
│  │ p001 │ │ p002 │ │ p003 │ │ p004 │ │ p005 │ │ p006 │  ...   │
│  │ thumb│ │ thumb│ │ thumb│ │ thumb│ │ thumb│ │ thumb│        │
│  └──────┘ └──────┘ └──────┘ └──────┘ └──────┘ └──────┘        │
│                                                               │
│  (点缩略图弹出 lightbox 看原图)                                 │
└───────────────────────────────────────────────────────────────┘
```

**可视化要点**：
- 缩略图 grid 一目了然看是不是拆对了（比如颠倒、空白页、双页连版）
- 点图能放大，肉眼验证清晰度足够 OCR
- Manifest 摘要给人一个总量感

## 8. 独立 Docker 容器

`docker/ingest.Dockerfile`：
```dockerfile
FROM llm-pipeline-base:latest
USER root
RUN pip install --no-cache-dir pymupdf
COPY src/modules/pdf_ingest /app/modules/pdf_ingest
USER 10001:10001
```

`compose.yaml` 片段（profile 方式，可选拉起）：
```yaml
  pdf_ingest:
    build:
      context: .
      dockerfile: docker/ingest.Dockerfile
    networks: [ llm_internal ]
    volumes:
      - ./data/input_pdfs:/data/input_pdfs:ro
      - ./data/pages:/data/pages
    profiles: [ "standalone", "all" ]
    command: >
      gunicorn -b 0.0.0.0:5102 -w 1
      'modules.pdf_ingest.standalone:create_app()'
```

注意：服务没有 `ports:` 段，外界不可达。要通过 web_app 才能访问（Web 主程序反代到它），或从宿主用 `docker compose exec` 手工调。

## 9. 单元测试

- 用 `tests/fixtures/tiny.pdf`（2 页）
- 测 `page_count == 2`
- 测 PNG 文件确实生成、尺寸 > 0
- 测 manifest 校验（sha 稳定）
- 测 idempotency：重复调用 `ingest()` 不报错、结果相同

## 10. 性能 / 故障

- **吞吐**：300 DPI 一页约 0.3–0.8 秒（CPU bound）
- **内存**：每页 peak ~200 MB（PyMuPDF 渲染时），大 PDF 不要并行
- **坏 PDF**：加密/损坏/扫描不规范，catch 并记进 manifest 的 `warnings` 数组
- **中文/特殊字符文件名**：用 SHA-256 作为 `doc_id` 默认值避免问题

## 11. 构建检查清单

- [ ] `core.ingest()` 函数写好、单测通过
- [ ] Blueprint 6 个路由全实现
- [ ] CLI 能跑
- [ ] Dockerfile 构建通过
- [ ] Standalone compose profile 能拉起、能访问测试 UI（经由反代）
- [ ] 测试 PDF 上传 → 看到缩略图 → manifest 正确
