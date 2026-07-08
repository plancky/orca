"""Prompt loader. Each pipeline stage owns its own prompt file in this package.

A prompt named `<name>` resolves to `<name>.txt` (raw template) if present,
otherwise to a `<name>.py` module exposing a module-level `PROMPT: str`. Stages
added by later waves (classifier, planner, extractor, synthesizer, clarify) drop
their file here; nothing else in this package needs to change.
"""

from importlib import import_module
from pathlib import Path

_PROMPTS_DIR = Path(__file__).resolve().parent


class PromptNotFoundError(LookupError):
    """No `<name>.txt` or `<name>.py` (with a string `PROMPT`) could be resolved."""


def load_prompt(name: str) -> str:
    text_file = _PROMPTS_DIR / f"{name}.txt"
    if text_file.is_file():
        return text_file.read_text(encoding="utf-8")
    try:
        module = import_module(f"{__package__}.{name}")
    except ModuleNotFoundError as exc:
        raise PromptNotFoundError(name) from exc
    prompt = getattr(module, "PROMPT", None)
    if not isinstance(prompt, str):
        raise PromptNotFoundError(f"prompt module '{name}' has no string PROMPT")
    return prompt
