#!/usr/bin/env python
"""Run a benchmark against an OpenAI-compatible endpoint.

    python run.py configs/qwen3.5-4b.yaml --tasks qlp_modeling --custom-tasks tasks/qlp_modeling.py

Replaces `lighteval endpoint litellm <config>` on the CLI, because three things are needed
that the CLI does not offer: resolving the API key from .env, passing provider-specific
body fields through to the endpoint (see bench/litellm_extra_body.py), and pinning the
generation budget and solver time limit from a single place.
"""

import argparse
import importlib.util
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import yaml
from dotenv import load_dotenv

from bench.litellm_extra_body import (
    ExtraBodyLiteLLMModelConfig,
    disable_litellm_response_cache,
    install_extra_body,
)


ROOT = Path(__file__).parent


def load_model_config(
    config_path: Path, max_gen_tokens: int, concurrent_requests: int
) -> ExtraBodyLiteLLMModelConfig:
    with open(config_path) as f:
        params = yaml.safe_load(f)["model_parameters"]

    # `api_key_env` names the .env variable holding the key; the key itself never
    # appears in the config files.
    key_var = params.pop("api_key_env", None)
    if key_var:
        api_key = os.environ.get(key_var)
        if not api_key:
            raise SystemExit(f"{key_var} is not set — copy .env.example to .env and fill it in.")
        params["api_key"] = api_key

    # Single source of truth for the generation budget. lighteval's LiteLLM backend
    # prefers max_completion_tokens from generation_parameters and only falls back to the
    # task's generation_size, so leaving both to drift apart would silently favour the
    # config. --max-gen-tokens drives both (the task reads the env var below).
    generation = dict(params.get("generation_parameters") or {})
    generation["max_new_tokens"] = max_gen_tokens
    params["generation_parameters"] = generation

    # Same reasoning as above: one place decides, so a run cannot be measured at a
    # different load than its metadata claims.
    params["concurrent_requests"] = concurrent_requests

    return ExtraBodyLiteLLMModelConfig(**params)


def load_task_system_prompt(custom_tasks_path: str) -> str | None:
    """SYSTEM_PROMPT from the task module, or None if it does not define one.

    Imported by path because that is how lighteval loads the task file too. The module is
    read after the environment is set up, so it sees the selected ruleset.
    """
    spec = importlib.util.spec_from_file_location("or_bench_task_module", custom_tasks_path)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return getattr(module, "SYSTEM_PROMPT", None)


def write_run_metadata(path: Path, model_config: ExtraBodyLiteLLMModelConfig, args) -> None:
    """Everything needed to interpret or repeat this run, next to its results.

    Ground-truth objective values come out of the Yasol container, so the run is only
    meaningful together with the solver revision it was scored against.
    """
    solver: object
    try:
        from bench.yasol_client import YasolClient, YasolUnavailable

        try:
            solver = YasolClient(args.yasol_url).version()
        except YasolUnavailable as exc:
            solver = {"error": str(exc)}
    except ImportError as exc:  # requests missing
        solver = {"error": str(exc)}

    model = model_config.model_dump()
    model.pop("api_key", None)  # never write the key into results

    path.write_text(
        json.dumps(
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "config_file": str(args.config),
                "tasks": args.tasks,
                "max_gen_tokens": args.max_gen_tokens,
                "time_limit_qlp": args.time_limit_qlp,
                "concurrent_requests": args.concurrent_requests,
                "ruleset": args.ruleset,
                "shots": args.shots,
                "yasol_url": args.yasol_url,
                "tolerance": 1e-6,
                "model": model,
                "yasol_service": solver,
            },
            indent=2,
            default=str,
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path, help="Model config YAML, e.g. configs/qwen3.5-4b.yaml")
    parser.add_argument("--tasks", default="qlp_modeling|0", help="lighteval task spec: task|num_fewshot")
    parser.add_argument("--custom-tasks", default=str(ROOT / "tasks" / "qlp_modeling.py"))
    parser.add_argument("--output-dir", default=str(ROOT / "results"))
    parser.add_argument("--max-samples", type=int, default=None)
    parser.add_argument(
        "--max-gen-tokens",
        type=int,
        default=8192,
        help="Generation budget per sample. Drives both the task's generation_size and the "
        "model's max_new_tokens (default: 8192; reasoning models want 32768).",
    )
    parser.add_argument(
        "--time-limit-qlp",
        type=int,
        default=60,
        help="Yasol --timeLimit per instance in seconds. Capped by the service's "
        "YASOL_MAX_TIMEOUT (default: 60).",
    )
    parser.add_argument(
        "--concurrent-requests",
        type=int,
        default=1,
        help="Requests kept in flight against the endpoint. Raise it to keep the server "
        "busy; the two large models are run at 2 (default: 1).",
    )
    parser.add_argument(
        "--shots",
        type=int,
        default=1,
        help="Worked examples in the prompt: 0 or 1 (default: 1). The number after the task "
        "name is lighteval's own few-shot counter and has no effect here — this flag is what "
        "controls it.",
    )
    parser.add_argument(
        "--ruleset",
        default="user",
        help="How to arrange role and format rules across the system and user message; "
        "the task module defines the names (default: user, the best measured).",
    )
    parser.add_argument("--yasol-url", default=os.environ.get("OR_BENCH_YASOL_URL", "http://127.0.0.1:8010"))
    args = parser.parse_args()

    if args.concurrent_requests < 1:
        raise SystemExit("--concurrent-requests must be at least 1.")
    if args.shots not in (0, 1):
        raise SystemExit("--shots must be 0 or 1; there is exactly one worked example.")

    load_dotenv(ROOT / ".env")

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = Path(args.output_dir) / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    # The task module and the scorer are imported by lighteval, not by this script, so
    # configuration reaches them through the environment.
    os.environ["OR_BENCH_MAX_GEN_TOKENS"] = str(args.max_gen_tokens)
    os.environ["OR_BENCH_TIME_LIMIT_QLP"] = str(args.time_limit_qlp)
    os.environ["OR_BENCH_YASOL_URL"] = args.yasol_url
    os.environ["OR_BENCH_TRACE_FILE"] = str(run_dir / "trace.jsonl")
    os.environ["OR_BENCH_RULESET"] = args.ruleset
    os.environ["OR_BENCH_SHOTS"] = str(args.shots)

    model_config = load_model_config(args.config, args.max_gen_tokens, args.concurrent_requests)

    # The ruleset belongs in a real system message. lighteval's Doc.instruction does not
    # produce one for this backend — it is prepended to the user message — so the task
    # module exposes SYSTEM_PROMPT and it is installed on the model config here.
    model_config.system_prompt = load_task_system_prompt(args.custom_tasks)

    write_run_metadata(run_dir / "run.json", model_config, args)

    disable_litellm_response_cache()
    install_extra_body(model_config.extra_body, dump_messages_to=run_dir / "messages.json")

    from lighteval.logging.evaluation_tracker import EvaluationTracker
    from lighteval.pipeline import ParallelismManager, Pipeline, PipelineParameters

    pipeline = Pipeline(
        tasks=args.tasks,
        pipeline_parameters=PipelineParameters(
            launcher_type=ParallelismManager.NONE,
            custom_tasks_directory=args.custom_tasks,
            max_samples=args.max_samples,
        ),
        evaluation_tracker=EvaluationTracker(output_dir=args.output_dir, save_details=True),
        model_config=model_config,
    )

    pipeline.evaluate()
    pipeline.show_results()
    pipeline.save_and_push_results()

    print(f"\nRun metadata and per-instance trace: {run_dir}")


if __name__ == "__main__":
    main()
