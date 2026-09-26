"""Exercise every stage of the execution-accuracy scorer with hand-made completions.

    python scripts/test_scorer.py [yasol_url]

This does not test any model. It feeds the scorer answers that are deliberately correct,
wrong, malformed, unsolvable or unparseable — on both routes — and asserts that each lands
in the right bucket, so that a low benchmark score can be trusted to mean "the model
failed" and not "the harness failed".
"""

import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from lighteval.models.model_output import ModelResponse  # noqa: E402
from lighteval.tasks.requests import Doc  # noqa: E402

from bench.scoring import (  # noqa: E402
    ExecutionAccuracy,
    JuliaBackend,
    QlpBackend,
    extract_fenced,
    objective_matches,
    status_from_solver_output,
)
from bench.yasol_client import YasolClient  # noqa: E402


CORRECT = """MINIMIZE
3 x1 + 2 x2 + 4 y
SUBJECT TO
E_c1: 1 x1 + 1 x2 + 1 y >= 2
BOUNDS
0 <= x1 <= 1
0 <= x2 <= 1
0 <= y <= 1
BINARIES
x1 x2 y
EXISTS
x1 y
ALL
x2
ORDER
x1
x2
y
END"""

# Same instance without the adversary: the ALL block moves to EXISTS, which makes the
# problem easier and yields 5 instead of 7. A plausible modeling mistake.
WRONG_OBJECTIVE = CORRECT.replace("EXISTS\nx1 y\nALL\nx2", "EXISTS\nx1 x2 y\nALL\n")

# Parentheses abort Yasol's parser — the dominant real failure mode on this benchmark.
UNPARSEABLE = CORRECT.replace("1 x1 + 1 x2 + 1 y", "1 x1 + 1 (x2 + y)")

INFEASIBLE = """MINIMIZE
1 p
SUBJECT TO
E_c1: 1 p + 1 q >= 2
E_c2: 1 p + 1 q <= 1
BOUNDS
0 <= p <= 1
0 <= q <= 1
BINARIES
p q
EXISTS
p q
ORDER
p
q
END"""

# Yasol reaches "no solution" on two separate paths. INFEASIBLE above comes from the
# sentinel objective; this one is detected outright and reported as UNSAT, which the
# service normalises. Both must score identically, otherwise a correct answer would pass
# or fail depending on which solver code path happened to run.
UNSAT = """MINIMIZE
6 x1 + 4 x2
SUBJECT TO
E_capacity: 5 x1 + 5 x2 - 1 u1 - 2 u2 >= 20
U_budget: 1 u1 + 1 u2 <= 2
BOUNDS
0 <= x1 <= 1
0 <= x2 <= 1
0 <= u1 <= 1
0 <= u2 <= 1
BINARIES
x1 x2 u1 u2
EXISTS
x1 x2
ALL
u1 u2
ORDER
x1
x2
u1
u2
END"""

JULIA_CORRECT = """using JuMP, YasolSolver
model = Model(() -> YasolSolver.Optimizer())
set_optimizer_attribute(model, "solver path", ENV["YASOL_BIN"])
set_optimizer_attribute(model, "output info", 1)
set_optimizer_attribute(model, "problem file name", "model.qlp")
@variable(model, x1, binary = true, lower_bound = 0, upper_bound = 1, YasolVariable, quantifier = "exists", block = 1)
@variable(model, x2, binary = true, lower_bound = 0, upper_bound = 1, YasolVariable, quantifier = "all", block = 2)
@variable(model, y, binary = true, lower_bound = 0, upper_bound = 1, YasolVariable, quantifier = "exists", block = 3)
@constraint(model, c1, -1 * x1 - 1 * x2 - 1 * y <= -2, YasolConstraint, quantifier = "exists")
@objective(model, Min, 3 * x1 + 2 * x2 + 4 * y)
optimize!(model)
"""

JULIA_BROKEN = 'using JuMP, YasolSolver\nthis is not valid Julia at all(\n'

REFERENCE_OPTIMAL = {"id": "t", "reference_status": "OPTIMAL", "reference_objective": 7.0}
REFERENCE_INFEASIBLE = {"id": "t", "reference_status": "INFEASIBLE", "reference_objective": None}


def score(scorer, completion: str, reference: dict) -> tuple[dict, str]:
    doc = Doc(task_name="t", query="", choices=[""], gold_index=0, specific=reference)
    reasons = []
    original = scorer._finish

    def capture(scores, record, reason):
        reasons.append(reason)
        return original(scores, record, reason)

    scorer._finish = capture
    try:
        result = scorer.compute(doc=doc, model_response=ModelResponse(text=[completion]))
    finally:
        scorer._finish = original
    return result, reasons[0]


def check(name: str, condition: bool, detail: str = "") -> None:
    print(f"  {'PASS' if condition else 'FAIL'}  {name}{'  — ' + detail if detail else ''}")
    if not condition:
        raise SystemExit(1)


def main() -> None:
    url = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8010"
    client = YasolClient(url)
    qlp = ExecutionAccuracy(QlpBackend(client, time_limit=30))
    julia = ExecutionAccuracy(JuliaBackend(client, timeout=180))

    print("unit-level helpers")
    check("tagged fence preferred over plain fence",
          extract_fenced("```python\nnope\n```\ntext\n```qlp\nYES\n```", "qlp") == "YES")
    check("last fence wins", extract_fenced("```qlp\nfirst\n```\n```qlp\nsecond\n```", "qlp") == "second")
    check("plain fence as fallback", extract_fenced("```\nplain\n```", "qlp") == "plain")
    check("no fence -> None", extract_fenced("just prose", "qlp") is None)
    check("julia fence selected by language",
          extract_fenced("```qlp\nA\n```\n```julia\nB\n```", "julia") == "B")
    check("tolerance accepts 1e-9 relative", objective_matches(7.000000001, 7.0))
    check("tolerance rejects 1e-3 relative", not objective_matches(7.001, 7.0))
    check("tolerance defined at reference 0", objective_matches(1e-9, 0.0) and not objective_matches(0.5, 0.0))
    check("console UNSAT -> INFEASIBLE",
          status_from_solver_output("Solution Status: UNSAT\n") == ("INFEASIBLE", None))
    check("console sentinel -> INFEASIBLE",
          status_from_solver_output("Solution Status: OPTIMAL\nRESULT: -2.30584e+18 : x\n")
          == ("INFEASIBLE", None))
    check("console optimum parsed",
          status_from_solver_output("Solution Status: OPTIMAL\nRESULT: 7 : x\n") == ("OPTIMAL", 7.0))

    print("\nQLP route — extraction")
    scores, reason = score(qlp, "Here is my model but I forgot the fence.", REFERENCE_OPTIMAL)
    check("prose -> extraction_error", reason == "extraction_error", reason)
    check("  execution_rate stays 0", scores["execution_rate"] == 0.0)
    scores, reason = score(qlp, f"```qlp\n{CORRECT[:60]}", REFERENCE_OPTIMAL)
    check("cut-off answer -> extraction_unterminated", reason == "extraction_unterminated", reason)

    print("\nQLP route — format vs execution")
    scores, reason = score(qlp, f"```qlp\n{UNPARSEABLE}\n```", REFERENCE_OPTIMAL)
    check("parentheses -> format_error", reason == "format_error", reason)
    check("  err_format flagged, not err_execution",
          scores["err_format"] == 1.0 and scores["err_execution"] == 0.0)
    scores, reason = score(qlp, "```qlp\nthis is not a QLP file at all\n```", REFERENCE_OPTIMAL)
    check("garbage -> format_error", reason == "format_error", reason)

    unreachable = ExecutionAccuracy(QlpBackend(YasolClient("http://127.0.0.1:9"), time_limit=5))
    scores, reason = score(unreachable, f"```qlp\n{CORRECT}\n```", REFERENCE_OPTIMAL)
    check("unreachable service -> service_error", reason == "service_error", reason)
    check("  err_service flagged, not err_format",
          scores["err_service"] == 1.0 and scores["err_format"] == 0.0)

    print("\nQLP route — status match")
    scores, reason = score(qlp, f"```qlp\n{INFEASIBLE}\n```", REFERENCE_OPTIMAL)
    check("infeasible answer vs OPTIMAL reference -> status_mismatch", reason == "status_mismatch", reason)
    check("  counted as executed", scores["execution_rate"] == 1.0)
    _, reason = score(qlp, f"```qlp\n{CORRECT}\n```", REFERENCE_INFEASIBLE)
    check("optimal answer vs INFEASIBLE reference -> status_mismatch", reason == "status_mismatch", reason)

    print("\nQLP route — both infeasibility paths")
    scores, reason = score(qlp, f"```qlp\n{INFEASIBLE}\n```", REFERENCE_INFEASIBLE)
    check("sentinel path correct", reason == "correct_no_objective", reason)
    scores, reason = score(qlp, f"```qlp\n{UNSAT}\n```", REFERENCE_INFEASIBLE)
    check("UNSAT path scores identically", reason == "correct_no_objective", reason)

    print("\nQLP route — objective")
    scores, reason = score(qlp, f"```qlp\n{CORRECT}\n```", REFERENCE_OPTIMAL)
    check("correct model -> correct", reason == "correct", reason)
    check("  scores 1", scores["execution_accuracy"] == 1.0)
    scores, reason = score(qlp, f"```qlp\n{WRONG_OBJECTIVE}\n```", REFERENCE_OPTIMAL)
    check("adversary dropped -> wrong_objective", reason == "wrong_objective", reason)
    check("  still counted as executed", scores["execution_rate"] == 1.0)
    _, reason = score(qlp, f"Sure!\n\n```qlp\n{CORRECT}\n```\n\nHope that helps.", REFERENCE_OPTIMAL)
    check("chatty answer still correct", reason == "correct", reason)

    print("\nJulia route")
    scores, reason = score(julia, f"```julia\n{JULIA_BROKEN}\n```", REFERENCE_OPTIMAL)
    check("broken Julia -> format_error", reason == "format_error", reason)
    check("  err_format flagged", scores["err_format"] == 1.0)
    scores, reason = score(julia, f"```julia\n{JULIA_CORRECT}\n```", REFERENCE_OPTIMAL)
    check("correct JuMP model -> correct", reason == "correct", reason)
    check("  scores 1", scores["execution_accuracy"] == 1.0)

    print("\nall scorer stages behave as specified on both routes")


if __name__ == "__main__":
    main()
