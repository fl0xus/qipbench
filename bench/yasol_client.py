"""HTTP client for the Yasol service.

The service container is the execution sandbox of the benchmark: generated QLP instances
are solved there, never in the harness process.

The client draws one distinction that matters for scoring — a request the service
*rejected* (4xx) is something the model produced and the solver refused, so it counts
against the model. A service that is unreachable or broken (connection error, 5xx) is a
harness fault and must never be scored as a model failure; it raises `YasolUnavailable`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import requests


class YasolUnavailable(RuntimeError):
    """The service could not be reached or answered unusably. Harness fault."""


@dataclass(frozen=True)
class SolveOutcome:
    status: str | None
    objective_value: float | None
    timed_out: bool
    exit_code: int | None
    duration_seconds: float
    variables: list[dict] = field(default_factory=list)
    note: str | None = None
    rejected: str | None = None
    stdout: str = ""


class YasolClient:
    def __init__(self, base_url: str, request_margin: int = 30) -> None:
        self.base_url = base_url.rstrip("/")
        self.request_margin = request_margin

    def version(self) -> dict:
        try:
            response = requests.get(f"{self.base_url}/version", timeout=30)
            response.raise_for_status()
            return response.json()
        except (requests.RequestException, ValueError) as exc:
            raise YasolUnavailable(f"{self.base_url}/version: {exc}") from exc

    def solve_qlp(self, instance: str, time_limit: int) -> SolveOutcome:
        try:
            response = requests.post(
                f"{self.base_url}/solve/qlp",
                files={"file": ("instance.qlp", instance.encode("utf-8"), "text/plain")},
                data={"time_limit": str(time_limit)},
                # The service kills the solver at time_limit + its own grace period; the
                # HTTP timeout has to sit above that so a solver timeout comes back as a
                # scored result rather than as a client-side error.
                timeout=time_limit + self.request_margin,
            )
        except requests.RequestException as exc:
            raise YasolUnavailable(f"{self.base_url}/solve/qlp: {exc}") from exc

        if response.status_code >= 500:
            raise YasolUnavailable(f"{self.base_url}/solve/qlp returned {response.status_code}")

        if response.status_code >= 400:
            # The instance itself was refused (malformed, too large, bad option).
            try:
                detail = str(response.json().get("detail", response.text))[:500]
            except ValueError:
                detail = response.text[:500]
            return SolveOutcome(
                status=None,
                objective_value=None,
                timed_out=False,
                exit_code=None,
                duration_seconds=0.0,
                rejected=f"HTTP {response.status_code}: {detail}",
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise YasolUnavailable(f"{self.base_url}/solve/qlp returned non-JSON: {exc}") from exc

        return SolveOutcome(
            status=payload.get("status"),
            objective_value=payload.get("objective_value"),
            timed_out=bool(payload.get("timed_out")),
            exit_code=payload.get("exit_code"),
            duration_seconds=float(payload.get("duration_seconds") or 0.0),
            variables=payload.get("variables") or [],
            note=payload.get("note"),
            stdout=payload.get("stdout") or "",
        )

    def solve_julia(self, code: str, timeout: int) -> dict:
        """Run Julia code that drives Yasol through YasolSolver.jl.

        Returned as-is rather than as a SolveOutcome: the caller needs the artifacts and
        the Julia exit code, not just a solver verdict.
        """
        try:
            response = requests.post(
                f"{self.base_url}/solve/julia",
                json={"code": code, "timeout": timeout},
                timeout=timeout + self.request_margin,
            )
        except requests.RequestException as exc:
            raise YasolUnavailable(f"{self.base_url}/solve/julia: {exc}") from exc

        if response.status_code >= 500:
            raise YasolUnavailable(f"{self.base_url}/solve/julia returned {response.status_code}")

        if response.status_code >= 400:
            try:
                detail = str(response.json().get("detail", response.text))[:500]
            except ValueError:
                detail = response.text[:500]
            return {"exit_code": 1, "stderr": f"HTTP {response.status_code}: {detail}", "solutions": []}

        try:
            return response.json()
        except ValueError as exc:
            raise YasolUnavailable(f"{self.base_url}/solve/julia returned non-JSON: {exc}") from exc
