from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import requests
from requests.exceptions import ConnectionError, ReadTimeout, RequestException

from .config import settings
from .schemas import CallStats
from .text_utils import extract_json, render_prompt


DEFAULT_JSON_REPAIR_PROMPT = """Return only valid JSON matching this schema shape:
{schema}

TEXT TO FIX:
<<<{bad}>>>
"""


class OllamaClient:
    """Small Ollama /api/generate client with retry and JSON repair support."""

    def __init__(
        self,
        url: str | None = None,
        model: str | None = None,
        num_predict: int | None = None,
        num_ctx: int | None = None,
        *,
        base_url: str | None = None,
        connect_timeout: int | None = None,
        read_timeout: int | None = None,
        max_retries: int = 3,
        retry_backoff_seconds: float = 2.0,
        session: requests.Session | None = None,
        prompt_dir: Path | None = None,
    ) -> None:
        self.url = url or settings.OLLAMA_URL
        self.base_url = (base_url or settings.OLLAMA_BASE_URL).rstrip("/")
        self.model = model or settings.OLLAMA_MODEL
        self.num_predict = int(num_predict or settings.NUM_PREDICT)
        self.num_ctx = num_ctx if num_ctx is not None else settings.NUM_CTX
        self.timeout = (
            int(connect_timeout or settings.OLLAMA_CONNECT_TIMEOUT),
            int(read_timeout or settings.OLLAMA_READ_TIMEOUT),
        )
        self.max_retries = max_retries
        self.retry_backoff_seconds = retry_backoff_seconds
        self.prompt_dir = prompt_dir or settings.PROMPT_DIR
        self.session = session or requests.Session()
        self.session.headers.update({"Connection": "keep-alive"})

    def _payload(self, prompt: str, *, num_predict: int | None = None, images: list[str] | None = None) -> dict[str, Any]:
        options: dict[str, Any] = {
            "temperature": 0,
            "num_predict": int(num_predict or self.num_predict),
        }
        if self.num_ctx:
            options["num_ctx"] = int(self.num_ctx)
        payload: dict[str, Any] = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": options,
        }
        if images:
            payload["images"] = images
        return payload

    @staticmethod
    def _extract_response(data: dict[str, Any]) -> str:
        error = data.get("error")
        if isinstance(error, str) and error.strip():
            raise RuntimeError(error.strip())
        if isinstance(data.get("response"), str):
            return data["response"]
        message = data.get("message")
        if isinstance(message, dict) and isinstance(message.get("content"), str):
            return message["content"]
        for key in ("output", "text", "content"):
            value = data.get(key)
            if isinstance(value, str):
                return value
        return ""

    def generate(self, prompt: str, stats: CallStats | None = None, *, num_predict: int | None = None) -> str:
        stats = stats or CallStats()
        last_error: Exception | None = None

        for attempt in range(1, self.max_retries + 1):
            try:
                stats.model_calls += 1
                response = self.session.post(
                    self.url,
                    json=self._payload(prompt, num_predict=num_predict),
                    timeout=self.timeout,
                )
                response.raise_for_status()
                return self._extract_response(response.json()).strip()
            except (ConnectionError, ReadTimeout, RequestException, RuntimeError, ValueError) as exc:
                last_error = exc
                if attempt < self.max_retries:
                    time.sleep(self.retry_backoff_seconds * attempt)

        raise RuntimeError(f"Ollama call failed after {self.max_retries} attempts: {last_error}")

    def generate_vision(
        self,
        prompt: str,
        image_b64: str,
        stats: CallStats | None = None,
        *,
        num_predict: int | None = None,
    ) -> str:
        stats = stats or CallStats()
        last_error: Exception | None = None

        for attempt in range(1, self.max_retries + 1):
            try:
                stats.model_calls += 1
                response = self.session.post(
                    self.url,
                    json=self._payload(prompt, num_predict=num_predict, images=[image_b64]),
                    timeout=self.timeout,
                )
                response.raise_for_status()
                return self._extract_response(response.json()).strip()
            except (ConnectionError, ReadTimeout, RequestException, RuntimeError, ValueError) as exc:
                last_error = exc
                if attempt < self.max_retries:
                    time.sleep(self.retry_backoff_seconds * attempt)

        raise RuntimeError(f"Ollama vision call failed after {self.max_retries} attempts: {last_error}")

    def generate_json(
        self,
        prompt: str,
        schema_hint: str,
        stats: CallStats | None = None,
        *,
        num_predict: int | None = None,
    ) -> Any | None:
        stats = stats or CallStats()
        raw = self.generate(prompt, stats, num_predict=num_predict)
        parsed = extract_json(raw)
        if parsed is not None:
            return parsed

        repaired = self.generate(
            render_prompt(self._json_repair_prompt(), schema=schema_hint, bad=raw),
            stats,
            num_predict=800,
        )
        stats.repair_calls += 1
        return extract_json(repaired)

    def wait_ready(self, timeout_s: int = 240, interval_s: float = 2.0) -> None:
        deadline = time.time() + timeout_s
        last_error: Exception | str | None = None
        version_url = f"{self.base_url}/api/version"

        while time.time() < deadline:
            try:
                response = self.session.get(version_url, timeout=(5, 10))
                if response.status_code == 200:
                    return
                last_error = f"status={response.status_code} body={response.text[:200]}"
            except Exception as exc:
                last_error = exc
            time.sleep(interval_s)

        raise RuntimeError(f"Ollama not ready after {timeout_s}s. Last error: {last_error}")

    def _json_repair_prompt(self) -> str:
        prompt_path = self.prompt_dir / "json_repair.txt"
        try:
            return prompt_path.read_text(encoding="utf-8")
        except OSError:
            return DEFAULT_JSON_REPAIR_PROMPT

