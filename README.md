# Manumission App

## Project Overview

Manumission App is an end-to-end modular Flask application for extracting information from historical slavery and manumission archival documents. It converts scanned PDF documents into images, runs OCR, page classification, named-entity recognition, metadata extraction, and place extraction, then uses an LLM to produce final CSV outputs.

The system has been refactored from monolithic Python scripts into modular services. Each module can run independently, be tested independently, and be visualized through its own UI.

Current completed runtime target: M4 / Phase 4.4. Ollama gateway is available; `pdf_ingest` can upload or register PDFs, render page PNGs, write manifests, and show thumbnails at `http://127.0.0.1:5102/ingest/`; `normalizer` can demonstrate name, place, date, evidence, name comparison, and place dedupe rules at `http://127.0.0.1:5108/normalizer/`; `aggregator` can write final CSVs and preview/download them at `http://127.0.0.1:5109/aggregate/`; `ocr` can preview preprocessing and run OCR into `data/ocr_text/<doc_id>/` at `http://127.0.0.1:5103/ocr/` when the OCR model is available; `page_classifier` can classify OCR pages, show regex override hints, and persist `pNNN.classify.json` files at `http://127.0.0.1:5104/classify/`; `name_extractor` can run the five-stage subject-name pipeline, persist `pNNN.names.json`, explain dropped candidates, and rerun one downstream stage at `http://127.0.0.1:5105/names/`; `metadata_extractor` can extract one validated `Detailed info.csv` row per named person, persist `pNNN.meta.json`, and inspect field-level evidence at `http://127.0.0.1:5106/meta/`; and `place_extractor` can extract per-person place paths, persist `pNNN.places.json`, inspect candidate, verified, date-enriched, and reconciled route rows, and download the current page or selected person as CSV at `http://127.0.0.1:5107/places/`.

## Architecture

The system consists of the following modules:

- **shared**: Core library containing the LLM client, schemas, paths, text utilities, and storage.
- **ollama_gateway**: Ollama container and model management.
- **pdf_ingest**: Splits PDFs into images.
- **ocr**: Runs OCR with a vision model.
- **page_classifier**: Classifies whether a page should be extracted.
- **name_extractor**: Extracts the names of enslaved or manumitted subjects.
- **metadata_extractor**: Extracts case metadata.
- **place_extractor**: Extracts place paths and dates.
- **normalizer**: Normalizes, validates, and deduplicates data.
- **aggregator**: Aggregates intermediate data into final CSV files.
- **orchestrator**: Pipeline orchestration and dashboard.
- **web_app**: Main Flask application and UI.

## Installation and Setup

### Prerequisites

- Docker and Docker Compose
- NVIDIA GPU available to Docker Desktop / WSL for Ollama

### Installation Steps

1. Clone the repository:
   ```bash
   git clone https://github.com/Jiahao-Grinnell/manumission_app.git
   cd manumission_app
   ```

2. Seed the models (internet access is required the first time):
   ```bash
   ./scripts/seed_model.sh qwen2.5:14b-instruct
   ./scripts/seed_model.sh glm-ocr:latest
   ```

3. Start the services:
   ```bash
   docker compose up -d
   ```

4. During Phase 1.2, verify Ollama with:
   ```bash
   bash scripts/verify_gateway.sh
   ```

The browser URL `http://127.0.0.1:5000` becomes available after the Web App module is implemented.

To run the current PDF ingest UI:

```bash
docker compose --profile ingest up -d --build pdf_ingest
```

Then open:

```text
http://127.0.0.1:5102/ingest/
```

To run the current normalizer UI:

```bash
docker compose --profile normalizer up -d --build normalizer
```

Then open:

```text
http://127.0.0.1:5108/normalizer/
```

To run the current aggregator UI:

```bash
docker compose --profile aggregator up -d --build aggregator
```

Then open:

```text
http://127.0.0.1:5109/aggregate/
```

To run the current OCR UI:

```bash
docker compose --profile ocr up -d --build ocr
```

Then open:

```text
http://127.0.0.1:5103/ocr/
```

The preprocessing preview works without a live OCR model call. Full OCR requires `glm-ocr:latest` to be present in runtime Ollama.

To run the current page classifier UI:

```bash
docker compose --profile classifier up -d --build page_classifier
```

Then open:

```text
http://127.0.0.1:5104/classify/
```

The page classifier reads from `data/ocr_text/<doc_id>/`, writes to `data/intermediate/<doc_id>/`, and supports both single-page and whole-document runs.

To run the current name extractor UI:

```bash
docker compose --profile names up -d --build name_extractor
```

Then open:

```text
http://127.0.0.1:5105/names/
```

The name extractor reads OCR text from `data/ocr_text/<doc_id>/` plus classifier results from `data/intermediate/<doc_id>/pNNN.classify.json`, writes `pNNN.names.json`, and preserves every pipeline stage for debugging. The UI does not accept freeform `doc_id` entry: it auto-lists only documents that have at least one page with `should_extract=true`. If a document is missing from the dropdown, run the page classifier on that document first and make sure at least one page was kept for extraction.

To run the current metadata extractor UI:

```bash
docker compose --profile meta up -d --build metadata_extractor
```

Then open:

```text
http://127.0.0.1:5106/meta/
```

The metadata extractor reads OCR text from `data/ocr_text/<doc_id>/`, page-classifier results from `data/intermediate/<doc_id>/pNNN.classify.json`, and name results from `data/intermediate/<doc_id>/pNNN.names.json`. It writes `pNNN.meta.json`, stores one row per named person for `Detailed info.csv`, and keeps validation plus evidence alongside each extracted field for debugging.

To run the current place extractor UI:

```bash
docker compose --profile places up -d --build place_extractor
```

Then open:

```text
http://127.0.0.1:5107/places/
```

The place extractor reads OCR text from `data/ocr_text/<doc_id>/`, page-classifier results from `data/intermediate/<doc_id>/pNNN.classify.json`, and name results from `data/intermediate/<doc_id>/pNNN.names.json`. It writes `pNNN.places.json`, stores one or more place rows per named person for `name place.csv`, keeps candidate, verified, date-enriched, and reconciled route rows for debugging, and exposes direct CSV download buttons for the current page or selected person.

## Usage

1. Upload a PDF on the `/upload` page.
2. For very large PDFs, place the file in `data/input_pdfs/` and register it from the input page instead of uploading through the browser.
3. Monitor pipeline progress on the dashboard.
4. Inspect persisted intermediate artifacts such as rendered page images, OCR text, and per-page JSON as needed.
5. Download the generated CSV files.

## Development

For detailed module specifications, see the corresponding files in the `docs/` directory.

### Module List

- [00_shared.md](docs/00_shared.md): Shared core library
- [01_ollama_gateway.md](docs/01_ollama_gateway.md): Ollama gateway
- [02_pdf_ingest.md](docs/02_pdf_ingest.md): PDF ingest
- [03_ocr.md](docs/03_ocr.md): OCR
- [04_page_classifier.md](docs/04_page_classifier.md): Page classifier
- [05_name_extractor.md](docs/05_name_extractor.md): Name extractor
- [06_metadata_extractor.md](docs/06_metadata_extractor.md): Metadata extractor
- [07_place_extractor.md](docs/07_place_extractor.md): Place extractor
- [08_normalizer.md](docs/08_normalizer.md): Normalizer
- [09_aggregator.md](docs/09_aggregator.md): Aggregator
- [10_orchestrator.md](docs/10_orchestrator.md): Orchestrator
- [11_web_app.md](docs/11_web_app.md): Web application

### Build Order

Build the system in the order described in [process.md](docs/process.md).

### Running Locally

See [running.md](docs/running.md) for Windows, Docker Desktop, WSL, and visual test UI instructions.

## Contributing

Contributions are welcome. Please follow the build checklist for each module.

## License

[License information]
