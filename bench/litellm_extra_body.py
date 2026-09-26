"""Passthrough for provider-specific request-body fields in lighteval's LiteLLM backend.

lighteval's `LiteLLMModelConfig` is a pydantic model with `extra="forbid"`, and its
`GenerationParameters` is a closed list too. The backend builds a fixed set of arguments
for `litellm.completion()`, so there is no supported way to send provider-specific body
fields — such as llama.cpp's `chat_template_kwargs`, which is what switches reasoning off
on the Qwen models served by this endpoint.

Two pieces are needed:

* `ExtraBodyLiteLLMModelConfig` — carries the extra fields. They have to sit on the
  config rather than in the runner, because lighteval derives its response-cache key from
  `model_config.model_dump()`. Without this, a thinking-on and a thinking-off run of the
  same model would share cached responses.
* `install_extra_body()` — merges the fields into every request. lighteval looks
  `completion` up on the `litellm` module at call time, so wrapping the module attribute
  is enough; no lighteval internals are touched.
"""

import json
import threading
from pathlib import Path
from typing import Any

import litellm
from lighteval.models.endpoints.litellm_model import LiteLLMModelConfig


class ExtraBodyLiteLLMModelConfig(LiteLLMModelConfig):
    """LiteLLM config plus a raw request-body passthrough.

    `lighteval.models.model_loader.load_model` dispatches on `isinstance(config,
    LiteLLMModelConfig)`, so the subclass is routed to the normal LiteLLM client.
    """

    extra_body: dict[str, Any] = {}


def install_extra_body(extra_body: dict[str, Any], dump_messages_to: str | Path | None = None) -> None:
    """Merge `extra_body` into every litellm.completion() call, and optionally record the
    first request's messages.

    The message dump exists because where a prompt actually lands is not obvious: lighteval
    documents `Doc.instruction` as a system prompt, but for this backend it is prepended to
    the *user* message, and a real system message only comes from `ModelConfig.system_prompt`.
    Writing the first request verbatim makes that auditable per run instead of a matter of
    belief.
    """
    original_completion = litellm.completion
    if getattr(original_completion, "_extra_body_installed", False):
        raise RuntimeError("install_extra_body() was already called in this process.")

    if not extra_body and dump_messages_to is None:
        return

    dumped = threading.Event()

    def completion_with_extra_body(**kwargs):
        if extra_body:
            # An explicit extra_body on the call site wins over the configured one.
            kwargs["extra_body"] = {**extra_body, **(kwargs.get("extra_body") or {})}
        if dump_messages_to is not None and not dumped.is_set():
            dumped.set()
            Path(dump_messages_to).write_text(
                json.dumps(kwargs.get("messages"), indent=2, ensure_ascii=False)
            )
        return original_completion(**kwargs)

    completion_with_extra_body._extra_body_installed = True
    litellm.completion = completion_with_extra_body


def disable_litellm_response_cache() -> None:
    """Turn off LiteLLM's own on-disk response cache.

    lighteval enables it at import time and passes `caching=True` per request. Its cache
    key is built from known LLM API params only — `extra_body` is not among them, and
    provider-specific optional params are excluded unless
    `enable_caching_on_provider_specific_optional_params` is set. A thinking-off run would
    therefore be served the cached thinking-on response. lighteval's own sample cache
    (keyed on the full model config, including `extra_body`) stays active and does the
    same job correctly.
    """
    litellm.cache = None
