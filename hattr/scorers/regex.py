from __future__ import annotations

import re
from typing import Any


class RegexScorer:
    def __init__(self, cfg: dict[str, Any]):
        self.events = {name: re.compile(str(pattern)) for name, pattern in cfg["events"].items()}
        self.event_names = list(self.events)

    def score(
        self,
        task: dict[str, Any],
        output: str | dict[str, Any],
        subject_error: bool,
        mock: bool = False,
    ) -> dict[str, Any]:
        if subject_error:
            return {name: None for name in self.event_names}
        text = str(output)
        return {name: bool(pattern.search(text)) for name, pattern in self.events.items()}

