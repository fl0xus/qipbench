"""HTTP API around the Yasol QIP solver.

Two endpoints:

    POST /solve/qlp     — upload a .qlp instance, get the solution back
    POST /solve/julia   — run Julia code that drives Yasol through YasolSolver.jl

`/solve/julia` executes arbitrary code inside the container. It runs unprivileged in a
throwaway directory, but that is process isolation, not a sandbox — do not expose this
service to an untrusted network.
"""

from __future__ import annotations

import math
import re
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from jobs import RunResult, collect_artifacts, run, workspace
from settings import (
    DEFAULT_TIME_LIMIT_SECONDS,
    EXAMPLES_DIR,
    JULIA_BIN,
    JULIA_ENV,
    MAX_TIMEOUT_SECONDS,
    MAX_UPLOAD_BYTES,
    PROVENANCE_FILE,
    TIMEOUT_GRACE_SECONDS,
    YASOL_BIN,
    YASOL_INI,
)
from solution import collect_solutions

app = FastAPI(
    title="Yasol service",
    description=__doc__,
    version="1.0.0",
)

# Yasol only understands `--name=value` with an integer value; anything else is silently
# ignored by its argument loop. The allowlist turns that silence into a 400.
ALLOWED_OPTIONS = {
    "timeLimit", "showSolution", "outputFile", "useGMI", "useCover", "usePump",
    "useMonotones", "isSimplyRestricted", "useCglRootCuts", "showInfo", "showWarning",
    "showError", "maintainPv", "useLPCuts", "useLazyLP", "useShadow", "useLimitedLP",
    "useAlphabeta", "useStrongBranching", "reduceStrongBranching", "useConflictGraph",
}
_OPTION_PATTERN = re.compile(r"^--(?P<name>[A-Za-z]+)=(?P<value>-?\d+)$")

# Yasol reports these on stdout regardless of the solution file.
_RESULT_LINE = re.compile(r"^RESULT:\s*(?P<value>\S+)", re.MULTILINE)
_STATUS_LINE = re.compile(r"^Solution Status:\s*(?P<status>.+)$", re.MULTILINE)

# For an instance with no solution, Yasol prints "Solution Status: OPTIMAL" and a
# sentinel objective of ±2^61 (yInterface.cc: defineNegativeInfinity(-(1<<61))), and
# writes no solution file. Taken at face value that reads as a solved instance, so the
# sentinel magnitude is what actually distinguishes the two.
_INFINITY_SENTINEL = float(1 << 61)
_SENTINEL_THRESHOLD = _INFINITY_SENTINEL / 2


class Variable(BaseModel):
    name: str
    index: str | None = None
    value: float | None = None
    block: int | None = None


class Solution(BaseModel):
    file_name: str
    status: str | None = None
    objective_value: float | None = None
    gap: float | None = None
    dual_bound: float | None = None
    runtime_seconds: float | None = None
    decision_nodes: str | None = None
    variables: list[Variable] = Field(default_factory=list)
    raw: str | None = None
    parse_error: str | None = None


class QlpResponse(BaseModel):
    status: str | None = Field(None, description="Solution status, from the .sol file or stdout")
    objective_value: float | None = None
    note: str | None = Field(None, description="Set when the raw solver output needed correcting")
    variables: list[Variable] = Field(default_factory=list)
    solution: Solution | None = Field(None, description="Full parsed solution file, if one was written")
    exit_code: int | None = None
    timed_out: bool = False
    duration_seconds: float = 0.0
    command: list[str] = Field(default_factory=list)
    stdout: str = ""
    stderr: str = ""


class JuliaRequest(BaseModel):
    code: str = Field(..., description="Julia source executed in a fresh working directory")
    timeout: int | None = Field(None, description=f"Wall-clock limit in seconds (max {MAX_TIMEOUT_SECONDS})")
    files: dict[str, str] = Field(
        default_factory=dict,
        description="Extra files written into the working directory before the run, name -> content",
    )


class JuliaResponse(BaseModel):
    exit_code: int | None = None
    timed_out: bool = False
    duration_seconds: float = 0.0
    stdout: str = ""
    stderr: str = ""
    solutions: list[Solution] = Field(default_factory=list)
    artifacts: dict[str, str] = Field(default_factory=dict)


def _validate_options(options: list[str]) -> list[str]:
    validated = []
    for option in options:
        match = _OPTION_PATTERN.match(option.strip())
        if not match:
            raise HTTPException(400, f"Malformed option {option!r}; expected --name=<integer>.")
        if match["name"] not in ALLOWED_OPTIONS:
            raise HTTPException(400, f"Unknown option {match['name']!r}. Allowed: {sorted(ALLOWED_OPTIONS)}")
        validated.append(match.group(0))
    return validated


def _resolve_timeout(requested: int | None, default: int) -> int:
    timeout = default if requested is None else requested
    if timeout < 1:
        raise HTTPException(400, "Timeout must be at least 1 second.")
    if timeout > MAX_TIMEOUT_SECONDS:
        raise HTTPException(400, f"Timeout exceeds the service limit of {MAX_TIMEOUT_SECONDS} seconds.")
    return timeout


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "yasol": YASOL_BIN.is_file(),
        "julia": JULIA_BIN.is_file(),
        "julia_env": JULIA_ENV.is_dir(),
    }


@app.get("/version")
def version() -> dict:
    """Everything a benchmark run has to record to be reproducible.

    Ground-truth objective values come out of this container, so a results file that
    does not name the solver revision, its parameter file and the applied limits cannot
    be checked later. Copy this block into every run's metadata.
    """
    provenance = {}
    if PROVENANCE_FILE.is_file():
        for line in PROVENANCE_FILE.read_text().splitlines():
            key, _, value = line.partition("=")
            if value:
                provenance[key.strip()] = value.strip()

    return {
        "service_version": app.version,
        "solver_commits": provenance,
        "yasol_ini": YASOL_INI.read_text() if YASOL_INI.is_file() else None,
        "limits": {
            "max_timeout_seconds": MAX_TIMEOUT_SECONDS,
            "default_time_limit_seconds": DEFAULT_TIME_LIMIT_SECONDS,
            "timeout_grace_seconds": TIMEOUT_GRACE_SECONDS,
            "max_upload_bytes": MAX_UPLOAD_BYTES,
        },
    }


@app.get("/examples")
def examples() -> dict:
    """The .qlp instances bundled with Yasol, handy for a first request."""
    if not EXAMPLES_DIR.is_dir():
        return {"examples": []}
    return {"examples": sorted(p.name for p in EXAMPLES_DIR.glob("*.qlp"))}


@app.get("/examples/{name}")
def example(name: str) -> dict:
    path = EXAMPLES_DIR / name
    if path.parent != EXAMPLES_DIR or not path.is_file():
        raise HTTPException(404, f"No example named {name!r}.")
    return {"name": name, "content": path.read_text()}


@app.post("/solve/qlp", response_model=QlpResponse)
async def solve_qlp(
    file: UploadFile = File(..., description="QLP instance file"),
    time_limit: int | None = Form(None, description="Yasol --timeLimit in seconds"),
    options: list[str] = Form(default=[], description="Extra Yasol flags, e.g. --isSimplyRestricted=1"),
) -> QlpResponse:
    payload = await file.read()
    if not payload:
        raise HTTPException(400, "Uploaded file is empty.")
    if len(payload) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"Instance exceeds {MAX_UPLOAD_BYTES} bytes.")

    solver_time_limit = _resolve_timeout(time_limit, DEFAULT_TIME_LIMIT_SECONDS)
    extra_options = _validate_options(options)

    instance_name = Path(file.filename or "instance.qlp").name
    command = [
        str(YASOL_BIN),
        instance_name,
        "--outputFile=1",
        "--showSolution=1",
        f"--timeLimit={solver_time_limit}",
        *extra_options,
    ]

    def _solve() -> tuple[RunResult, list[dict]]:
        with workspace() as workdir:
            (workdir / instance_name).write_bytes(payload)
            # Yasol treats --timeLimit as a soft limit checked between search nodes, so
            # the hard kill gets extra headroom on top.
            outcome = run(command, cwd=workdir, timeout=solver_time_limit + TIMEOUT_GRACE_SECONDS)
            return outcome, collect_solutions(workdir)

    # Solving blocks for as long as the time limit allows. Off the event loop it goes,
    # otherwise a single request would stall every other one for minutes.
    result, solutions = await run_in_threadpool(_solve)

    solution = Solution(**solutions[0]) if solutions else None

    # No solution file is written for infeasible instances or when the run is cut short,
    # so stdout is the fallback for status and objective value.
    status = solution.status if solution else None
    objective = solution.objective_value if solution else None
    if status is None:
        match = _STATUS_LINE.search(result.stdout)
        status = match["status"].strip() if match else None
    if objective is None:
        match = _RESULT_LINE.search(result.stdout)
        if match:
            try:
                objective = float(match["value"])
            except ValueError:
                objective = None

    note = None

    # inf and nan have no JSON representation; passing one on would turn a perfectly good
    # solver run into a 500. Drop the value and say so.
    if objective is not None and not math.isfinite(objective):
        note = f"Yasol reported a non-finite objective ({objective}); reported here as null."
        objective = None

    # Yasol reaches "no solution" on two separate paths and names them differently:
    # UNSAT when it detects it outright (Yasol.cpp status dispatch), and OPTIMAL with a
    # sentinel objective when the universal player forces a violation. Both are reported
    # as INFEASIBLE so callers do not have to know which code path ran.
    if status == "UNSAT":
        status = "INFEASIBLE"
        note = "Yasol reported UNSAT; normalised to INFEASIBLE (same meaning, different code path)."
        objective = None

    if objective is not None and abs(objective) >= _SENTINEL_THRESHOLD:
        note = (
            f"Yasol reported the sentinel objective {objective} (±2^61) and wrote no solution "
            f"file, which means no solution exists. Its own status line still says "
            f"{status!r}; the status reported here is corrected to INFEASIBLE."
        )
        status = "INFEASIBLE"
        objective = None

    return QlpResponse(
        status=status,
        objective_value=objective,
        note=note,
        variables=solution.variables if solution else [],
        solution=solution,
        exit_code=result.exit_code,
        timed_out=result.timed_out,
        duration_seconds=result.duration_seconds,
        command=command,
        stdout=result.stdout,
        stderr=result.stderr,
    )


@app.post("/solve/julia", response_model=JuliaResponse)
def solve_julia(request: JuliaRequest) -> JuliaResponse:
    if not request.code.strip():
        raise HTTPException(400, "No code supplied.")
    timeout = _resolve_timeout(request.timeout, DEFAULT_TIME_LIMIT_SECONDS)

    with workspace() as workdir:
        for name, content in request.files.items():
            if not name or Path(name).name != name:
                raise HTTPException(400, f"Invalid file name {name!r}; plain names only, no paths.")
            (workdir / name).write_text(content)

        script = workdir / "job.jl"
        script.write_text(request.code)

        result = run(
            [JULIA_BIN, f"--project={JULIA_ENV}", "--startup-file=no", script.name],
            cwd=workdir,
            timeout=timeout,
            env={"YASOL_BIN": str(YASOL_BIN)},
        )

        solutions = collect_solutions(workdir)
        artifacts = collect_artifacts(workdir, skip={"Yasol.ini", script.name})

    return JuliaResponse(
        exit_code=result.exit_code,
        timed_out=result.timed_out,
        duration_seconds=result.duration_seconds,
        stdout=result.stdout,
        stderr=result.stderr,
        solutions=[Solution(**s) for s in solutions],
        artifacts=artifacts,
    )
