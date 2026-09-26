"""Julia route: natural-language problem -> JuMP model -> YasolSolver.jl -> Yasol.

Same problems, same reference values and the same scorer as tasks/qlp_modeling.py — only
the artifact differs. Instead of writing Yasol's QLP dialect by hand, the model writes JuMP
code and YasolSolver.jl generates the instance.

The point of running both is to separate two things the QLP route cannot tell apart: a
model that cannot formulate a quantified program, and a model that can but does not know
the file format. JuMP is widely represented in training data; the QLP dialect is not.
"""

import os
import sys
from pathlib import Path

from lighteval.tasks.lighteval_task import LightevalTaskConfig
from lighteval.tasks.requests import Doc


ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bench.dataset import reference_from_row  # noqa: E402
from bench.scoring import build_metric  # noqa: E402


DATA_DIR = str((ROOT / "data" / "qlp").resolve())
PROMPTS = ROOT / "prompts"

MAX_GEN_TOKENS = int(os.environ.get("OR_BENCH_MAX_GEN_TOKENS", "8192"))

# `solver path` and the working directory are provided by the container; the model only
# has to build the model itself. Results are read from the solution file by the harness,
# not through JuMP accessors — YasolSolver.jl cannot parse Yasol's current .sol format
# (see yasol-service/README.md), so termination_status and value() are unusable.
#
# The set_optimizer_attribute rule is not a modelling hint but a version fact. YasolSolver.jl
# caps MathOptInterface at <= 1.1.1, which pins JuMP to 1.1.1 — a 2022 release without
# set_attribute. Measured zero-shot, 20 of 30 answers failed with
# `UndefVarError: set_attribute not defined`: the model wrote correct modern JuMP against a
# library that predates it. Without the rule the zero-shot number measures the age of our
# dependency as much as the model's ability.
#
# The no-equality rule has the same character. YasolSolver.jl writes an EqualTo constraint
# to the .qlp without its operator and right-hand side — `r1 + r2 == 1` comes out as
# `U_Constraint1: +1.0x5 +1.0x6`, Yasol's parser then reads on into the next line and
# aborts with `Name not found: x6E_Constraint1:`. Inequalities are written correctly, and
# splitting the equality in two reproduces it exactly: measured on the worked example
# below, OPTIMAL 14.0 either way, matching prompts/fewshot_solution.qlp. Every instance in
# qip_final needs an "exactly one event happens" constraint, so without the rule the route
# cannot be passed at all: 11 of 16 failures in results/runs/20260731T080049Z are this bug
# and nothing else. Drop the rule once the writer is fixed — it is a workaround for a
# defect in our dependency, not a property of the modelling task.
INSTRUCTION = (
    "You are an expert in quantified integer programming. "
    "Given a problem description, write Julia code that builds the model with JuMP and "
    "solves it with YasolSolver.jl.\n"
    "Rules:\n"
    "- Set attributes with set_optimizer_attribute, not set_attribute; the JuMP version "
    "in use is 1.1.1 and does not have set_attribute yet.\n"
    "- Use ENV[\"YASOL_BIN\"] as the \"solver path\" attribute and set a \"problem file name\".\n"
    "- Every variable needs the YasolVariable extension with a quantifier (\"exists\" or "
    "\"all\") and a block number; the block numbers give the order in which decisions "
    "are fixed.\n"
    "- Give every variable an explicit lower_bound and upper_bound, even a binary one "
    "where JuMP would imply them.\n"
    "- Never write an equality constraint. Express `a + b == 1` as the two constraints "
    "`a + b <= 1` and `a + b >= 1`, both with the same quantifier and each with its own "
    "name.\n"
    "- Call optimize!(model) at the end.\n"
    "- Do not print or inspect results; writing the model and solving it is enough.\n"
    "Output exactly one fenced code block tagged julia, and nothing else."
)

FEWSHOT_PROBLEM = (PROMPTS / "fewshot_problem.txt").read_text().strip()
FEWSHOT_SOLUTION = (PROMPTS / "fewshot_julia.jl").read_text().strip()
FEWSHOT_NOTES = (PROMPTS / "fewshot_notes_julia.md").read_text().strip()

# Set by run.py from --shots; see tasks/qlp_modeling.py for why lighteval's own few-shot
# handling cannot be used here.
SHOTS = int(os.environ.get("OR_BENCH_SHOTS", "1"))


def julia_prompt(line: dict, task_name: str) -> Doc:
    example = (
        f"Problem:\n{FEWSHOT_PROBLEM}\n\n```julia\n{FEWSHOT_SOLUTION}\n```\n\n"
        f"{FEWSHOT_NOTES}\n\n"
        if SHOTS >= 1
        else ""
    )
    query = f"{example}Problem:\n{line['problem']}\n"

    reference_status, reference_objective = reference_from_row(line)
    return Doc(
        task_name=task_name,
        query=query,
        choices=[f"{reference_status} {reference_objective}"],
        gold_index=0,
        instruction=INSTRUCTION,
        specific={
            "id": line["id"],
            "reference_status": reference_status,
            "reference_objective": reference_objective,
        },
    )


julia_modeling = LightevalTaskConfig(
    name="julia_modeling",
    prompt_function=julia_prompt,
    hf_repo=DATA_DIR,
    hf_subset="default",
    hf_avail_splits=["test"],
    evaluation_splits=["test"],
    few_shots_split=None,
    few_shots_select=None,
    metrics=[build_metric("julia")],
    generation_size=MAX_GEN_TOKENS,
    stop_sequence=None,
    version=0,
)

TASKS_TABLE = [julia_modeling]
