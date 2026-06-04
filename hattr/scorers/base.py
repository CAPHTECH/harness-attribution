from __future__ import annotations

from typing import Any, Protocol


class Scorer(Protocol):
    event_names: list[str]

    def score(
        self,
        task: dict[str, Any],
        output: str | dict[str, Any],
        subject_error: bool,
        mock: bool = False,
    ) -> dict[str, Any]:
        """Return event fields plus optional diagnostic fields."""

