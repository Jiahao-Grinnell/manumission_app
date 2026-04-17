# Manumission App

## 项目概述

Manumission App 是一个端到端的模块化 Flask 应用程序，用于从历史奴隶/解放档案文献中提取信息。它将扫描的 PDF 文档处理为图像，进行 OCR、页面分类、命名实体识别、元数据提取和地点提取，使用 LLM 生成最终的 CSV 输出。

该系统从单体 Python 脚本重构为模块化服务，每个模块独立运行、可测试和可视化。

## 架构

系统由以下模块组成：

- **shared**: 核心库，包含 LLM 客户端、模式、路径、文本工具、存储。
- **ollama_gateway**: Ollama 容器和模型管理。
- **pdf_ingest**: PDF 拆分为图像。
- **ocr**: 使用视觉模型进行 OCR。
- **page_classifier**: 分类页面是否需要提取。
- **name_extractor**: 提取被奴役/解放的主体人名。
- **metadata_extractor**: 提取案件元数据。
- **place_extractor**: 提取地点路径和日期。
- **normalizer**: 数据规范化、验证和去重。
- **aggregator**: 聚合为最终 CSV。
- **orchestrator**: 流水线编排与仪表板。
- **web_app**: 主 Flask 应用与 UI。

## 安装与设置

### 先决条件

- Docker 和 Docker Compose
- GPU 用于 Ollama（可选但推荐）

### 安装步骤

1. 克隆仓库：
   ```bash
   git clone https://github.com/Jiahao-Grinnell/manumission_app.git
   cd manumission_app
   ```

2. 种子模型（首次需要外网）：
   ```bash
   ./scripts/seed_model.sh qwen2.5:14b-instruct
   ```

3. 启动服务：
   ```bash
   docker compose up -d
   ```

4. 打开浏览器：http://127.0.0.1:5000

## 使用方法

1. 在 `/upload` 页面上传 PDF。
2. 监控仪表板上的流水线进度。
3. 下载生成的 CSV 文件。

## 开发

每个模块的详细规范请参见 `docs/` 目录中的相应文件。

### 模块列表

- [00_shared.md](docs/00_shared.md): 共享核心库
- [01_ollama_gateway.md](docs/01_ollama_gateway.md): Ollama 网关
- [02_pdf_ingest.md](docs/02_pdf_ingest.md): PDF 摄取
- [03_ocr.md](docs/03_ocr.md): OCR
- [04_page_classifier.md](docs/04_page_classifier.md): 页面分类器
- [05_name_extractor.md](docs/05_name_extractor.md): 名称提取器
- [06_metadata_extractor.md](docs/06_metadata_extractor.md): 元数据提取器
- [07_place_extractor.md](docs/07_place_extractor.md): 地点提取器
- [08_normalizer.md](docs/08_normalizer.md): 规范化器
- [09_aggregator.md](docs/09_aggregator.md): 聚合器
- [10_orchestrator.md](docs/10_orchestrator.md): 编排器
- [11_web_app.md](docs/11_web_app.md): Web 应用

### 构建顺序

请按照 [process.md](docs/process.md) 中的指导顺序构建系统。

## 贡献

欢迎贡献！请遵循每个模块的构建检查清单。

## 许可证

[许可证信息]