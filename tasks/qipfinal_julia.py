"""qip_final, Julia route — the same 16 instances via JuMP and YasolSolver.jl.

Counterpart to tasks/qipfinal_modeling.py. Running both separates a model that cannot
formulate a multistage program from one that can but does not know Yasol's file format.
"""

import sys
from pathlib import Path

from lighteval.tasks.lighteval_task import LightevalTaskConfig


ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bench.scoring import build_metric  # noqa: E402
from tasks.julia_modeling import INSTRUCTION, MAX_GEN_TOKENS, julia_prompt  # noqa: E402


__all__ = ["INSTRUCTION", "TASKS_TABLE"]

DATA_DIR = str((ROOT / "data" / "qip_final").resolve())

qipfinal_julia = LightevalTaskConfig(
    name="qipfinal_julia",
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

TASKS_TABLE = [qipfinal_julia]
