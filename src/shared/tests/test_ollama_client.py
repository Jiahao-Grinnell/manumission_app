from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from shared.ollama_client import OllamaClient
from shared.schemas import CallStats


class FakeResponse:
    def __init__(self, payload: dict[str, Any], status_code: int = 200) -> None:
        self.payload = payload
        self.status_code = status_code
        self.text = str(payload)

    def json(self) -> dict[str, Any]:
        return self.payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeSession:
    def __init__(self, post_payloads: list[dict[str, Any]] | None = None) -> None:
        self.headers: dict[str, str] = {}
        self.post_payloads = post_payloads or []
        self.posts: list[dict[str, Any]] = []
        self.gets: list[str] = []

    def post(self, url: str, json: dict[str, Any], timeout: tuple[int, int]) -> FakeResponse:
        self.posts.append({"url": url, "json": json, "timeout": timeout})
        if not self.post_payloads:
            raise AssertionError("No fake post payload configured")
        return FakeResponse(self.post_payloads.pop(0))

    def get(self, url: str, timeout: tuple[int, int]) -> FakeResponse:
        self.gets.append(url)
        return FakeResponse({"version": "fake"})


class OllamaClientTests(unittest.TestCase):
    def test_generate_posts_expected_payload(self) -> None:
        stats = CallStats()
        session = FakeSession([{"response": " hello "}])
        client = OllamaClient(
            url="http://ollama:11434/api/generate",
            model="model-a",
            num_predict=123,
            num_ctx=4096,
            session=session,  # type: ignore[arg-type]
            retry_backoff_seconds=0,
        )

        self.assertEqual(client.generate("prompt", stats), "hello")
        self.assertEqual(stats.model_calls, 1)
        payload = session.posts[0]["json"]
        self.assertEqual(payload["model"], "model-a")
        self.assertEqual(payload["options"]["num_predict"], 123)
        self.assertEqual(payload["options"]["num_ctx"], 4096)

    def test_generate_json_repairs_bad_json(self) -> None:
        stats = CallStats()
        session = FakeSession([
            {"response": "not json"},
            {"response": '```json\n{"ok": true}\n```'},
        ])
        with TemporaryDirectory() as tmp:
            prompt_dir = Path(tmp)
            (prompt_dir / "json_repair.txt").write_text("schema={schema}\nbad={bad}", encoding="utf-8")
            client = OllamaClient(session=session, prompt_dir=prompt_dir, retry_backoff_seconds=0)  # type: ignore[arg-type]
            self.assertEqual(client.generate_json("prompt", '{"ok":true}', stats), {"ok": True})

        self.assertEqual(stats.model_calls, 2)
        self.assertEqual(stats.repair_calls, 1)
        self.assertIn("not json", session.posts[1]["json"]["prompt"])

    def test_generate_vision_includes_images(self) -> None:
        stats = CallStats()
        session = FakeSession([{"response": "text"}])
        client = OllamaClient(session=session, retry_backoff_seconds=0)  # type: ignore[arg-type]
        self.assertEqual(client.generate_vision("prompt", "abc", stats), "text")
        self.assertEqual(session.posts[0]["json"]["images"], ["abc"])

    def test_wait_ready_calls_version_endpoint(self) -> None:
        session = FakeSession()
        client = OllamaClient(base_url="http://ollama:11434", session=session, retry_backoff_seconds=0)  # type: ignore[arg-type]
        client.wait_ready(timeout_s=1, interval_s=0)
        self.assertEqual(session.gets, ["http://ollama:11434/api/version"])


if __name__ == "__main__":
    unittest.main()

