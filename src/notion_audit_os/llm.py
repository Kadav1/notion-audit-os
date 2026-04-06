"""LLM adapter boundary.

The deterministic pipeline (intake parsing, notes normalization, finding
drafting) is the source of truth for **structure**. LLMs are only ever
allowed to rewrite *language* — they never decide categories, never
invent observations, never set scores, and never change the recommended
package.

To keep that boundary clean, every LLM call in the project goes through
an :class:`LLMAdapter`. v1 ships with a :class:`StubLLMAdapter` that
does no model calls at all — the project runs end-to-end with zero AI
dependencies. A real LM Studio (or other) adapter can be wired in
later by implementing the same protocol.

Prompts, when they exist, live as small text files under ``prompts/``
and are loaded by the adapter. They must not be inlined inside intake,
notes, or findings business logic.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable


@runtime_checkable
class LLMAdapter(Protocol):
    """Minimal interface every LLM provider must implement.

    Methods are intentionally small and pure-string. Any structured
    work (JSON parsing, category routing, scoring, etc.) is the caller's
    job — the adapter only rewrites prose.
    """

    name: str

    def summarize(self, text: str, *, max_chars: int = 400) -> str:
        """Return a shorter prose summary of ``text``.

        Implementations must never invent facts that are not in the
        input. A trivial implementation may return ``text`` unchanged
        (truncated to ``max_chars``).
        """
        ...

    def draft_recommendation(
        self,
        *,
        category: str,
        observation: str,
        evidence: list[str],
    ) -> str:
        """Draft a one-sentence recommendation for an observation.

        Implementations must keep the recommendation grounded in the
        provided observation/evidence. Returning an empty string is
        always allowed and means "no AI-drafted recommendation; defer
        to the human reviewer".
        """
        ...


class StubLLMAdapter:
    """Default no-op adapter. Does no model calls.

    * :meth:`summarize` returns the input truncated to ``max_chars``.
    * :meth:`draft_recommendation` returns ``""`` so the human reviewer
      always supplies the recommendation in v1.

    This is the safest possible default: the project runs without any
    AI infrastructure, and AI never silently authors content.
    """

    name = "stub"

    def summarize(self, text: str, *, max_chars: int = 400) -> str:
        if not text:
            return ""
        text = " ".join(text.split())
        if len(text) <= max_chars:
            return text
        return text[: max_chars - 1].rstrip() + "…"

    def draft_recommendation(
        self,
        *,
        category: str,
        observation: str,
        evidence: list[str],
    ) -> str:
        return ""


_default_adapter: LLMAdapter = StubLLMAdapter()


def get_default_adapter() -> LLMAdapter:
    """Return the process-wide default adapter (the stub in v1)."""
    return _default_adapter


def set_default_adapter(adapter: LLMAdapter) -> None:
    """Override the default adapter. Intended for tests and Phase VIII+ wiring."""
    global _default_adapter
    _default_adapter = adapter


def load_prompt(name: str) -> str:
    """Load a prompt template by name from the project's ``prompts/`` directory.

    Prompts are kept out of business modules so they can be reviewed and
    edited without touching code.
    """
    from .storage import project_root  # local import to avoid cycle

    path: Path = project_root() / "prompts" / f"{name}.txt"
    if not path.is_file():
        raise FileNotFoundError(f"prompt not found: {path}")
    return path.read_text(encoding="utf-8")


__all__ = [
    "LLMAdapter",
    "StubLLMAdapter",
    "get_default_adapter",
    "set_default_adapter",
    "load_prompt",
]
