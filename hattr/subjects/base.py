from __future__ import annotations

from typing import Any, Protocol


class SubjectAdapter(Protocol):
    def generate(
        self, prompt: str, output_schema: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Return {output, error, meta...}; output may be str or dict."""

