from __future__ import annotations

import argparse
import sys
from pathlib import Path

from shared.config import settings
from shared.paths import normalize_doc_id

from .core import PdfIngestError, ingest


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Render a PDF into page PNGs and a manifest.")
    parser.add_argument("--pdf", required=True, help="Path to the input PDF.")
    parser.add_argument("--doc-id", help="Document id. Defaults to the PDF filename stem.")
    parser.add_argument("--dpi", type=int, default=300, help="Render DPI, default: 300.")
    parser.add_argument(
        "--out",
        type=Path,
        default=settings.pages_root,
        help="Root output directory. The document folder is created under this path.",
    )
    parser.add_argument("--start-page", type=int, default=1, help="First page to render, 1-based.")
    parser.add_argument("--end-page", type=int, help="Last page to render, 1-based.")
    parser.add_argument("--force", action="store_true", help="Rerender pages even if manifest says done.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    pdf_path = Path(args.pdf)
    doc_id = normalize_doc_id(args.doc_id or pdf_path.stem)
    out_dir = Path(args.out) / doc_id

    def progress(action: str, page: int, total: int, path: Path) -> None:
        verb = {
            "skip": "Skipping",
            "render": "Rendered",
            "error": "Error",
        }.get(action, action)
        print(f"[{page}/{total}] {verb} {path.name}", flush=True)

    try:
        manifest = ingest(
            pdf_path,
            out_dir,
            dpi=args.dpi,
            doc_id=doc_id,
            start_page=args.start_page,
            end_page=args.end_page,
            force=args.force,
            progress=progress,
        )
    except (OSError, ValueError, PdfIngestError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(f"Done. Status: {manifest['status']}. Wrote {manifest['completed_pages']} pages to {out_dir}/")
    print(f"Manifest: {out_dir / 'manifest.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
