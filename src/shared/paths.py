from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

from .config import settings


_UNSAFE_DOC_ID_CHARS = re.compile(r"[\\/:*?\"<>|\x00-\x1f]")


def normalize_doc_id(doc_id: str) -> str:
    cleaned = _UNSAFE_DOC_ID_CHARS.sub("_", (doc_id or "").strip())
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    if not cleaned:
        raise ValueError("doc_id must not be empty")
    if cleaned in {".", ".."} or ".." in cleaned.split("/"):
        raise ValueError(f"unsafe doc_id: {doc_id!r}")
    return cleaned


@dataclass(frozen=True)
class DocumentPaths:
    doc_id: str
    data_root: Path = settings.DATA_ROOT

    @property
    def pdf(self) -> Path:
        return self.data_root / "input_pdfs" / f"{self.doc_id}.pdf"

    @property
    def pages_dir(self) -> Path:
        return self.data_root / "pages" / self.doc_id

    @property
    def ocr_dir(self) -> Path:
        return self.data_root / "ocr_text" / self.doc_id

    @property
    def inter_dir(self) -> Path:
        return self.data_root / "intermediate" / self.doc_id

    @property
    def output_dir(self) -> Path:
        return self.data_root / "output" / self.doc_id

    @property
    def logs_dir(self) -> Path:
        return self.data_root / "logs" / self.doc_id

    @property
    def audit_dir(self) -> Path:
        return self.data_root / "audit" / self.doc_id

    def manifest(self) -> Path:
        return self.pages_dir / "manifest.json"

    def page_image(self, page: int) -> Path:
        return self.pages_dir / f"p{page:03d}.png"

    def ocr_text(self, page: int) -> Path:
        return self.ocr_dir / f"p{page:03d}.txt"

    def classify(self, page: int) -> Path:
        return self.inter_dir / f"p{page:03d}.classify.json"

    def names(self, page: int) -> Path:
        return self.inter_dir / f"p{page:03d}.names.json"

    def meta(self, page: int) -> Path:
        return self.inter_dir / f"p{page:03d}.meta.json"

    def places(self, page: int) -> Path:
        return self.inter_dir / f"p{page:03d}.places.json"


def doc_paths(doc_id: str) -> DocumentPaths:
    return DocumentPaths(doc_id=normalize_doc_id(doc_id))
