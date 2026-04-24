from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from modules.aggregator.core import aggregate
from modules.metadata_extractor.core import run_folder as run_metadata_folder
from modules.name_extractor.core import run_folder as run_names_folder
from modules.ocr.core import run_folder as run_ocr_folder
from modules.page_classifier.core import run_folder as run_classifier_folder
from modules.pdf_ingest.core import ingest
from modules.place_extractor.core import run_folder as run_places_folder
from shared.config import settings
from shared.paths import doc_paths


ProgressCallback = Callable[[str, int, int, Path], None]


def run_stage(
    stage: str,
    doc_id: str,
    *,
    source_pdf: str | Path | None = None,
    dpi: int = 300,
    resume: bool = True,
    ocr_model: str | None = None,
    text_model: str | None = None,
    progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    if settings.ORCH_MODE != "inproc":
        raise NotImplementedError(f"Unsupported ORCH_MODE for Phase 5: {settings.ORCH_MODE}")

    paths = doc_paths(doc_id)
    if stage == "ingest":
        if source_pdf is None:
            raise FileNotFoundError(f"No PDF source provided for ingest of {doc_id}")
        return ingest(
            source_pdf,
            paths.pages_dir,
            dpi=dpi,
            doc_id=doc_id,
            force=not resume,
            progress=progress,
        )
    if stage == "ocr":
        return run_ocr_folder(
            paths.pages_dir,
            paths.ocr_dir,
            model=ocr_model,
            resume=resume,
            progress=progress,
        )
    if stage == "classify":
        return run_classifier_folder(
            paths.ocr_dir,
            paths.inter_dir,
            model=text_model,
            resume=resume,
            progress=progress,
        )
    if stage == "names":
        return run_names_folder(
            paths.ocr_dir,
            paths.inter_dir,
            paths.inter_dir,
            model=text_model,
            resume=resume,
            progress=progress,
        )
    if stage == "meta":
        return run_metadata_folder(
            paths.ocr_dir,
            paths.inter_dir,
            paths.inter_dir,
            model=text_model,
            resume=resume,
            progress=progress,
        )
    if stage == "places":
        return run_places_folder(
            paths.ocr_dir,
            paths.inter_dir,
            paths.inter_dir,
            model=text_model,
            resume=resume,
            progress=progress,
        )
    if stage == "aggregate":
        result = aggregate(doc_id)
        return {
            "doc_id": result.doc_id,
            "status": "complete",
            "detail_path": str(result.detail_path),
            "place_path": str(result.place_path),
            "status_path": str(result.status_path),
            "summary_path": str(result.summary_path),
            "stats": result.stats,
        }
    raise ValueError(f"Unsupported orchestration stage: {stage}")
