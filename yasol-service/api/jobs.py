"""Per-request scratch workspaces and subprocess execution."""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Sequence

from settings import MAX_OUTPUT_CHARS, WORK_ROOT, YASOL_INI


@dataclass
class RunResult:
    exit_code: int | None
    timed_out: bool
    stdout: str
    stderr: str
    duration_seconds: float


def _truncate(text: str) -> str:
    if len(text) <= MAX_OUTPUT_CHARS:
        return text
    return text[:MAX_OUTPUT_CHARS] + f"\n... [truncated, {len(text) - MAX_OUTPUT_CHARS} more characters]"


@contextmanager
def workspace() -> Iterator[Path]:
    """A fresh directory per request, removed afterwards.

    Both Yasol and YasolSolver.jl read `./Yasol.ini` from the current working directory,
    so a copy is placed in every workspace. Isolation also keeps Yasol from appending a
    counter to the solution file name when a `.sol` already exists.
    """
    WORK_ROOT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=WORK_ROOT) as tmp:
        path = Path(tmp)
        if YASOL_INI.is_file():
            shutil.copy(YASOL_INI, path / "Yasol.ini")
        yield path


def run(cmd: Sequence[str | Path], cwd: Path, timeout: float, env: dict[str, str] | None = None) -> RunResult:
    """Run a command, killing the whole process group if it overruns `timeout`.

    The group kill matters for the Julia endpoint: Julia spawns Yasol as a child, and
    killing only Julia would leave the solver running.
    """
    started = time.monotonic()
    process = subprocess.Popen(
        [str(part) for part in cmd],
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        errors="replace",
        start_new_session=True,
        env={**os.environ, **(env or {})},
    )

    timed_out = False
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        except ProcessLookupError:
            pass
        stdout, stderr = process.communicate()

    return RunResult(
        exit_code=process.returncode,
        timed_out=timed_out,
        stdout=_truncate(stdout),
        stderr=_truncate(stderr),
        duration_seconds=round(time.monotonic() - started, 3),
    )


def collect_artifacts(workdir: Path, skip: set[str] | None = None) -> dict[str, str]:
    """Text files the job left behind, keyed by name."""
    skip = skip or set()
    artifacts: dict[str, str] = {}
    for path in sorted(workdir.iterdir()):
        if not path.is_file() or path.name in skip:
            continue
        try:
            artifacts[path.name] = _truncate(path.read_text())
        except (UnicodeDecodeError, OSError):
            continue
    return artifacts
