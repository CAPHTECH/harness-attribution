from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
from typing import Any


class CodexSubject:
    def __init__(self, cfg: dict[str, Any]):
        self.model = cfg.get("model")
        self.oss_local_provider = cfg.get("oss_local_provider") or cfg.get("local_provider")

    def generate(
        self, prompt: str, output_schema: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        started = time.perf_counter()
        with tempfile.TemporaryDirectory(prefix="hattr-codex-") as scratch:
            cmd = [
                "codex",
                "exec",
                "--ephemeral",
                "-s",
                "read-only",
                "--skip-git-repo-check",
                "--ignore-user-config",
                "-C",
                scratch,
            ]
            last_message_file = os.path.join(scratch, "last_message.txt")
            if output_schema is None:
                cmd.extend(["-o", last_message_file])
            else:
                schema_file = os.path.join(scratch, "schema.json")
                with open(schema_file, "w", encoding="utf-8") as f:
                    json.dump(output_schema, f, ensure_ascii=False)
                cmd.extend(["--output-schema", schema_file])
            if self.oss_local_provider:
                cmd.extend(["--oss", "--local-provider", str(self.oss_local_provider)])
            if self.model:
                cmd.extend(["-m", str(self.model)])
            cmd.append(prompt)
            env = os.environ.copy()
            # Do NOT override CODEX_HOME: codex auth (auth.json) lives there; resetting it to an
            # empty dir causes 401 Unauthorized. --ignore-user-config already ignores config.toml.
            for key in (
                "CODEX_COMPANION_SESSION_ID",
                "CODEX_THREAD_ID",
                "CODEX_SANDBOX",
                "CODEX_SANDBOX_NETWORK_DISABLED",
            ):
                env.pop(key, None)
            with open(os.devnull, "rb") as stdin:
                proc = subprocess.run(
                    cmd,
                    stdin=stdin,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    env=env,
                )

            output: str | dict[str, Any] = ""
            if output_schema is None and os.path.exists(last_message_file):
                with open(last_message_file, "r", encoding="utf-8") as f:
                    output = f.read().strip()
            elif output_schema is not None and proc.returncode == 0:
                try:
                    output = json.loads(proc.stdout.strip())
                except json.JSONDecodeError:
                    output = ""

        duration_s = time.perf_counter() - started
        is_error = proc.returncode != 0 or output in ("", {})
        return {
            "output": output,
            "error": is_error,
            "returncode": proc.returncode,
            "stderr": proc.stderr,
            "stdout": proc.stdout,
            "duration_s": duration_s,
        }
