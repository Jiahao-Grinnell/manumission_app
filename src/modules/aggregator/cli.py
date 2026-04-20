from __future__ import annotations

import argparse
from pathlib import Path

from .core import aggregate


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Aggregate intermediate JSON into final CSV files.")
    parser.add_argument("--doc-id", help="Document id using the standard /data layout.")
    parser.add_argument("--inter_dir", type=Path, help="Intermediate JSON directory.")
    parser.add_argument("--out_dir", type=Path, help="Output CSV directory.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    result = aggregate(args.doc_id, inter_dir=args.inter_dir, out_dir=args.out_dir)
    print(f"Aggregated {result.doc_id}")
    print(f"Detailed rows: {result.stats['detail_rows']} -> {result.detail_path}")
    print(f"Place rows: {result.stats['place_rows']} -> {result.place_path}")
    print(f"Status rows: {result.stats['status_rows']} -> {result.status_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
