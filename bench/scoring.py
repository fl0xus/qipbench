"""Execution-accuracy scorer for QIP modeling tasks.

Scores what the model produced by *running* it: extract the artifact, solve it, compare
status first and only then the objective value. A text comparison against a reference
formulation would be meaningless here — the same problem has many equivalent encodings.

Two routes share the same stages, differing only in what the model writes and how it is
executed:

    QlpBackend     a ```qlp instance, solved directly
    JuliaBackend   ```julia code using JuMP + YasolSolver.jl, which writes the QLP itself

Stages, first failure wins:

    1  extraction    no fenced block in the completion
    2  format        the artifact could not be turned into a solvable model
    3  timeout       the solver hit its limit
    4  execution     it ran but reached no verdict (Yasol ERROR / UNKNOWN)
    5  status match  OPTIMAL / INFEASIBLE / FEASIBLE must agree with the reference status
    6  objective     reference without an objective -> status suffices; otherwise
                     relative deviation below TOLERANCE

`format` is kept apart from `execution` on purpose. Both mean "scored zero", but only the
first says the model never got as far as a solvable model — and on this benchmark that is
the dominant failure, so lumping them together would hide the finding.
"""

from __future__ import annotations

import json
import os
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from lighteval.metrics.metrics_sample import SampleLevelComputation
from lighteval.metrics.utils.metric_utils import SampleLevelMetricGrouping
from lighteval.models.model_output import ModelResponse
from lighteval.tasks.requests import Doc, SamplingMethod

from bench.yasol_client import YasolClient, YasolUnavailable


TOLERANCE = 1e-6

# Statuses meaning "the solver reached a verdict". INCUMBENT counts as executed but is not
# OPTIMAL — a solution was found without proving it optimal, so it fails the status match
# rather than being hidden as an execution error.
_SOLVED_STATUSES = {"OPTIMAL", "INFEASIBLE", "FEASIBLE", "INCUMBENT"}
_FAILED_STATUSES = {"ERROR", "UNKNOWN"}

_ANY_FENCE = re.compile(r"```[ \t]*[A-Za-z0-9_+-]*[ \t]*\r?\n(.*?)```", re.DOTALL)
# An opening fence with no closing one means the generation stopped mid-answer. Different
# failure from "the model never used a fence", worth telling apart in the trace.
_OPEN_FENCE = re.compile(r"```[ \t]*[A-Za-z0-9_+-]*[ \t]*\r?\n")

_RESULT_LINE = re.compile(r"^RESULT:\s*(?P<value>\S+)", re.MULTILINE)
_STATUS_LINE = re.compile(r"^Solution Status:\s*(?P<status>.+)$", re.MULTILINE)
_SENTINEL_THRESHOLD = float(1 << 61) / 2

METRIC_NAMES = [
    "execution_accuracy",
    "execution_rate",
    "err_extraction",
    "err_format",
    "err_service",
    "err_timeout",
    "err_execution",
    "err_status_mismatch",
    "err_wrong_objective",
]

_TRACE_LOCK = threading.Lock()


@dataclass
class Attempt:
    """What a backend made of the model's artifact."""

    status: str | None = None
    objective_value: float | None = None
    timed_out: bool = False
    failure: str | None = None  # "format" | "execution" | None
    detail: str | None = None


def extract_fenced(completion: str, language: str) -> str | None:
    """The last fenced block, preferring the one tagged `language`."""
    tagged = re.compile(rf"```[ \t]*{language}[ \t]*\r?\n(.*?)```", re.DOTALL | re.IGNORECASE)
    for pattern in (tagged, _ANY_FENCE):
        matches = pattern.findall(completion)
        if matches:
            candidate = matches[-1].strip()
            if candidate:
                return candidate
    return None


def objective_matches(observed: float, reference: float) -> bool:
    """Relative deviation below TOLERANCE.

    The denominator is `max(1, |reference|)` rather than `|reference|`, which keeps the comparison
    defined for a reference value of zero. For |reference| >= 1 this is the usual relative error.
    """
    return abs(observed - reference) / max(1.0, abs(reference)) < TOLERANCE


def status_from_solver_output(text: str) -> tuple[str | None, float | None]:
    """Status and objective from Yasol's console output.

    Needed for the Julia route: YasolSolver.jl does not pass --outputFile, and Yasol
    writes no solution file at all for an instance without a solution. Without reading the
    console output, every infeasible instance would look like a failed run.

    Applies the same two normalisations as the service — UNSAT and the ±2^61 sentinel both
    mean INFEASIBLE.
    """
    match = _STATUS_LINE.search(text)
    status = match["status"].strip() if match else None

    objective = None
    result = _RESULT_LINE.search(text)
    if result:
        try:
            objective = float(result["value"])
        except ValueError:
            objective = None

    if status == "UNSAT":
        return "INFEASIBLE", None
    if objective is not None and abs(objective) >= _SENTINEL_THRESHOLD:
        return "INFEASIBLE", None
    return status, objective


class QlpBackend:
    """The model writes the QLP instance itself."""

    fence = "qlp"
    name = "qlp"

    def __init__(self, client: YasolClient, time_limit: int) -> None:
        self.client = client
        self.time_limit = time_limit

    def attempt(self, artifact: str) -> Attempt:
        outcome = self.client.solve_qlp(artifact, self.time_limit)

        if outcome.rejected is not None:
            return Attempt(failure="format", detail=outcome.rejected)
        if outcome.timed_out:
            return Attempt(timed_out=True)
        if outcome.status is None:
            # Yasol died before printing a status: it could not parse the instance.
            # Its parser aborts via C++ exception, so the exit code is a signal, not 0.
            return Attempt(failure="format", detail=(outcome.stdout or "")[-300:])
        if outcome.status in _FAILED_STATUSES:
            return Attempt(status=outcome.status, failure="execution")
        return Attempt(status=outcome.status, objective_value=outcome.objective_value)


class JuliaBackend:
    """The model writes JuMP code; YasolSolver.jl produces the QLP and calls the solver."""

    fence = "julia"
    name = "julia"

    def __init__(self, client: YasolClient, timeout: int) -> None:
        self.client = client
        self.timeout = timeout

    def attempt(self, artifact: str) -> Attempt:
        response = self.client.solve_julia(artifact, self.timeout)

        if response.get("timed_out"):
            return Attempt(timed_out=True)
        if response.get("exit_code") != 0:
            # Julia raised: a syntax error or misuse of the JuMP/YasolSolver API.
            return Attempt(failure="format", detail=(response.get("stderr") or "")[-400:])

        solutions = response.get("solutions") or []
        if solutions:
            solution = solutions[0]
            return Attempt(status=solution.get("status"), objective_value=solution.get("objective_value"))

        # No solution file. For an instance without a solution that is expected, so the
        # solver's console output decides.
        for name, content in (response.get("artifacts") or {}).items():
            if name.endswith("_output.txt"):
                status, objective = status_from_solver_output(content)
                if status in _SOLVED_STATUSES:
                    return Attempt(status=status, objective_value=objective)
                if status in _FAILED_STATUSES:
                    return Attempt(status=status, failure="execution")
                return Attempt(failure="format", detail=content[-300:])

        return Attempt(failure="execution", detail="Julia produced no solver output")


def _blank_scores() -> dict[str, float]:
    return dict.fromkeys(METRIC_NAMES, 0.0)


class ExecutionAccuracy(SampleLevelComputation):
    """Run the model's artifact and compare the outcome against the reference values."""

    def __init__(self, backend: QlpBackend | JuliaBackend, trace_file: Path | None = None) -> None:
        self.backend = backend
        self.trace_file = trace_file

    def compute(self, doc: Doc, model_response: ModelResponse, **kwargs: Any) -> dict[str, float]:
        completion = model_response.text[0] if model_response.text else ""
        reference = doc.specific or {}
        scores = _blank_scores()
        record: dict[str, Any] = {
            "id": reference.get("id"),
            "route": self.backend.name,
            "reference_status": reference.get("reference_status"),
            "reference_objective": reference.get("reference_objective"),
            "completion": completion,
        }

        # --- 1. extraction ---------------------------------------------------
        artifact = extract_fenced(completion, self.backend.fence)
        if artifact is None:
            scores["err_extraction"] = 1.0
            truncated = _OPEN_FENCE.search(completion) is not None
            return self._finish(
                scores, record, "extraction_unterminated" if truncated else "extraction_error"
            )
        record["artifact"] = artifact

        # --- 2. run it -------------------------------------------------------
        try:
            attempt = self.backend.attempt(artifact)
        except YasolUnavailable as exc:
            # Harness fault. Scored 0 so it cannot inflate the result, but counted
            # separately: any non-zero err_service invalidates the run.
            scores["err_service"] = 1.0
            record["error"] = str(exc)
            return self._finish(scores, record, "service_error")

        record.update({"observed_status": attempt.status, "observed_objective": attempt.objective_value})
        if attempt.detail:
            record["detail"] = attempt.detail

        if attempt.timed_out:
            scores["err_timeout"] = 1.0
            return self._finish(scores, record, "timeout")
        if attempt.failure == "format":
            scores["err_format"] = 1.0
            return self._finish(scores, record, "format_error")
        if attempt.failure == "execution":
            scores["err_execution"] = 1.0
            return self._finish(scores, record, "execution_error")

        # The artifact ran and the solver reached a verdict.
        scores["execution_rate"] = 1.0

        # --- 3. status match -------------------------------------------------
        if attempt.status != reference.get("reference_status"):
            scores["err_status_mismatch"] = 1.0
            return self._finish(scores, record, "status_mismatch")

        # --- 4. nothing to compare -------------------------------------------
        reference_objective = reference.get("reference_objective")
        if reference_objective is None:
            scores["execution_accuracy"] = 1.0
            return self._finish(scores, record, "correct_no_objective")

        # --- 5. objective ----------------------------------------------------
        if attempt.objective_value is not None and objective_matches(attempt.objective_value, reference_objective):
            scores["execution_accuracy"] = 1.0
            return self._finish(scores, record, "correct")

        scores["err_wrong_objective"] = 1.0
        return self._finish(scores, record, "wrong_objective")

    def _finish(self, scores: dict[str, float], record: dict[str, Any], reason: str) -> dict[str, float]:
        record["reason"] = reason
        record["execution_accuracy"] = scores["execution_accuracy"]
        self._trace(record)
        return scores

    def _trace(self, record: dict[str, Any]) -> None:
        """Append the full per-instance record to the run's JSONL sidecar.

        lighteval's own details file has the completion and the scores but not the solver
        response, so the failure analysis the benchmark needs would be impossible from it
        alone.
        """
        if self.trace_file is None:
            return
        line = json.dumps(record, ensure_ascii=False, default=str)
        with _TRACE_LOCK:
            with open(self.trace_file, "a", encoding="utf-8") as handle:
                handle.write(line + "\n")


def build_metric(route: str = "qlp") -> SampleLevelMetricGrouping:
    """The metric, configured from the environment that `run.py` sets up."""
    url = os.environ.get("OR_BENCH_YASOL_URL", "http://127.0.0.1:8010")
    limit = int(os.environ.get("OR_BENCH_TIME_LIMIT_QLP", "60"))
    trace = os.environ.get("OR_BENCH_TRACE_FILE") or None
    client = YasolClient(url)

    if route == "julia":
        # Julia start-up plus solving; the endpoint caps it anyway.
        backend: QlpBackend | JuliaBackend = JuliaBackend(client, timeout=limit + 60)
    else:
        backend = QlpBackend(client, time_limit=limit)

    return SampleLevelMetricGrouping(
        metric_name=METRIC_NAMES,
        sample_level_fn=ExecutionAccuracy(backend, Path(trace) if trace else None),
        category=SamplingMethod.GENERATIVE,
        corpus_level_fn=dict.fromkeys(METRIC_NAMES, np.mean),
        higher_is_better={
            name: name in {"execution_accuracy", "execution_rate"} for name in METRIC_NAMES
        },
    )
