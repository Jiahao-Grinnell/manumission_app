# Module 02 - pdf_ingest

> PDF page-splitting module. Converts a scanned PDF into one high-resolution PNG per page. This is the **new entry point** for the pipeline.

## 1. Purpose

The old system accepted loose page images or `.txt` files. The new system accepts **a complete PDF**. This module is responsible for:

1. Receiving a PDF uploaded by the user, or registering a large PDF that already exists in `data/input_pdfs/`.
2. Rendering each page as a PNG, defaulting to 300 DPI.
3. Generating `manifest.json` with basic metadata, source size, and per-page status.
4. Not performing any image processing or OCR. That belongs to the downstream OCR module.

## 2. Input / Output

**Input**: `data/input_pdfs/<doc_id>.pdf`

Real input PDFs may be larger than 500 MB. Browser upload is a convenience path, not the only path. For large files, the preferred flow is:

1. Put the PDF in `data/input_pdfs/`.
2. Register it through the UI or CLI by selecting the file and assigning a `doc_id`.
3. Run ingest against that existing file.

**Output**:

```text
data/pages/<doc_id>/
|-- p001.png
|-- p002.png
|-- ...
|-- p137.png
`-- manifest.json
```

Example `manifest.json`:

```json
{
  "doc_id": "historical_archive_vol3",
  "source_pdf": "historical_archive_vol3.pdf",
  "source_pdf_sha256": "3a7f...",
  "source_pdf_size_bytes": 317689337,
  "page_count": 137,
  "dpi": 300,
  "status": "complete",
  "completed_pages": 137,
  "created_at": "2026-04-17T10:23:45Z",
  "updated_at": "2026-04-17T10:45:12Z",
  "pages": [
    {"page": 1,   "filename": "p001.png", "width": 2480, "height": 3508, "size_bytes": 1456789, "status": "done"},
    {"page": 2,   "filename": "p002.png", "width": 2480, "height": 3508, "size_bytes": 1432109},
    ...
  ]
}
```

## 3. Core Algorithm

Use PyMuPDF (`fitz`). It is pure Python, fast, cross-platform, and does not require external executables:

```python
# core.py outline
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

Design decisions:

- **PNG instead of JPEG**: OCR quality is more important than file size.
- **300 DPI by default**: A practical OCR value. Below 200 is poor; above 400 has diminishing returns.
- **One file per page**: Downstream resume logic stays simple.
- **One-page-at-a-time rendering**: required for large PDFs. Do not render multiple full-size pages into memory unless an explicit concurrency limit is configured.
- **Incremental manifest updates**: after each page renders, update `manifest.json` atomically so interrupted ingest can resume without starting over.
- **No custom compression**: PyMuPDF's default PNG compression is enough.

## 4. Directory Structure

```text
src/modules/pdf_ingest/
|-- __init__.py
|-- core.py              # ingest() core
|-- blueprint.py         # Flask blueprint for main app or standalone mode
|-- standalone.py        # App factory for standalone mode
|-- cli.py               # CLI entry point: python -m modules.pdf_ingest.cli
|-- templates/
|   |-- ui.html          # Test UI
|   `-- _partials/
|       `-- thumb_grid.html
|-- static/
|   `-- ingest.css
`-- tests/
    |-- test_core.py
    `-- fixtures/
        `-- tiny.pdf     # 2-page test PDF
```

## 5. Blueprint (HTTP API)

| Method | Path | Behavior |
|---|---|---|
| GET | `/ingest/` | Test UI page |
| POST | `/ingest/upload` | Upload a PDF form (`multipart/form-data`, field `pdf` plus optional `doc_id`) |
| POST | `/ingest/register` | Register an existing file from `data/input_pdfs/` without uploading it through Flask |
| POST | `/ingest/run` | Split an existing `data/input_pdfs/<doc_id>.pdf` (JSON body: `{"doc_id":"xxx","dpi":300}`) |
| GET | `/ingest/manifest/<doc_id>` | Return the manifest for an ingested document |
| GET | `/ingest/thumb/<doc_id>/<page>` | Return a generated thumbnail for a page (max-width 200px) |
| GET | `/ingest/page/<doc_id>/<page>` | Return the original page image |

Requests are accepted only from `127.0.0.1`, enforced by the main `web_app` middleware. See module 11.

## 6. CLI

```bash
python -m modules.pdf_ingest.cli \
  --pdf /data/input_pdfs/myDoc.pdf \
  --doc-id myDoc \
  --dpi 300 \
  --out /data/pages
```

Output:

```text
[1/137] Rendering p001.png
[2/137] Rendering p002.png
...
Done. Wrote 137 pages to /data/pages/myDoc/
Manifest: /data/pages/myDoc/manifest.json
```

## 7. Test UI Design

A Jinja page with three areas:

```text
+---------------------------------------------------------------+
|  [Upload form]                                                |
|  File: [ choose PDF ]    Doc ID: [ __________ ]   [Upload]   |
+---------------------------------------------------------------+
|  Existing input PDF: [ dropdown: full input.pdf / ... ]       |
|  Doc ID: [ __________ ]   [ Register without upload ]         |
+---------------------------------------------------------------+
|  Select existing doc: [ dropdown: myDoc / demo / ... ]        |
+---------------------------------------------------------------+
|  Manifest summary: 137 pages @ 300 DPI, 425 MB, uploaded ...  |
|                                                               |
|  [p001] [p002] [p003] [p004] [p005] [p006] ...                |
|  thumb  thumb  thumb  thumb  thumb  thumb                    |
|                                                               |
|  Click a thumbnail to open a lightbox with the original image. |
+---------------------------------------------------------------+
```

Visualization goals:

- The thumbnail grid makes it obvious whether splitting worked, including upside-down pages, blank pages, or two-page spreads.
- Clicking an image allows visual inspection of whether resolution is high enough for OCR.
- The manifest summary gives a quick sense of document size and scale.
- For large PDFs, the UI should show file size, completed pages, estimated disk expansion, free-space warning, and a resume button when `manifest.json` is partial.

## 8. Standalone Docker Container

`docker/ingest.Dockerfile`:

```dockerfile
FROM llm-pipeline-base:latest
USER root
RUN pip install --no-cache-dir pymupdf
COPY src/modules/pdf_ingest /app/modules/pdf_ingest
USER 10001:10001
```

`compose.yaml` fragment using profiles:

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

Note: the service has no `ports:` block, so it is not externally reachable. Access it through `web_app`, or call it manually with `docker compose exec` from the host.

## 9. Unit Tests

- Use `tests/fixtures/tiny.pdf`, a two-page PDF.
- Assert `page_count == 2`.
- Assert PNG files are generated and dimensions are greater than zero.
- Validate the manifest, including a stable SHA.
- Test idempotency: repeated calls to `ingest()` do not fail and produce the same result.
- Add a sample-input smoke test using a reduced page range from `sample input 1.pdf` or `sample input 2.pdf`.
- Add a full-input smoke test that can be run manually against `full input.pdf` or another large local file. It should verify streaming behavior, resumability, and manifest correctness without committing the PDF to git.

## 10. Performance / Failure Modes

- **Throughput**: 300 DPI takes about 0.3 to 0.8 seconds per page and is CPU-bound.
- **Memory**: Peak memory is about 200 MB per page while PyMuPDF renders. Avoid parallel rendering for large PDFs unless a strict worker limit is configured.
- **Disk expansion**: rendered PNGs can be much larger than the source PDF. Before ingesting a large PDF, estimate required disk space and warn if free space is low.
- **Large PDFs**: files over 500 MB should be supported through `data/input_pdfs/` registration even if browser upload limits are lower.
- **Bad PDFs**: Encrypted, damaged, or irregular scans should be caught and recorded in a `warnings` array in the manifest.
- **Chinese or special-character filenames**: Use SHA-256 as the default `doc_id` to avoid path issues.

## 11. Build Checklist

- [ ] `core.ingest()` is implemented and unit tests pass.
- [ ] All seven blueprint routes are implemented.
- [ ] Existing-file registration works for PDFs already in `data/input_pdfs/`.
- [ ] CLI works.
- [ ] Dockerfile builds.
- [ ] Standalone Compose profile starts and the test UI is reachable through the proxy.
- [ ] Test PDF upload produces thumbnails and a correct manifest.
- [ ] Large-PDF resume works from a partial manifest.
- [ ] UI warns about estimated disk expansion and low free space.
