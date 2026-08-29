"""Strategy check: static verification of a strategy before/alongside backtests.

Runs a battery of cheap, deterministic checks against the spec and produces a
report with per-check verdicts (pass / warn / fail / info):

- DSL syntax validity of every rule
- rule coverage (at least one entry and one exit)
- tautological / always-false rule expressions
- indicator declaration sanity
- risk-parameter sanity (RR >= 1, stop vs spread, per-trade risk, daily cap vs
  planned trade count)
- data availability for the declared pairs / timeframes
- a review of the latest completed backtest's outcomes (if any)
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.dsl.evaluator import validate_expression
from app.dsl.parser import Node, parse_expression
from app.schemas.strategy import StrategySpec

ALLOWED_TIMEFRAMES = frozenset({"M1", "M5", "M15", "H1"})
SUPPORTED_INDICATORS = frozenset({"EMA", "SMA", "RSI", "ATR", "BB", "VWAP"})


# -- AST helpers ------------------------------------------------------------
def _node_equal(a: Node | None, b: Node | None) -> bool:
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    if a.kind != b.kind or a.name != b.name or a.value != b.value:
        return False
    if len(a.args) != len(b.args):
        return False
    if not _node_equal(a.left, b.left) or not _node_equal(a.right, b.right):
        return False
    return all(_node_equal(x, y) for x, y in zip(a.args, b.args))


def _collect_calls(node: Node | None, out: list[Node]) -> None:
    if node is None:
        return
    if node.kind == "call":
        out.append(node)
    _collect_calls(node.left, out)
    _collect_calls(node.right, out)
    for arg in node.args:
        _collect_calls(arg, out)


def _find_binary(node: Node | None, out: list[Node]) -> None:
    if node is None:
        return
    if node.kind == "binary":
        out.append(node)
    _find_binary(node.left, out)
    _find_binary(node.right, out)
    for arg in node.args:
        _find_binary(arg, out)


def _rule_issues(expression: str) -> list[tuple[str, str]]:
    errors = validate_expression(expression)
    if errors:
        return [("fail", "DSL syntax: " + "; ".join(errors))]
    node = parse_expression(expression)
    issues: list[tuple[str, str]] = []

    binaries: list[Node] = []
    _find_binary(node, binaries)
    for b in binaries:
        if b.value in ("==", "!=", ">", ">=", "<", "<=") and _node_equal(b.left, b.right):
            issues.append(
                ("fail", f"tautological '{b.value}' comparison between identical sides")
            )

    calls: list[Node] = []
    _collect_calls(node, calls)
    for call in calls:
        if call.name in ("crossover", "crossunder") and len(call.args) == 2:
            if _node_equal(call.args[0], call.args[1]):
                issues.append(
                    ("fail", f"{call.name}() with identical inputs is always false")
                )
        for arg in call.args:
            if arg.kind == "literal" and (isinstance(arg.value, float) and arg.value != arg.value):
                issues.append(("fail", "non-finite literal"))
    return issues


# -- checks ----------------------------------------------------------------
def _check_risk(spec: StrategySpec) -> list[tuple[str, str]]:
    issues: list[tuple[str, str]] = []
    rm = spec.risk_management
    sl = rm.stop_loss_method
    sp = rm.stop_loss_parameters
    tp = rm.take_profit_method
    tpg = rm.take_profit_parameters

    risk_pct = rm.risk_per_trade_pct
    if risk_pct > 4.0:
        issues.append(("fail", f"risk_per_trade {risk_pct}% is extreme (engine cap is 5%)"))
    elif risk_pct > 2.0:
        issues.append(("warn", f"risk_per_trade {risk_pct}% is aggressive"))

    if tp == "risk_reward":
        rr = float(tpg.get("risk_reward_ratio", 1.0))
        if rr < 1.0:
            issues.append(("fail", "risk_reward_ratio < 1 means take profit is smaller than risk"))
        elif rr < 1.1:
            issues.append(("warn", "risk_reward_ratio is barely above 1 (weak expectancy, costs can flip it)"))
    elif tp == "FIXED":
        tp_dist = float(tpg.get("fixed_distance_pips", 0.0))
        sl_dist = float(sp.get("fixed_distance_pips", 0.0))
        if sl_dist > 0 and tp_dist > 0 and tp_dist < sl_dist:
            issues.append(("warn", "FIXED take-profit distance is smaller than the FIXED stop distance"))

    if sl in ("ATR", "VOLATILITY") and float(sp.get("atr_multiplier", 1.2) or 1.2) < 1.0:
        issues.append(("warn", "A/volatility stop uses a multiplier below 1.0 (very tight)"))

    # stop vs spread
    if sl == "FIXED":
        stop_pips = float(sp.get("fixed_distance_pips", 0.0))
        if stop_pips > 0 and stop_pips <= 2 * float(spec.execution_filters.max_spread_pips):
            issues.append(("warn", f"FIXED stop of {stop_pips} pips is tighter than 2x the max spread config"))

    if rm.max_daily_loss_pct < risk_pct * max(1, rm.max_trades_per_day):
        issues.append(
            (
                "warn",
                "daily loss cap is smaller than the worst-case draw of the planned trades "
                "(max_daily_loss < risk_per_trade x max_trades_per_day)",
            )
        )
    if rm.max_trades_per_day == 0:
        issues.append(("warn", "max_trades_per_day = 0 blocks every trade in the engine"))
    if rm.max_open_positions < 1:
        issues.append(("fail", "max_open_positions must be >= 1"))
    return issues


def _check_data(spec: StrategySpec, symbols: list[str] | None) -> list[tuple[str, str]]:
    issues: list[tuple[str, str]] = []
    if symbols:
        known = {s.upper() for s in symbols}
        missing = [p.upper() for p in spec.supported_pairs if p.upper() not in known]
        if missing:
            issues.append(("fail", f"pairs unavailable in the data feed: {', '.join(missing)}"))
    for tf in spec.supported_timeframes:
        if tf.upper() not in ALLOWED_TIMEFRAMES:
            issues.append(("warn", f"timeframe {tf} is not served by the data provider"))
    if spec.execution_filters.max_spread_pips > 5.0:
        issues.append(("warn", "max_spread_pips > 5 pips is very permissive for a scalper"))
    return issues


def _check_indicators(spec: StrategySpec) -> list[tuple[str, str]]:
    issues: list[tuple[str, str]] = []
    for ind in spec.indicators:
        name = (ind.name or "").upper()
        if name not in SUPPORTED_INDICATORS:
            issues.append(("warn", f"declared indicator '{name}' is not computed by the backtester"))
        period = ind.parameters.get("period")
        if period is not None:
            try:
                if int(period) < 2:
                    issues.append(("fail", f"indicator {name} uses invalid period {period}"))
            except (TypeError, ValueError):
                issues.append(("warn", f"indicator {name} has a non-integer period {period!r}"))
    return issues


def _check_backtest_review(metrics: dict | None) -> list[tuple[str, str]]:
    if not metrics or not metrics.get("num_trades"):
        return [("info", "no completed backtest yet — run one to unlock the performance review")]
    issues: list[tuple[str, str]] = []
    try:
        pf = metrics.get("profit_factor")
        win_rate = metrics.get("win_rate")
        num = int(metrics.get("num_trades", 0))
        dd = metrics.get("max_drawdown_pct")
        if num < 30:
            issues.append(("warn", f"only {num} trades in the latest backtest — sample too small to be conclusive"))
        if pf is not None:
            try:
                pf_f = float(pf)
            except (TypeError, ValueError):
                pf_f = None
            if pf_f is not None:
                if pf_f < 1.0:
                    issues.append(("fail", "latest backtest lost money after costs (profit factor < 1.0)"))
                elif pf_f < 1.15:
                    issues.append(("warn", "latest backtest profit factor is marginal (target >= 1.15)"))
        if win_rate is not None and win_rate < 0.30:
            issues.append(("warn", "win rate is below 30% — recovery depends on a high RR"))
        if dd is not None and dd < -20:
            issues.append(("warn", f"latest backtest drawdown is deep ({dd:.1f}%)"))
    except Exception:  # noqa: BLE001
        issues.append(("warn", "could not interpret latest backtest metrics"))
    return issues or [("pass", "latest backtest outcomes are within the advisory targets")]


# -- live context check ----------------------------------------------------
def _check_live(live_context: dict | None) -> list[tuple[str, str]]:
    """Verify the strategy's pair against the current, real market feed."""
    if not live_context:
        return [("info", "no live quote available for this pair — run the checker against a feed to unlock live checks")]
    issues: list[tuple[str, str]] = []
    pair = live_context.get("symbol")
    provider = live_context.get("provider", "unknown")
    feed = live_context.get("feed_state", "unknown")
    market_status = live_context.get("market_status", "unknown")
    spread = live_context.get("spread_pips")
    stale = bool(live_context.get("is_stale"))

    if stale or feed in ("STALE", "DISCONNECTED", "CONNECTING"):
        issues.append(
            (
                "fail" if stale else "warn",
                f"feed for {pair} is {feed} — signals and paper orders are blocked until fresh data",
            )
        )
    if market_status not in ("open", "unknown"):
        issues.append(("info", f"market for {pair} is {market_status} — monitoring paused"))
    if spread is not None:
        max_spread = live_context.get("max_spread_pips")
        note = f" (within limit)" if max_spread is not None and spread <= max_spread else ""
        if max_spread is not None and spread > max_spread:
            issues.append(("warn", f"current spread {spread} pips exceeds the strategy max ({max_spread})"))
        else:
            issues.append(("info", f"current spread {spread} pips via {provider}{note}"))
    else:
        issues.append(("info", f"no live quote yet for {pair} via {provider}"))
    return issues or [("pass", "live feed is healthy")]


# -- main entry ------------------------------------------------------------
def run_strategy_check(
    spec: StrategySpec,
    available_symbols: list[str] | None = None,
    latest_metrics: dict | None = None,
    live_context: dict | None = None,
) -> dict:
    checks: list[dict] = []

    for group in ("entry_rules", "exit_rules"):
        rules = getattr(spec, group)
        merged = []
        if not rules:
            merged.append(("warn", "no rules defined"))
        else:
            for rule in rules:
                merged.extend(_rule_issues(rule.expression))
        severity = _aggregate_severity([s for s, _ in merged])
        checks.append(
            {
                "check": f"{group}:dsl",
                "severity": severity,
                "detail": "valid" if severity == "pass" and merged else "; ".join(f"{d}" for _, d in merged),
            }
        )

    # coverage
    if not spec.entry_rules:
        checks.append({"check": "coverage", "severity": "warn", "detail": "no entry rules — strategy can never enter"})
    elif not spec.exit_rules:
        checks.append({"check": "coverage", "severity": "warn", "detail": "no exit rules — only stops/targets manage exits"})
    else:
        checks.append({"check": "coverage", "severity": "pass", "detail": "entry and exit rules defined"})

    # identical exit == entry
    entry_exprs = {r.expression for r in spec.entry_rules}
    for r in spec.exit_rules:
        if r.expression in entry_exprs:
            checks.append(
                {
                    "check": "exit_vs_entry",
                    "severity": "warn",
                    "detail": f"exit rule '{r.id}' is identical to an entry rule — may exit on the exact bar of entry",
                }
            )

    for label, fn in (
        ("risk", lambda: _check_risk(spec)),
        ("data", lambda: _check_data(spec, available_symbols)),
        ("indicators", lambda: _check_indicators(spec)),
        ("backtest", lambda: _check_backtest_review(latest_metrics)),
        ("live_data", lambda: _check_live(live_context)),
    ):
        rows = fn()
        severity = _aggregate_severity([s for s, _ in rows]) if rows else "pass"
        checks.append(
            {
                "check": label,
                "severity": severity,
                "detail": "; ".join(d for _, d in rows) if rows else "no issues",
            }
        )

    ranking = {"pass": 0, "info": 1, "warn": 2, "fail": 3}
    fails = [c for c in checks if c["severity"] == "fail"]
    warns = [c for c in checks if c["severity"] == "warn"]
    if fails:
        overall = "fail"
    elif warns:
        overall = "warn"
    else:
        overall = "pass"

    summary = (
        f"Blocking issues: {len(fails)}. Advisory warnings: {len(warns)}."
        if fails or warns
        else "All static checks passed."
    )
    return {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "overall": overall,
        "summary": summary,
        "checks": sorted(checks, key=lambda c: ranking[c["severity"]], reverse=True),
    }


def _aggregate_severity(severities: list[str]) -> str:
    if "fail" in severities:
        return "fail"
    if "warn" in severities:
        return "warn"
    if not severities:
        return "pass"
    return severities[0]