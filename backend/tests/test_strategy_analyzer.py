"""Tests for the AI Strategy Analyzer (single-call, cached by text hash).

Covers: input caps, cache-by-hash, schema validation, rejection of unsafe
designs (martingale/grid/no-stop-loss), NEEDS_USER_INPUT for ambiguous text,
allow-listed DSL conversion, no eval/exec, provider labels, rate limiting,
cross-workspace isolation, and safe (digest-only) audit logging.
"""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

VALID_PROMPT = (
    "Trend pullback strategy on EURUSD and GBPUSD on M5 during the London session, "
    "using EMA 20 and EMA 50, ATR 14 for the stop, risk 1% per trade, max 5 trades "
    "a day, max spread 2 pips."
)


def _email():
    return f"ana-{uuid.uuid4().hex[:8]}@example.com"


def _user():
    email = _email()
    r = client.post("/api/auth/register", json={"email": email, "password": "secret-pass-123"})
    assert r.status_code == 201, r.text
    token = client.post("/api/auth/login", json={"email": email, "password": "secret-pass-123"}).json()["access_token"]
    return token


def _h(token):
    return {"Authorization": f"Bearer {token}"}


def _analyze(token, text, expect=200):
    return client.post("/api/strategy-analyzer/analyze", json={"prompt_text": text}, headers=_h(token))


# ---------------------------------------------------------------------------
# Core behavior
# ---------------------------------------------------------------------------
def test_analyze_valid_prompt_returns_structured_analysis():
    token = _user()
    r = _analyze(token, VALID_PROMPT)
    assert r.status_code == 200, r.text
    a = r.json()["analysis"]
    assert a["testability_status"] == "VALID"
    assert a["strategy_family"] == "trend_pullback"
    assert a["timeframe"] == "M5"
    assert "EURUSD" in a["recommended_symbols"] and "GBPUSD" in a["recommended_symbols"]
    assert a["entry_rules"][0]["side"] in ("long", "short")
    assert a["stop_loss"]["type"] == "ATR"
    assert r.json()["converted"] is True
    assert r.json()["strategy_spec"]["risk_management"]["risk_per_trade_pct"] == 1.0
    assert r.json()["provider_used"] == "mock"


def test_identical_prompt_is_cached_by_text_hash():
    token = _user()
    first = client.post("/api/strategy-analyzer/analyze", json={"prompt_text": VALID_PROMPT}, headers=_h(token))
    assert first.status_code == 200 and first.json()["cache_hit"] is False
    second = client.post("/api/strategy-analyzer/analyze", json={"prompt_text": VALID_PROMPT}, headers=_h(token))
    assert second.status_code == 200 and second.json()["cache_hit"] is True
    assert second.json()["text_sha256"] == first.json()["text_sha256"]
    assert second.json()["analysis"]["name"] == first.json()["analysis"]["name"]


def test_caching_is_workspace_scoped():
    token_a = _user()
    token_b = _user()
    client.post("/api/strategy-analyzer/analyze", json={"prompt_text": VALID_PROMPT}, headers=_h(token_a))
    r = client.post("/api/strategy-analyzer/analyze", json={"prompt_text": VALID_PROMPT}, headers=_h(token_b))
    assert r.status_code == 200 and r.json()["cache_hit"] is False


def test_oversized_input_rejected():
    token = _user()
    r = _analyze(token, "x" * 5000, expect=422)
    assert r.status_code == 422


def test_too_short_input_rejected():
    token = _user()
    r = _analyze(token, "buy dips")
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# Rejection rules (unsafe / ambiguous designs)
# ---------------------------------------------------------------------------
def test_martingale_rejected():
    token = _user()
    r = _analyze(token, "On EURUSD M5, martingale: double my bet after every loss.")
    assert r.status_code == 200
    a = r.json()["analysis"]
    assert a["testability_status"] == "INVALID"
    assert any("martingale" in w.lower() for w in a["warnings"])
    assert r.json()["converted"] is False


def test_unlimited_averaging_rejected():
    token = _user()
    r = _analyze(token, "On EURUSD M15 use a grid, placing a new order every 20 pips forever.")
    assert r.status_code == 200
    assert r.json()["analysis"]["testability_status"] == "INVALID"
    assert any("grid" in w.lower() for w in r.json()["analysis"]["warnings"])


def test_no_stop_loss_rejected():
    token = _user()
    r = _analyze(token, "Momentum on EURUSD H1, no stop loss ever, let winners ride.")
    a = r.json()["analysis"]
    assert a["testability_status"] == "INVALID"
    assert any("stop loss" in w.lower() for w in a["warnings"])


def test_ambiguous_input_requires_user_input():
    token = _user()
    r = _analyze(token, "just scalp the NFP news and hope for the best")
    assert r.status_code == 200
    a = r.json()["analysis"]
    assert a["testability_status"] == "NEEDS_USER_INPUT"
    assert r.json()["converted"] is False


def test_vague_symbols_require_user_input():
    token = _user()
    r = _analyze(token, "use EMA crossover on some forex pairs on M15")
    a = r.json()["analysis"]
    assert a["testability_status"] == "NEEDS_USER_INPUT"


# ---------------------------------------------------------------------------
# Safe DSL conversion (no eval/exec)
# ---------------------------------------------------------------------------
def test_converted_spec_uses_only_allowlisted_dsl():
    token = _user()
    r = _analyze(token, VALID_PROMPT)
    spec = r.json()["strategy_spec"]
    from app.services.strategy_check import _rule_issues

    for rule in spec["entry_rules"] + spec["exit_rules"]:
        verdicts = _rule_issues(rule["expression"])
        assert not [v for v in verdicts if v[0] == "fail"], (rule["expression"], verdicts)


def test_converted_spec_survives_strategy_post():
    token = _user()
    r = _analyze(token, VALID_PROMPT)
    spec = r.json()["strategy_spec"]
    created = client.post("/api/strategies", json={"name": "AI-Ok", "spec": spec}, headers=_h(token))
    assert created.status_code == 201, created.text


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------
def test_rate_limit_blocks_excess_unique_analyses():
    token = _user()
    statuses = []
    # force unique prompts within the per-hour budget +1
    for i in range(32):
        text = f"{VALID_PROMPT} variant {i}"
        statuses.append(_analyze(token, text).status_code)
    assert statuses.count(429) >= 1
    assert statuses[:30].count(429) == 0  # budget allows the first 30 unique analyses


# ---------------------------------------------------------------------------
# Audit logging (digest only, never the prompt)
# ---------------------------------------------------------------------------
def test_analyze_audits_digest_without_prompt_text():
    token = _user()
    client.post("/api/strategy-analyzer/analyze", json={"prompt_text": VALID_PROMPT}, headers=_h(token))
    rows = client.get("/api/audit/logs?action=strategy_analyze", headers=_h(token))
    assert rows.status_code == 200
    payloads = [item.get("payload", {}) for item in rows.json()["items"]]
    assert payloads, "expected at least one strategy_analyze audit row"
    assert all(p.get("sha256_prefix") for p in payloads)
    body = rows.text
    assert VALID_PROMPT[:50] not in body  # prompt contents must never be logged


def test_strategy_families_are_detected():
    token = _user()
    cases = {
        "Breaks out of the 20 bar range on EURUSD M15, ATR stop.": "breakout",
        "Mean reversion fading RSI extremes on GBPUSD M5 with a stop.": "mean_reversion",
        "Liquidity sweep failure long entries on USDJPY M5 with ATR stops.": "liquidity_sweep",
        "EMA crossover momentum on USDCAD H1, fixed stop.": "momentum",
    }
    for text, family in cases.items():
        r = _analyze(token, text)
        assert r.status_code == 200, r.text
        assert r.json()["analysis"]["strategy_family"] == family, text


def test_pair_symbols_do_not_trigger_dca_grid_rejection():
    # "USDCAD" contains the substring "dca"; the DCA rejection pattern is
    # word-bounded so real pair codes must never flag an unlimited-grid design.
    text = (
        "Breakout on USDCAD during the New York session on M15, using the highest and "
        "lowest of 20 candles for the range, ATR 14 stop, risk 1.5% per trade, "
        "max 4 trades a day, max spread 3 pips."
    )
    r = _analyze(token=_user(), text=text)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["analysis"]["testability_status"] == "VALID", body
    assert not any("grid" in w or "averaging" in w for w in body["analysis"]["warnings"]), body
    assert body["converted"] is True and body["strategy_spec"] is not None


def test_directional_filter_produces_single_side_spec():
    token = _user()
    r = _analyze(token, "Long only trend pullback on EURUSD M5, EMA 20/50, ATR 14 stop.")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["analysis"]["testability_status"] == "VALID", body
    rules = body["strategy_spec"]["entry_rules"]
    assert len(rules) == 1 and rules[0]["id"].startswith("long"), rules
    assert "in_session" in rules[0]["expression"]


def test_needs_user_input_still_gets_prefilled_spec():
    # Missing a symbol + timeframe in the text -> must be completed, but the
    # suggested spec must carry fully-formed (non-empty) DSL expressions so the
    # user never has to hand-write an expression.
    token = _user()
    r = _analyze(token, "Momentum strategy that buys EMA crossovers with a stop loss and 1:2 reward.")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["analysis"]["testability_status"] == "NEEDS_USER_INPUT", body
    suggested = body["suggested_spec"]
    assert suggested is not None
    assert suggested["sessions_utc"] and suggested["entry_rules"]
    for rule in suggested["entry_rules"] + suggested["exit_rules"]:
        assert rule["expression"].strip(), rule
    assert "timeframe" in " ".join(body["analysis"]["warnings"]).lower()


def test_breakout_respects_declared_range_window():
    token = _user()
    r = _analyze(token, "Breakout of the 50 bar range on USDCAD M15, ATR 14 stop.")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["converted"] is True, body
    long_expr = next(r["expression"] for r in body["strategy_spec"]["entry_rules"] if r["id"].startswith("long"))
    assert "highest(high,50)" in long_expr, long_expr


def test_structure_stop_requires_user_input():
    token = _user()
    r = _analyze(token, "Sell breakouts on EURUSD H1 with a stop below structure and 1:2 reward.")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["analysis"]["stop_loss"]["type"] == "STRUCTURE"
    assert body["analysis"]["testability_status"] == "NEEDS_USER_INPUT", body
    assert not body["converted"]