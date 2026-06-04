from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Any


class OpenAICompatSubject:
    def __init__(self, cfg: dict[str, Any]):
        self.base_url = str(cfg["base_url"]).rstrip("/")
        self.model = cfg.get("model")
        self.api_key_env = cfg.get("api_key_env")

    def generate(
        self, prompt: str, output_schema: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        started = time.perf_counter()
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
        }
        if output_schema is not None:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "hattr_output",
                    "strict": True,
                    "schema": output_schema,
                },
            }
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.api_key_env and os.environ.get(str(self.api_key_env)):
            headers["Authorization"] = f"Bearer {os.environ[str(self.api_key_env)]}"
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=body,
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            content = data["choices"][0]["message"]["content"]
            output: str | dict[str, Any]
            if output_schema is None:
                output = content
            else:
                output = json.loads(content)
            error = output in ("", {})
            stderr = ""
        except (urllib.error.URLError, KeyError, json.JSONDecodeError, TimeoutError) as exc:
            output = ""
            error = True
            stderr = str(exc)
        return {
            "output": output,
            "error": error,
            "returncode": 0 if not error else 1,
            "stderr": stderr,
            "stdout": "",
            "duration_s": time.perf_counter() - started,
        }

