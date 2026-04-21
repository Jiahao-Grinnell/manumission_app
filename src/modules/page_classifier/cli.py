from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from shared.config import settings

from .core import classify_file, run_folder


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Classify OCR text pages into extract/skip and report type.")
    parser.add_argument("--in_dir", type=Path, required=True, help="Directory containing pNNN.txt OCR files.")
    parser.add_argument("--out_dir", type=Path, required=True, help="Directory for pNNN.classify.json outputs.")
    parser.add_argument("--model", default=settings.OLLAMA_MODEL, help=f"Text model to use. Default: {settings.OLLAMA_MODEL}")
    parser.add_argument("--page", type=int, help="Run only one page number, e.g. 12 for p012.txt.")
    parser.add_argument("--report-type", dest="report_type", help="Force a final report type for debugging.")
    parser.add_argument("--force", action="store_true", help="Rerun even if the JSON output already exists.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    args.out_dir.mkdir(parents=True, exist_ok=True)

    try:
        if args.page:
            source = args.in_dir / f"p{args.page:03d}.txt"
            if not source.exists():
                raise FileNotFoundError(f"Missing OCR text file: {source}")
            target = args.out_dir / f"p{args.page:03d}.classify.json"
            result = classify_file(
                source,
                target,
                model=args.model,
                report_type_override=args.report_type,
            )
            print(json.dumps(result.as_dict(), indent=2, ensure_ascii=False))
            print(f"Wrote {target}")
            return 0

        def progress(action: str, page: int, total: int, path: Path) -> None:
            verb = {
                "skip": "Skipping",
                "done": "Classified",
                "error": "Error",
            }.get(action, action)
            print(f"[{page}/{total}] {verb} {path.name}", flush=True)

        summary = run_folder(
            args.in_dir,
            args.out_dir,
            model=args.model,
            report_type_override=args.report_type,
            resume=not args.force,
            progress=progress,
        )
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
