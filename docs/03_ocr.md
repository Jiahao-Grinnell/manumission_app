# 模块 03 — ocr

> 页面图像 → OCR 文本。流水线里第一个调 LLM 的模块，也是最重的视觉处理环节。

## 1. 目的

对 `data/pages/<doc_id>/p*.png` 逐页 OCR，产出 `data/ocr_text/<doc_id>/p*.txt`。

底层逻辑**继承原 `glm_ocr_ollama.py` 的思路**：传统 CV 预处理（去歪斜、增强、裁剪、切片）→ 切片送视觉模型 → 合并文本 → 兜底整图单次调用。

## 2. 输入 / 输出

**输入**：
- `data/pages/<doc_id>/p*.png`（来自 02 pdf_ingest）
- 模型名（默认 `glm-ocr:latest`）
- 运行参数（tile、max_new_tokens 等）

**输出**：
```
data/ocr_text/<doc_id>/
├── p001.txt
├── p002.txt
├── ...
├── run_status.log
└── _debug/                    # debug=True 时
    ├── p001__prep_0.png       # 预处理后切片 0
    ├── p001__prep_1.png       # 预处理后切片 1
    ├── p001__resp_0.json      # 对应模型原始响应
    ├── p001__resp_1.json
    └── p001__raw_0.txt        # 纯文本响应
```

**约定**：
- 文本文件每一行尽量保留原版面断行
- 无法 OCR 的页写入字面值 `[OCR_EMPTY]`（不留空文件，让下游知道是"试过但空"）

## 3. 核心算法（继承原系统）

```
page.png
  │
  ▼ enhance_gray              # 中值模糊去背景 + CLAHE + unsharp
  │
  ▼ deskew                    # 用 minAreaRect 校正歪斜
  │
  ▼ crop_foreground           # 基于自适应阈值的前景裁剪
  │
  ▼ resize_long_side          # 保证长边 ≥ 1800（OCR 需要足够分辨率）
  │
  ▼ split_vertical_with_overlap(parts=2, overlap=200)   # 切成上下两片带重叠
  │
  ▼ for each slice:
  │     base64(slice) → ollama vision generate(prompt, image)
  │     → cleanup_ocr_text(response)
  │
  ▼ join non-empty slice texts with "\n\n"
  │
  ▼ if empty: fallback 整图单次调用
  │
  ▼ if still empty: write "[OCR_EMPTY]"
```

原代码里的 OCR prompt 直接原样搬：

```
You are an OCR engine. Transcribe ALL visible text from the image.
Rules:
- Output ONLY the text (no markdown, no code fences).
- Preserve line breaks as best as possible.
- Do not add commentary or explanations.
- If you cannot read any text, output exactly: [OCR_EMPTY]
```

抽到 `config/prompts/ocr.txt`。

## 4. 目录结构

```
src/modules/ocr/
├── __init__.py
├── core.py                   # run_folder / ocr_page 主流程
├── preprocessing.py          # enhance_gray / deskew / crop_foreground / resize / tile
├── blueprint.py              # Flask
├── standalone.py
├── cli.py
├── templates/
│   ├── ui.html
│   └── _partials/
│       ├── page_picker.html
│       └── preprocess_strip.html
├── static/
│   └── ocr.css
└── tests/
    ├── test_preprocessing.py    # 只测纯 CV，无 LLM
    ├── test_core_mocked.py      # 用 mock OllamaClient
    └── fixtures/
        ├── clean_page.png
        ├── skewed_page.png
        └── noisy_page.png
```

`preprocessing.py` 的每个函数都能独立调用和测试，与 LLM 解耦。

## 5. Blueprint API

| 方法 | 路径 | 行为 |
|---|---|---|
| GET  | `/ocr/` | 测试 UI |
| GET  | `/ocr/docs` | 列出所有有拆页产物的 doc_id |
| GET  | `/ocr/pages/<doc_id>` | 列出该 doc 所有可 OCR 的页面 |
| POST | `/ocr/preview/<doc_id>/<page>` | 只跑预处理（不调 LLM），返回 5 张中间图 base64 |
| POST | `/ocr/run-single/<doc_id>/<page>` | 跑单页完整 OCR（调 LLM） |
| POST | `/ocr/run-all/<doc_id>` | 异步跑整 doc，返回 job_id |
| GET  | `/ocr/debug/<doc_id>/<page>` | 返回该页的 debug 目录内容（如果开启 debug） |
| GET  | `/ocr/text/<doc_id>/<page>` | 返回已 OCR 的文本 |
| GET  | `/ocr/status/<doc_id>` | 该 doc 的 OCR 进度 |

## 6. CLI

完全兼容原脚本参数：
```bash
python -m modules.ocr.cli \
  --in_dir /data/pages/myDoc \
  --out_dir /data/ocr_text/myDoc \
  --model glm-ocr:latest \
  --ollama_url http://ollama:11434/api/generate \
  --no_debug \
  --max_new_tokens 1200
```

## 7. 测试 UI 设计（**这是本模块的核心可视化**）

UI 要实现"肉眼 debug OCR 的每一步"。布局：

```
┌───────────────────────────────────────────────────────────────────┐
│  Doc: [ myDoc ▼ ]   Page: [ p012 ▼ ]   Model: [ glm-ocr ▼ ]       │
│  [ Preview only ]   [ Run OCR on this page ]   [ Run all pages ]  │
├───────────────────────────────────────────────────────────────────┤
│  Preprocessing pipeline                                           │
│  ┌─────┐ → ┌─────┐ → ┌─────┐ → ┌─────┐ → ┌──────┬──────┐          │
│  │ orig│   │enhc.│   │desk.│   │crop │   │tile0 │tile1 │          │
│  │     │   │     │   │     │   │     │   │      │      │          │
│  └─────┘   └─────┘   └─────┘   └─────┘   └──────┴──────┘          │
│  每张图点击放大；hover 显示尺寸 / 耗时                              │
├───────────────────────────────────────────────────────────────────┤
│  Model responses                                                  │
│  ┌────────────────────────┐  ┌────────────────────────┐          │
│  │ Tile 0 response        │  │ Tile 1 response        │          │
│  │ elapsed: 4.3s          │  │ elapsed: 3.9s          │          │
│  │ chars: 1203            │  │ chars: 872             │          │
│  │ ┌────────────────────┐ │  │ ┌────────────────────┐ │          │
│  │ │ <OCR text here>    │ │  │ │ <OCR text here>    │ │          │
│  │ │ ...                │ │  │ │ ...                │ │          │
│  │ └────────────────────┘ │  │ └────────────────────┘ │          │
│  │ [Raw JSON ▼]           │  │ [Raw JSON ▼]           │          │
│  └────────────────────────┘  └────────────────────────┘          │
├───────────────────────────────────────────────────────────────────┤
│  Final output (joined)                                            │
│  ┌───────────────────────────────────────────────────────────────┐│
│  │ <merged page text>                                             ││
│  │ ...                                                            ││
│  └───────────────────────────────────────────────────────────────┘│
│  [ Download .txt ]                                                │
└───────────────────────────────────────────────────────────────────┘
```

**可视化要点**：
1. **5 连图**是最关键的调试入口。看预处理管线哪一步出问题了一目了然（比如 deskew 过矫正、crop 切掉了正文）。
2. **模型响应并排显示**：能看出两个切片是否都给出了合理结果，或者某一片完全空。
3. **Raw JSON 折叠**：默认不显示、点开可看（含模型 `done_reason`、`eval_count` 等）。
4. **elapsed / chars 小 badge**：快速扫一眼性能和产量。

## 8. Docker

`docker/ocr.Dockerfile`：
```dockerfile
FROM llm-pipeline-base:latest
USER root
RUN apt-get update && apt-get install -y --no-install-recommends libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*
COPY requirements/ocr.txt /tmp/ocr.txt
RUN pip install --no-cache-dir -r /tmp/ocr.txt
COPY src/modules/ocr /app/modules/ocr
USER 10001:10001
```

`requirements/ocr.txt`:
```
opencv-python-headless>=4.8.0
numpy>=1.26.0
```

Compose 片段：
```yaml
  ocr:
    build:
      context: .
      dockerfile: docker/ocr.Dockerfile
    depends_on:
      ollama:
        condition: service_healthy
    networks: [ llm_internal ]
    volumes:
      - ./data/pages:/data/pages:ro
      - ./data/ocr_text:/data/ocr_text
      - ./config/prompts:/app/config/prompts:ro
    profiles: [ "standalone", "all" ]
    command: >
      gunicorn -b 0.0.0.0:5103 -w 1 --timeout 1800
      'modules.ocr.standalone:create_app()'
```

`--timeout 1800`：单页 OCR 可能几十秒到几分钟，不要让 gunicorn 杀掉 worker。

## 9. 测试

**单元测试**（无 LLM）：
- `preprocessing.py` 每个函数对 clean/skewed/noisy fixture 验证输出尺寸、不抛异常
- `cleanup_ocr_text` 对含 markdown fence 的响应能清理
- `should_skip_existing` 对空文件、`[OCR_EMPTY]`、正常文本的行为

**集成测试**（需要 Ollama）：
- 放一张清晰英文页，期望 OCR 出某段字符串的子串
- 放一张空白页，期望输出 `[OCR_EMPTY]`

这种集成测试用 `pytest -m integration` 标记，CI 里可跳过。

## 10. 性能 / 故障

- **单页耗时**：glm-ocr 7B 在 16GB GPU 上每切片约 3–10s，每页两切片约 6–20s
- **大批量**：默认串行。要并发需要 Ollama 侧支持 multi-slot，或跑多实例
- **OOM**：图像太大导致 vision 模型 OOM → 调小 `preprocess_long` 参数
- **模型幻觉**：vision 模型偶尔给出"文档看起来是 X"这种说明性文字而非 OCR → prompt 里已明令禁止，但出现时要在 `cleanup_ocr_text` 里加模式剥离

## 11. 构建检查清单

- [ ] `preprocessing.py` 所有函数搬过来 + 独立单测通过
- [ ] `core.py` 的 `ocr_page` / `run_folder` 能跑
- [ ] `cleanup_ocr_text` 去除 fence、去除说明性文字
- [ ] Blueprint 所有路由实现
- [ ] CLI 与原脚本参数兼容
- [ ] 测试 UI：能看到 5 连预处理图 + 两切片响应 + 最终合并文本
- [ ] 断点续跑（`should_skip_existing`）生效
- [ ] Debug 开启时 `_debug/` 目录生成对应文件
- [ ] 独立容器能拉起
