# QIPBench

A benchmark for LLM-assisted modelling of **quantified integer programs** (QIP): a
natural-language problem goes in, a solvable model comes out, and the model is graded by
**running it** — the generated artefact is solved by [Yasol](yasol-service/) and its status
and objective value are compared against verified reference values. Nothing is graded by
comparing text: the same problem has many equivalent encodings.

The benchmark set is `data/qip_final` — 16 instances, all solvable, all objective values
independently verified. Two routes share the same instances, the same reference values and
the same scorer, differing only in what the model has to write:

| Task | The model writes | How it runs |
|---|---|---|
| `qipfinal_modeling` | a ` ```qlp ` instance | handed straight to Yasol |
| `qipfinal_julia` | ` ```julia ` using JuMP + YasolSolver.jl | Julia builds the QLP and calls Yasol |

Running both separates two things a single number cannot: a model that cannot formulate a
multistage program, and a model that can but does not know the file format. JuMP is widely
represented in training data; Yasol's QLP dialect is not.

## Requirements

- Docker (for the solver container — the solver never runs in the harness process)
- Python 3.12
- An OpenAI-compatible inference endpoint and its API key

## Setup

**1. Build and start the solver container.**

```bash
docker build -t yasol-service yasol-service/ && docker run -d --name yasol -p 127.0.0.1:8010:8000 yasol-service
```

Check it before doing anything else. All three flags must be `true`; `julia` and
`julia_env` are what the Julia route needs.

```bash
curl -s http://127.0.0.1:8010/health
```

```json
{"status":"ok","yasol":true,"julia":true,"julia_env":true}
```

**2. Create the Python environment.**

```bash
uv venv --python 3.12 .venv && VIRTUAL_ENV=.venv uv pip install "lighteval[litellm]" python-dotenv requests
```

**3. Configure the endpoint.** Copy `.env.example` to `.env` and fill in the key. The key
never appears in a config file: each config names the environment variable holding it via
`api_key_env`, and the key is stripped before run metadata is written.

```bash
cp .env.example .env
```

**4. Point a config at your endpoint.** One model per file in `configs/`. Edit
`base_url` and `model_name` to match what you serve, or add a file of your own:

```yaml
model_parameters:
  model_name: "openai/your-model-id"     # the "openai/" prefix selects the OpenAI protocol
  base_url: "http://your-endpoint:8000/v1"
  api_key_env: "OR_BENCH_API_KEY"
  timeout: 3600
  extra_body:                             # reasoning off; drop this block to leave it on
    chat_template_kwargs:
      enable_thinking: false
  generation_parameters:
    temperature: 0.0                      # do not pin max_new_tokens here, see below
```

Configs come in pairs: the plain name is the reasoning-off variant, `-thinking` is the
same model with reasoning left on.

## Running the benchmark

QLP route:

```bash
.venv/bin/python run.py configs/qwen3.6-27b.yaml --tasks "qipfinal_modeling|0" --custom-tasks tasks/qipfinal_modeling.py --max-gen-tokens 8192 --concurrent-requests 2
```

Julia route:

```bash
.venv/bin/python run.py configs/qwen3.6-27b.yaml --tasks "qipfinal_julia|0" --custom-tasks tasks/qipfinal_julia.py --max-gen-tokens 8192 --concurrent-requests 2
```

| Flag | Effect | Default |
|---|---|---|
| `--max-gen-tokens` | Generation budget. Sets `generation_size` **and** `max_new_tokens` | 8192 |
| `--time-limit-qlp` | Yasol's `--timeLimit` per instance, capped by `YASOL_MAX_TIMEOUT` | 60 |
| `--concurrent-requests` | Requests kept in flight against the endpoint | 1 |
| `--shots` | Worked examples in the prompt: 0 or 1 | 1 |
| `--ruleset` | Where role and format rules go: `user`, `split`, `system`, `prohibitions` | `user` |
| `--max-samples` | Stop after N instances (for smoke tests) | all |
| `--yasol-url` | Address of the solver container | `http://127.0.0.1:8010` |

Two of these are less obvious than they look.

**`--max-gen-tokens` must set both places.** lighteval's LiteLLM backend prefers
`max_completion_tokens` from `generation_parameters` and falls back to the task's
`generation_size`. If those two disagreed, the config would silently win — which is why no
config pins `max_new_tokens`. With reasoning on, use `32768`; the chain of thought
otherwise eats the budget and the answer comes back empty.

**`--shots 0` does not produce usable numbers.** Measured across models and both routes,
zero-shot scores 0.000 with every single instance failing as a format error: without the
worked example the models invent a plausible but wrong file layout (section names and
their order for the QLP route, library signatures for the Julia route). The rules in the
prompt constrain what goes *inside* the sections, never their order — the example is what
carries that. `--shots 0` is a finding, not a measurement condition. **All reportable
numbers are 1-shot.**

The number after the task name (`qipfinal_modeling|0`) is lighteval's own few-shot counter
and has no effect here; `--shots` is what controls it.
