"""QLP modeling task: natural-language problem -> QLP instance -> Yasol -> objective value.

Scored by execution, not by text comparison: the generated instance is solved in the
Yasol service and its outcome is compared against the reference status and objective. See
bench/scoring.py for the stages.

Run through run.py, which sets the environment variables read below.
"""

import os
import sys
from pathlib import Path

from lighteval.tasks.lighteval_task import LightevalTaskConfig
from lighteval.tasks.requests import Doc


ROOT = Path(__file__).parent.parent
# lighteval imports this file by path, so the project root is not on sys.path yet.
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bench.dataset import reference_from_row  # noqa: E402
from bench.scoring import build_metric  # noqa: E402
from prompts.rules_qlp import parts  # noqa: E402


DATA_DIR = str((ROOT / "data" / "qlp").resolve())
PROMPTS = ROOT / "prompts"

# Set by run.py from --max-gen-tokens. A QLP instance plus any reasoning needs far more
# room than a plain answer, and a truncated instance scores as an extraction error.
MAX_GEN_TOKENS = int(os.environ.get("OR_BENCH_MAX_GEN_TOKENS", "8192"))

# Two slots, chosen per run via --ruleset (see prompts/rules_qlp.py for the arrangements).
# SYSTEM_PROMPT is installed on the model config by run.py; INSTRUCTION lands at the front
# of the user message, because lighteval prepends Doc.instruction there for this backend
# despite documenting it as a system prompt. Each run writes its first request to
# messages.json so the placement is auditable rather than assumed.
SYSTEM_PROMPT, INSTRUCTION = parts(os.environ.get("OR_BENCH_RULESET", "user"))

FEWSHOT_PROBLEM = (PROMPTS / "fewshot_problem.txt").read_text().strip()
FEWSHOT_SOLUTION = (PROMPTS / "fewshot_solution.qlp").read_text().strip()
FEWSHOT_NOTES = (PROMPTS / "fewshot_notes.md").read_text().strip()

# Set by run.py from --shots. Only 0 and 1 are meaningful: there is exactly one worked
# example, held outside the evaluation split so it cannot leak an answer.
#
# lighteval's own few-shot machinery is *not* used — `few_shots_split` is None, which makes
# the number after the task name (`qipfinal_modeling|0`) inert. Without this switch every
# run was silently 1-shot while the command line read like zero-shot.
SHOTS = int(os.environ.get("OR_BENCH_SHOTS", "1"))


def qlp_prompt(line: dict, task_name: str) -> Doc:
    example = (
        f"Problem:\n{FEWSHOT_PROBLEM}\n\n```qlp\n{FEWSHOT_SOLUTION}\n```\n\n"
        f"{FEWSHOT_NOTES}\n\n"
        if SHOTS >= 1
        else ""
    )
    query = f"{example}Problem:\n{line['problem']}\n"

    # `specific` qualifies them as `reference_`: there they stand next to the observed
    # status and objective, and telling the two apart is the scorer's whole job.
    reference_status, reference_objective = reference_from_row(line)
    return Doc(
        task_name=task_name,
        query=query,
        # Shown in the details file; the scorer reads `specific`, not these.
        choices=[f"{reference_status} {reference_objective}"],
        gold_index=0,
        instruction=INSTRUCTION,
        specific={
            "id": line["id"],
            "reference_status": reference_status,
            "reference_objective": reference_objective,
        },
    )


qlp_modeling = LightevalTaskConfig(
    name="qlp_modeling",
    prompt_function=qlp_prompt,
    hf_repo=DATA_DIR,
    hf_subset="default",
    hf_avail_splits=["test"],
    evaluation_splits=["test"],
    few_shots_split=None,
    few_shots_select=None,
    metrics=[build_metric("qlp")],
    generation_size=MAX_GEN_TOKENS,
    # No stop sequence: a newline or fence would cut the instance in half.
    stop_sequence=None,
    version=0,
)

TASKS_TABLE = [qlp_modeling]
