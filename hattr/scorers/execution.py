from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from typing import Any


CHECK_TEMPLATE = """\
import sys
try:
    import solution
except Exception as e:
    sys.stderr.write("IMPORT_ERROR:%r" % e); sys.exit(3)
if not hasattr(solution, {name!r}):
    sys.stderr.write("NO_FUNC"); sys.exit(4)
try:
    exec({tests!r}, solution.__dict__)
except Exception as e:
    sys.stderr.write("TEST_FAIL:%r" % e); sys.exit(5)
print("OK"); sys.exit(0)
"""


class ExecutionScorer:
    def __init__(self, cfg: dict[str, Any]):
        self.code_field = str(cfg.get("code_field", "code"))
        self.entry = str(cfg["entry"])
        self.tests_field = str(cfg["tests_field"])
        self.timeout = int(cfg.get("timeout", 15))
        self.sandbox = str(cfg.get("sandbox", "seatbelt"))
        if self.sandbox not in ("seatbelt", "none"):
            raise ValueError("execution scorer sandbox must be one of: seatbelt, none")
        self.event_map = dict(cfg.get("event_map", {}))
        self.event_names = list(self.event_map)

    def score(
        self,
        task: dict[str, Any],
        output: str | dict[str, Any],
        subject_error: bool,
        mock: bool = False,
    ) -> dict[str, Any]:
        if subject_error:
            return {name: None for name in self.event_names}
        if mock:
            builtins = {"compile": True, "passed": True}
            return self._events_from_builtins(builtins)
        code = ""
        if isinstance(output, dict):
            code = str(output.get(self.code_field, ""))
        else:
            code = str(output)
        name = self.entry.format(**task)
        tests = task.get(self.tests_field, [])
        result = score_code(code, name, list(tests), self.timeout, self.sandbox)
        events = self._events_from_builtins(result)
        events.update({"compile": result["compile"], "passed": result["passed"]})
        return events

    def _events_from_builtins(self, values: dict[str, bool]) -> dict[str, Any]:
        events: dict[str, Any] = {}
        for name, expr in self.event_map.items():
            events[name] = _safe_eval(str(expr), values)
        return events


def score_code(
    code: str,
    name: str,
    tests: list[str],
    timeout: int = 15,
    sandbox: str = "seatbelt",
) -> dict[str, bool]:
    with tempfile.TemporaryDirectory(prefix="hattr-codegen-run-") as d:
        with open(os.path.join(d, "solution.py"), "w", encoding="utf-8") as f:
            f.write(code)
        check = CHECK_TEMPLATE.format(name=name, tests="\n".join(tests))
        with open(os.path.join(d, "check.py"), "w", encoding="utf-8") as f:
            f.write(check)
        cmd = _command(d, sandbox)
        try:
            proc = subprocess.run(
                cmd,
                cwd=d,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=timeout,
                preexec_fn=_resource_limiter(timeout),
            )
            rc = proc.returncode
        except subprocess.TimeoutExpired:
            return {"compile": True, "passed": False}
    if rc == 0:
        return {"compile": True, "passed": True}
    if rc in (3, 4):
        return {"compile": False, "passed": False}
    if rc == 5:
        return {"compile": True, "passed": False}
    return {"compile": False, "passed": False}


def _safe_eval(expr: str, values: dict[str, Any]) -> bool:
    return bool(eval(expr, {"__builtins__": {}}, dict(values)))


def _command(workdir: str, sandbox: str) -> list[str]:
    if sandbox == "none":
        return [sys.executable, "-S", "check.py"]
    if sandbox != "seatbelt":
        raise ValueError("execution scorer sandbox must be one of: seatbelt, none")
    sandbox_exec = shutil.which("sandbox-exec")
    if sandbox_exec is None:
        raise RuntimeError(
            "execution scorer configured with sandbox=seatbelt, but sandbox-exec is not on PATH"
        )
    profile_path = os.path.join(workdir, "profile.sb")
    with open(profile_path, "w", encoding="utf-8") as f:
        f.write(_seatbelt_profile(os.path.abspath(workdir)))
    return [sandbox_exec, "-f", profile_path, "python3", "-S", "check.py"]


def _seatbelt_profile(workdir: str) -> str:
    escaped = workdir.replace("\\", "\\\\").replace('"', '\\"')
    return (
        "(version 1)\n"
        "(allow default)\n"
        "(deny network*)\n"
        "(deny file-write*)\n"
        f'(allow file-write* (subpath "{escaped}") (literal "/dev/null") '
        '(subpath "/private/var/folders"))\n'
    )


def _resource_limiter(timeout: int) -> Any:
    if os.name != "posix":
        return None
    try:
        import resource
    except ImportError:
        return None

    def limit_resources() -> None:
        cpu_seconds = max(1, int(timeout))
        _set_resource_limit(resource, resource.RLIMIT_CPU, cpu_seconds)
        _set_resource_limit(resource, resource.RLIMIT_AS, 1024 * 1024 * 1024)
        _set_resource_limit(resource, resource.RLIMIT_FSIZE, 16 * 1024 * 1024)

    return limit_resources


def _set_resource_limit(resource: Any, limit: int, value: int) -> None:
    try:
        _, hard = resource.getrlimit(limit)
        effective_hard = value if hard == resource.RLIM_INFINITY else min(value, hard)
        effective_soft = min(value, effective_hard)
        resource.setrlimit(limit, (effective_soft, effective_hard))
    except (OSError, ValueError):
        pass
