"""Pluggable LLM client. Anthropic for live runs; a deterministic Mock for
tests so the entire graph runs end-to-end with NO API key (the agents are
the cheapest, most swappable part — the moat is the deterministic harness)."""

from __future__ import annotations

import os
from typing import Protocol


class LLMClient(Protocol):
    def complete_json(self, system: str, user: str, schema: dict,
                      *, role: str) -> dict: ...


class MockLLMClient:
    """Scripted responses keyed by role; pops in order. Lets tests drive the
    graph down any path (PASS, FAIL, revise) deterministically."""

    def __init__(self, responses: dict[str, list[dict]]):
        self._q = {k: list(v) for k, v in responses.items()}

    def complete_json(self, system: str, user: str, schema: dict,
                      *, role: str) -> dict:
        q = self._q.get(role)
        if not q:
            raise AssertionError(f"MockLLM: no scripted response left for role '{role}'")
        return q.pop(0)


class AnthropicClient:
    """Live client. Forces structured JSON via tool-use so an agent can never
    return unparseable prose. Reads ANTHROPIC_API_KEY from env; model from
    ANTHROPIC_MODEL (or a per-role override map)."""

    def __init__(self, model: str | None = None,
                 role_models: dict[str, str] | None = None,
                 max_tokens: int = 2000):
        import anthropic                       # imported lazily; tests don't need it
        self._client = anthropic.Anthropic()    # ANTHROPIC_API_KEY from env
        self._model = model or os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")
        self._role_models = role_models or {}
        self._max_tokens = max_tokens

    def complete_json(self, system: str, user: str, schema: dict,
                      *, role: str) -> dict:
        model = self._role_models.get(role, self._model)
        resp = self._client.messages.create(
            model=model, max_tokens=self._max_tokens, system=system,
            messages=[{"role": "user", "content": user}],
            tools=[{"name": "emit", "description": "Emit the structured result.",
                    "input_schema": schema}],
            tool_choice={"type": "tool", "name": "emit"},
        )
        for block in resp.content:
            if getattr(block, "type", None) == "tool_use":
                return dict(block.input)
        raise RuntimeError(f"{role}: model returned no structured tool_use block")


__all__ = ["LLMClient", "MockLLMClient", "AnthropicClient"]
