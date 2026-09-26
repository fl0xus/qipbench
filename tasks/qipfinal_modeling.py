"""qip_final, QLP route — the benchmark set intended for reporting.

16 instances: the 12 hand-written ones (adaptivity matters in all of them) plus exactly
one representative per generated template — 1 from qip_2, 1 from qip_int, 2 from qlp.
Only one per template on purpose: within a template the problem texts are 88-99 percent
identical, so more of them would reward recognising the template rather than reading the
task. The mixture is deliberate; see documentation/task_qip_final.md, including why a
single aggregate score over it needs the per-origin breakdown to be meaningful.

Rebuilt by scripts/build_qip_final.py — that script, not this docstring, decides the
composition; data/qip_final.meta.json records what the last build produced.

Prompt, rules and scorer are shared with tasks/qlp_modeling.py — only the data differs.
"""

import sys
from pathlib import Path

from lighteval.tasks.lighteval_task import LightevalTaskConfig


ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bench.scoring import build_metric  # noqa: E402
from tasks.qlp_modeling import INSTRUCTION, MAX_GEN_TOKENS, SYSTEM_PROMPT, qlp_prompt  # noqa: E402


__all__ = ["SYSTEM_PROMPT", "INSTRUCTION", "TASKS_TABLE"]

DATA_DIR = str((ROOT / "data" / "qip_final").resolve())

qipfinal_modeling = LightevalTaskConfig(
    name="qipfinal_modeling",
    prompt_function=qlp_prompt,
    hf_repo=DATA_DIR,
    hf_subset="default",
    hf_avail_splits=["test"],
    evaluation_splits=["test"],
    few_shots_split=None,
    few_shots_select=None,
    metrics=[build_metric("qlp")],
    generation_size=MAX_GEN_TOKENS,
    stop_sequence=None,
    version=0,
)

TASKS_TABLE = [qipfinal_modeling]
