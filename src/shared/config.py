from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _optional_int(value: str | None) -> int | None:
    if value is None or value.strip() == "":
        return None
    return int(value)


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return int(raw)


@dataclass(frozen=True)
class Settings:
    DATA_ROOT: Path = Path(os.environ.get("DATA_ROOT", "/data"))
    PROMPT_DIR: Path = Path(os.environ.get("PROMPT_DIR", "/app/config/prompts"))

    OLLAMA_URL: str = os.environ.get("OLLAMA_URL", "http://ollama:11434/api/generate")
    OLLAMA_BASE_URL: str = os.environ.get("OLLAMA_BASE_URL", "http://ollama:11434")
    OLLAMA_MODEL: str = os.environ.get("OLLAMA_MODEL", "qwen2.5:14b-instruct")
    OCR_MODEL: str = os.environ.get("OCR_MODEL", "glm-ocr:latest")
    NUM_PREDICT: int = _int_env("OLLAMA_NUM_PREDICT", 1200)
    NUM_CTX: int | None = _optional_int(os.environ.get("OLLAMA_NUM_CTX"))
    OLLAMA_CONNECT_TIMEOUT: int = _int_env("OLLAMA_CONNECT_TIMEOUT", 10)
    OLLAMA_READ_TIMEOUT: int = _int_env("OLLAMA_READ_TIMEOUT", 600)

    MAX_UPLOAD_BYTES: int = _int_env("MAX_UPLOAD_BYTES", 1024 * 1024 * 1024)
    ORCH_MODE: str = os.environ.get("ORCH_MODE", "inproc")
    ORCH_MODULE_URLS_JSON: str = os.environ.get("ORCH_MODULE_URLS_JSON", "{}")

    @property
    def input_pdfs_dir(self) -> Path:
        return self.DATA_ROOT / "input_pdfs"

    @property
    def pages_root(self) -> Path:
        return self.DATA_ROOT / "pages"

    @property
    def ocr_root(self) -> Path:
        return self.DATA_ROOT / "ocr_text"

    @property
    def intermediate_root(self) -> Path:
        return self.DATA_ROOT / "intermediate"

    @property
    def output_root(self) -> Path:
        return self.DATA_ROOT / "output"

    @property
    def logs_root(self) -> Path:
        return self.DATA_ROOT / "logs"

    @property
    def audit_root(self) -> Path:
        return self.DATA_ROOT / "audit"


settings = Settings()
