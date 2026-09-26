"""How to read a dataset row, in one place.

Deliberately free of dependencies: the task modules, the scorer and the standalone
analysis scripts all import from here, and the scripts must not drag lighteval and numpy
in behind it.
"""

from __future__ import annotations


def reference_from_row(line: dict) -> tuple[str | None, float | None]:
    """(status, objective) — the answer a model's artefact is measured against.

    `objective` is legitimately None when there is no value to compare: an infeasible
    instance, or a pure feasibility question. The status alone decides then, which is why
    this returns both and never guesses one from the other.
    """
    if "status" not in line:
        raise KeyError(
            f"Dataset row {line.get('id', '<no id>')!r} has no `status` field. "
            "Sets written under the old key names need rebuilding."
        )
    return line["status"], line.get("objective")
