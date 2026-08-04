"""Parser for Yasol's `.sol` files.

Yasol writes what its own source calls "(pseudo)XML". Two quirks matter:

* The prolog is `<?xml version = "1.0" ...?>` — the spaces around `=` make it invalid
  XML, so it is stripped before parsing.
* `<quality>` carries its fields as *text content* (`SolutionStatus="OPTIMAL"` between
  the tags), not as attributes. Older Yasol versions wrote them as real attributes, so
  both shapes are accepted here.
"""

from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET
from pathlib import Path

_PROLOG = re.compile(r"^\s*<\?xml.*?\?>", re.DOTALL)
_KEY_VALUE = re.compile(r'(\w+)\s*=\s*"([^"]*)"')


def _as_float(value: str | None) -> float | None:
    """A finite float, or None.

    Non-finite values are dropped rather than passed on: `inf` and `nan` have no JSON
    representation, and returning them would make the endpoint fail with a 500 instead of
    reporting the result.
    """
    if value is None:
        return None
    try:
        number = float(value)
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def _fields(element: ET.Element | None) -> dict[str, str]:
    """Fields of an element, whether written as attributes or as text content."""
    if element is None:
        return {}
    if element.attrib:
        return dict(element.attrib)
    return dict(_KEY_VALUE.findall(element.text or ""))


def parse_solution_file(path: Path) -> dict:
    raw = path.read_text(errors="replace")
    root = ET.fromstring(_PROLOG.sub("", raw, count=1).strip())

    header = _fields(root.find("header"))
    quality = _fields(root.find("quality"))

    def _as_int(value: str | None) -> int | None:
        return int(value) if value is not None and value.lstrip("-").isdigit() else None

    variables = [
        {
            "name": var.get("name", ""),
            "index": var.get("index"),
            "value": _as_float(var.get("value")),
            "block": _as_int(var.get("block")),
        }
        for var in root.findall("./variables/variable")
    ]

    # Runtime is written as e.g. "0.003 seconds", or "TIMEOUT".
    runtime = header.get("Runtime", "")
    runtime_seconds = _as_float(runtime.split(" ")[0]) if runtime else None

    return {
        "file_name": path.name,
        "status": quality.get("SolutionStatus"),
        "objective_value": _as_float(header.get("ObjectiveValue")),
        "gap": _as_float(quality.get("Gap")),
        "dual_bound": _as_float(quality.get("Dual")),
        "runtime_seconds": runtime_seconds,
        "decision_nodes": header.get("DecisionNodes"),
        "variables": variables,
        "raw": raw,
    }


def collect_solutions(workdir: Path) -> list[dict]:
    """Parse every solution file in `workdir`.

    Yasol appends a counter (`.sol`, `.sol1`, …) when the target already exists, so more
    than one file can show up if the same instance is solved repeatedly in one workspace.
    """
    solutions = []
    for path in sorted(workdir.glob("*.sol*")):
        try:
            solutions.append(parse_solution_file(path))
        except ET.ParseError as exc:
            solutions.append(
                {
                    "file_name": path.name,
                    "status": None,
                    "parse_error": str(exc),
                    "raw": path.read_text(errors="replace"),
                }
            )
    return solutions
