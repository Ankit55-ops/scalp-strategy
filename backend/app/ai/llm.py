"""Provider-agnostic LLM abstraction for strategy generation.

Supports a `mock` mode (offline, deterministic candidates) and an
OpenAI-compatible HTTP endpoint. The LLM only ever returns JSON that is
validated through the Pydantic StrategySpec schema. Executable code is never
requested and never executed.
"""

from __future__ import annotations

from typing import Any

import httpx

from app.core.config import get_settings
from app.schemas.strategy import StrategySpec


class LLMClient:
    """Thin OpenAI-compatible chat client. Returns only response text."""

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
    ) -> None:
        s = get_settings()
        self.base_url = base_url or s.LLM_BASE_URL
        self.api_key = api_key or s.LLM_API_KEY
        self.model = model or s.LLM_MODEL

    def chat(self, system: str, user: str, temperature: float = 0.4) -> str:
        url = self.base_url.rstrip("/") + "/chat/completions"
        headers = {"Authorization": f"Bearer {self.api_key}"}
        payload = {
            "model": self.model,
            "temperature": temperature,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        with httpx.Client(timeout=60) as client:
            resp = client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"].strip()


def build_system_prompt() -> str:
    return (
        "You are a rigorous quantitative research assistant for FOREX SCALPING strategy "
        "development. You produce ONLY valid JSON conforming exactly to the provided schema. "
        "Rules must be machine-readable expressions using only these allow-listed functions: "
        "ema, sma, rsi, atr, crossover, crossunder, highest, lowest, stdev, abs, min, max, "
        "and comparison/boolean/arithmetic operators. Allowed symbols: open, high, low, close, "
        "volume, spread_pips, time_minute, in_session, is_blackout. Prefer simple, testable, "
        "rule-based strategies. Never use future information. Never claim a strategy works on "
        "every pair. Avoid martingale, grid averaging, revenge trading, unlimited averaging "
        "down, and no-stop-loss designs. State every indicator parameter explicitly. Include "
        "plain_english_explanation, assumptions, failure_modes. Return ONLY the JSON object, "
        "no markdown fences, no commentary."
    )
