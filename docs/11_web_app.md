# 模块 11 — web_app

> Flask 主程序。把所有模块的 blueprint 挂成一个统一的 web UI，是**唯一**对宿主机（`127.0.0.1` only）暴露端口的服务。

## 1. 目的

提供用户入口：
- PDF 上传
- Dashboard（继承 orchestrator 的 UI 作为首页）
- 跳转到各模块测试 UI
- 作业列表与历史
- CSV 下载
- 全局导航和鉴权

这一层**不写业务逻辑**，它只做"挂 blueprint + 导航 + 鉴权 + 启动 gunicorn"。

## 2. 两种部署形态

### 2.1 单体模式（推荐日常用）

主 web_app 进程里挂全部 blueprint（02~10）。一个容器跑所有东西，Ollama 独立容器。

好处：无 HTTP 序列化开销；调试简单；资源占用低。
坏处：某模块崩溃波及全局。

### 2.2 微服务模式（调试或隔离时用）

每个模块独立容器（有自己的 blueprint + standalone app），web_app 只是反向代理。orchestrator 用 `ORCH_MODE=http` 通过内部网络调。

好处：模块隔离、独立扩缩容、独立重启。
坏处：部署复杂、调试慢。

**同一套代码两种模式**。切换靠 compose profile + 环境变量 `ORCH_MODE`。

## 3. 目录结构

```
src/web_app/
├── __init__.py
├── app.py               # create_app() factory
├── register.py          # 挂所有 blueprint
├── routes.py            # 主页 / 上传页 / jobs 列表 / 健康检查
├── auth.py              # 本地访问限制中间件
├── errors.py            # 错误处理器
├── templates/
│   ├── base.html        # 全局布局 + 导航
│   ├── home.html        # 首页（= dashboard）
│   ├── upload.html
│   ├── jobs.html
│   └── download.html
├── static/
│   ├── pico.min.css     # 简约框架
│   ├── app.css
│   └── app.js
├── wsgi.py              # gunicorn 入口
└── tests/
    ├── test_routes.py
    └── test_auth.py
```

## 4. app factory

```python
# app.py
from flask import Flask
from .register import register_blueprints
from .auth import local_only
from .errors import register_error_handlers
from shared.config import settings
from shared.logging_setup import setup_logger

def create_app() -> Flask:
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.config["MAX_CONTENT_LENGTH"] = 500 * 1024 * 1024   # 500 MB PDF 上限
    app.config["UPLOAD_FOLDER"] = settings.DATA_ROOT / "input_pdfs"
    setup_logger("web", settings.DATA_ROOT / "logs")

    app.before_request(local_only)
    register_blueprints(app)
    register_error_handlers(app)

    from . import routes
    app.register_blueprint(routes.bp)
    return app
```

## 5. register.py

```python
def register_blueprints(app):
    # 每个模块暴露自己的 bp
    from modules.pdf_ingest.blueprint import bp as ingest_bp
    from modules.ocr.blueprint import bp as ocr_bp
    from modules.page_classifier.blueprint import bp as classify_bp
    from modules.name_extractor.blueprint import bp as names_bp
    from modules.metadata_extractor.blueprint import bp as meta_bp
    from modules.place_extractor.blueprint import bp as places_bp
    from modules.normalizer.blueprint import bp as norm_bp
    from modules.aggregator.blueprint import bp as agg_bp
    from orchestrator.blueprint import bp as orch_bp

    app.register_blueprint(ingest_bp,   url_prefix="/ingest")
    app.register_blueprint(ocr_bp,      url_prefix="/ocr")
    app.register_blueprint(classify_bp, url_prefix="/classify")
    app.register_blueprint(names_bp,    url_prefix="/names")
    app.register_blueprint(meta_bp,     url_prefix="/meta")
    app.register_blueprint(places_bp,   url_prefix="/places")
    app.register_blueprint(norm_bp,     url_prefix="/normalizer")
    app.register_blueprint(agg_bp,      url_prefix="/aggregate")
    app.register_blueprint(orch_bp,     url_prefix="/orchestrate")
```

**微服务模式下**：`register.py` 只挂 `orch_bp`，其他由反向代理（`web_app` 做 reverse proxy 到对应模块 service）。

## 6. auth.py —— 本地访问限制

双重保险（即使 compose 配错，也不至于 LAN 能访问）：

```python
from flask import request, abort
from ipaddress import ip_address

def local_only():
    # 浏览器直连时 remote_addr = 客户端 IP
    # 经反代时 X-Forwarded-For 可能带值；但我们的 reverse proxy 在容器内
    remote = request.remote_addr
    if remote is None:
        abort(403)
    addr = ip_address(remote)
    if not (addr.is_loopback or addr.is_private):
        # Docker 桥接一般给 172.17.0.x，算 private → 允许
        # LAN/互联网 → 拒绝
        abort(403)
```

容器外层还有 compose 的 `127.0.0.1:5000:5000` 绑定做第一层屏障。auth.py 是第二层。

## 7. routes.py

| 方法 | 路径 | 页面 |
|---|---|---|
| GET  | `/` | 首页（dashboard） |
| GET  | `/upload` | PDF 上传表单 |
| POST | `/upload` | 接收文件，保存到 `data/input_pdfs/`，重定向到 `/orchestrate/run?doc_id=...` |
| GET  | `/jobs` | 所有历史作业列表 |
| GET  | `/download/<doc_id>` | 下载该 doc 的 CSV（调 aggregator） |
| GET  | `/health` | liveness：返回 200 |
| GET  | `/ready` | readiness：检查 Ollama 可达 + 所有模块蓝图注册 |

## 8. base.html 导航

全站共享一个 nav：

```html
<nav>
  <ul>
    <li><a href="/">Dashboard</a></li>
    <li><a href="/upload">Upload PDF</a></li>
    <li><a href="/jobs">Jobs</a></li>
  </ul>
  <ul>
    <li><strong>Modules</strong></li>
    <li><a href="/ingest/">Ingest</a></li>
    <li><a href="/ocr/">OCR</a></li>
    <li><a href="/classify/">Classify</a></li>
    <li><a href="/names/">Names</a></li>
    <li><a href="/meta/">Meta</a></li>
    <li><a href="/places/">Places</a></li>
    <li><a href="/normalizer/">Normalizer</a></li>
    <li><a href="/aggregate/">Aggregate</a></li>
  </ul>
</nav>
```

每个模块的测试 UI 都通过这个 nav 可达。

## 9. Docker

`docker/web.Dockerfile`：
```dockerfile
FROM llm-pipeline-base:latest
USER root
COPY requirements/web.txt /tmp/web.txt
RUN pip install --no-cache-dir -r /tmp/web.txt
# 单体模式：所有模块代码都带进来
COPY src/shared /app/shared
COPY src/modules /app/modules
COPY src/orchestrator /app/orchestrator
COPY src/web_app /app/web_app
COPY config /app/config
USER 10001:10001
ENV PYTHONPATH=/app
ENV FLASK_APP=web_app.app:create_app
EXPOSE 5000
```

`compose.yaml` 片段：
```yaml
  web_app:
    build:
      context: .
      dockerfile: docker/web.Dockerfile
    depends_on:
      ollama:
        condition: service_healthy
    networks:
      - llm_internal
      - llm_frontend     # ← 这是 web_app 独有的
    ports:
      - "127.0.0.1:5000:5000"   # ← 只绑 localhost，不绑 0.0.0.0
    volumes:
      - ./data:/data
      - ./config:/app/config:ro
    environment:
      - ORCH_MODE=inproc          # 单体：直接函数调用
      - OLLAMA_URL=http://ollama:11434/api/generate
      - OLLAMA_MODEL=qwen2.5:14b-instruct
    command: >
      gunicorn -b 0.0.0.0:5000 -w 4 --threads 2 --timeout 3600
      --access-logfile - --error-logfile -
      'web_app.app:create_app()'
    restart: unless-stopped

networks:
  llm_internal:
    internal: true
  llm_frontend:           # 只用来让 web_app 暴露 5000 到宿主
    driver: bridge
```

**关键**：
- `ports: "127.0.0.1:5000:5000"` — 绑 localhost，**不**绑 `0.0.0.0`。LAN 和公网打不到。
- gunicorn `-w 4 --threads 2`：4 进程 × 2 线程，适合 Flask + 大量同时打开的 dashboard 页
- `--timeout 3600`：OCR 单次调用可能几分钟，不能让 worker 被杀
- `restart: unless-stopped`：自动恢复

## 10. 微服务模式额外配置

`compose.yaml` 里加 `--profile micro`，启所有模块服务并让 web_app 做反代：

```yaml
  web_app:
    ...
    environment:
      - ORCH_MODE=http
      - MODULE_URL_OCR=http://ocr:5103
      - MODULE_URL_CLASSIFY=http://page_classifier:5104
      ...
    profiles: [ "all", "micro" ]
```

在 `register.py` 里检测 `ORCH_MODE=http`，不再挂 blueprint，而是通过 `flask-reverse-proxy` 或自写一个简单的代理把 `/ocr/*` 请求转发到 `http://ocr:5103/ocr/*`。

## 11. 启动流程

第一次：
```bash
./scripts/seed_model.sh qwen2.5:14b-instruct   # 下模型（只这步需要外网）
docker compose up -d                            # 启动全栈
# 浏览器打开 http://127.0.0.1:5000
```

日常：
```bash
docker compose up -d
# 浏览器打开 http://127.0.0.1:5000
# 在 /upload 页传 PDF → 自动跳到 dashboard → 看着跑完 → /download/<doc_id> 下 CSV
```

## 12. 测试

- `test_routes.py`：基本 200/302 检查；上传小 PDF 能重定向到 dashboard
- `test_auth.py`：模拟 LAN IP 请求应 403；loopback 应 200
- `test_blueprints_registered.py`：确认所有 9 个 blueprint 都挂上了

## 13. 可观测性

- `/health` 和 `/ready` 分离
- 结构化日志：每个请求带 `X-Request-Id`（中间件生成 UUID）
- `/jobs` 页聚合 `data/logs/` 下所有 job.json

## 14. 安全检查清单

- [ ] `ports:` 严格 `127.0.0.1:5000:5000`，不写 `0.0.0.0` 或仅 `5000:5000`
- [ ] `auth.local_only` 生效，远程 IP 请求被 403
- [ ] 上传文件类型仅 `application/pdf`，size ≤ 500 MB
- [ ] Upload 文件名 `secure_filename`，不允许路径遍历
- [ ] gunicorn 非 root 用户跑
- [ ] `data/` 只容器内挂载，宿主不暴露
- [ ] Ollama 容器无 `ports:`

## 15. 构建检查清单

- [ ] `create_app()` factory 写好
- [ ] `register_blueprints` 挂 9 个 bp
- [ ] `local_only` 中间件生效
- [ ] `base.html` 导航含所有模块链接
- [ ] 上传页→启动 job→跳 dashboard 流畅
- [ ] `/jobs` 列表页列出所有已完成和进行中
- [ ] `/download/<doc_id>` 下载 zip
- [ ] `127.0.0.1` 访问成功、LAN IP 访问失败（双重验证：compose 绑定 + auth）
- [ ] gunicorn 多 worker 跑稳
- [ ] 单体模式 + 微服务模式都能启动
- [ ] 端到端：上传 PDF → 全流程完成 → 下载 CSV 验证内容与原脚本等价
