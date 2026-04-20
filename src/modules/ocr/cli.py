from __future__ import annotations

import argparse
from pathlib import Path

from shared.config import settings

from .core import run_folder


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run OCR over a folder of page images.")
    parser.add_argument("--in_dir", required=True, help="Folder containing page images.")
    parser.add_argument("--out_dir", required=True, help="Folder for OCR text output.")
    parser.add_argument("--ollama_url", default=settings.OLLAMA_URL, help="Ollama /api/generate URL.")
    parser.add_argument("--model", default=settings.OCR_MODEL, help="OCR model name.")
    parser.add_argument("--no_resume", action="store_true", help="Rerun even when output text exists.")
    parser.add_argument("--no_debug", action="store_true", help="Disable debug artifacts.")
    parser.add_argument("--no_tile", action="store_true", help="Send the full preprocessed page instead of vertical tiles.")
    parser.add_argument("--max_new_tokens", type=int, default=1200)
    parser.add_argument("--prompt", default=None)
    parser.add_argument("--timeout_s", type=int, default=240)
    parser.add_argument("--no_wait", action="store_true", help="Do not check Ollama readiness before processing.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    def progress(action: str, page: int, total: int, path: Path) -> None:
        print(f"[{action.upper()}] page={page} file={path.name} total={total}", flush=True)

    manifest = run_folder(
        args.in_dir,
        args.out_dir,
        model=args.model,
        ollama_generate_url=args.ollama_url,
        resume=not args.no_resume,
        debug=not args.no_debug,
        tile=not args.no_tile,
        max_new_tokens=args.max_new_tokens,
        prompt=args.prompt,
        timeout_s=args.timeout_s,
        wait_ready=not args.no_wait,
        progress=progress,
    )
    print(f"Done. Status: {manifest['status']}. Completed {manifest['completed_pages']}/{manifest['total_pages']} pages.")
    print(f"Manifest: {Path(args.out_dir) / 'manifest.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
