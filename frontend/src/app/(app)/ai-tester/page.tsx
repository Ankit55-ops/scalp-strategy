"use client";

import { FormEvent, useEffect, useMemo, useState } from "react";
import {
  ApiError,
  addStrategyVersion,
  analyzeStrategy,
  createRealBacktest,
  getExnessInstruments,
  getExnessStatusCard,
  getRealBacktest,
  getRealBacktestChart,
  getRealBacktestMetrics,
  getStrategies,
  listRealBacktests,
  realBacktestPreview,
  saveStrategy,
  tokenStore,
} from "@/lib/api";
import { Badge, Card, SectionTitle, Stat } from "@/components/ui";
import { ProviderConnectionStatusCard } from "@/components/ProviderConnectionStatusCard";
import CandlestickChart, { ChartMarker, ChartOverlay } from "@/components/chart/CandlestickChart";
import type {
  AIStrategyAnalysis,
  CandleView,
  InstrumentMappingView,
  ProviderConnectionStatusCard as CardData,
  RealBacktestChart,
  Strategy,
  StrategyAnalysis,
  ValidationMetrics,
  ValidationPreview,
  ValidationRun,
} from "@/types";

const INPUT =
  "w-full rounded-lg border border-border bg-bg px-3 py-2 text-sm outline-none focus:border-accent";

const EXEC_MODELS = [
  "BID_ASK_HISTORICAL_WHERE_AVAILABLE",
  "NEXT_CANDLE_OPEN",
  "SIGNAL_PRICE",
  "ESTIMATED_SPREAD_FROM_MID",
];

const ALLOWED_TIMEFRAMES = ["M1", "M5", "M15", "M30", "H1", "H4", "D1"];

const DSL_ALLOWED = ["ema", "sma", "rsi", "atr", "crossover", "crossunder", "highest", "lowest", "close", "high", "low", "and", "or", ">", "<", ">=", "<="];

const FAMILY_MAP: Record<string, string> = {
  trend_pullback: "trend_pullback",
  breakout: "breakout",
  mean_reversion: "mean_reversion",
  momentum: "momentum",
  range_fade: "range_fade",
  liquidity_sweep: "liquidity_sweep",
};

function toIso(dateVal: string): string {
  return new Date(`${dateVal}T00:00:00Z`).toISOString();
}

function fmtTs(ts: number | null): string {
  if (!ts) return "—";
  return new Date(ts * 1000).toISOString().slice(0, 16).replace("T", " ");
}

function statusTone(status: string): "good" | "bad" | "warn" | "default" {
  if (status === "COMPLETED") return "good";
  if (status === "FAILED") return "bad";
  if (status === "RUNNING" || status === "QUEUED") return "warn";
  return "default";
}

function metricTone(key: string, value: number): "good" | "bad" | "default" {
  if (/profit|return|sharpe|win|recovery/i.test(key)) return value >= 0 ? "good" : "bad";
  if (/drawdown|loss|exposure/i.test(key)) return value <= 0 ? "good" : "bad";
  return "default";
}

function toCandles(raw: CandleView[]): CandleView[] {
  return raw.map((c) => ({
    symbol: c.symbol ?? "—",
    timeframe: c.timeframe ?? "",
    ts: c.ts,
    open: c.open,
    high: c.high,
    low: c.low,
    close: c.close,
    volume: Number(c.volume || 0),
    source: c.source ?? "real_historical",
    is_complete: true,
  }));
}

function toMarkers(chart: RealBacktestChart): ChartMarker[] {
  const out: ChartMarker[] = [];
  for (const t of chart.trades) {
    if (t.entry_ts && t.entry_price) {
      out.push({ ts: t.entry_ts, price: t.entry_price, type: "entry", side: t.side === "short" ? "short" : "long" });
    }
    if (t.exit_ts && t.exit_price) {
      out.push({ ts: t.exit_ts, price: t.exit_price, type: "exit", side: t.side === "short" ? "short" : "long" });
    }
    if (t.stop) out.push({ ts: t.entry_ts, price: t.stop, type: "stop", side: t.side === "short" ? "short" : "long" });
    if (t.target) out.push({ ts: t.entry_ts, price: t.target, type: "target", side: t.side === "short" ? "short" : "long" });
  }
  return out;
}

function toOverlays(chart: RealBacktestChart): ChartOverlay[] {
  return Object.values(chart.overlays || {});
}

// Validate the edited spec object against the schema's hard requirements so the
// user gets precise feedback instead of a raw 422 from the backend.
function missingFields(spec: Record<string, unknown> | null): string[] {
  if (!spec || typeof spec !== "object") return ["valid JSON required"];
  const out: string[] = [];
  if (!Array.isArray(spec.supported_pairs) || (spec.supported_pairs as unknown[]).length === 0)
    out.push("`supported_pairs` must list at least one symbol");
  if (
    !Array.isArray(spec.supported_timeframes) ||
    (spec.supported_timeframes as unknown[]).length === 0 ||
    !(spec.supported_timeframes as string[]).every((tf) => ALLOWED_TIMEFRAMES.includes(tf))
  )
    out.push(`\`supported_timeframes\` must be one of ${ALLOWED_TIMEFRAMES.join("|")}`);
  if (!Array.isArray(spec.sessions_utc) || (spec.sessions_utc as unknown[]).length === 0)
    out.push("`sessions_utc` must list at least one {name, start, end} window (HH:MM UTC)");
  const entries = (spec.entry_rules as unknown[] | undefined) || [];
  const exits = (spec.exit_rules as unknown[] | undefined) || [];
  if (entries.length === 0 && exits.length === 0)
    out.push("at least one `entry_rules` or `exit_rules` rule is required");
  const rules = [...entries, ...exits] as { expression?: string }[];
  if (rules.some((r) => !r.expression || !r.expression.trim()))
    out.push("every rule needs a non-empty DSL `expression`");
  const rm = spec.risk_management as Record<string, unknown> | undefined;
  if (!rm || !rm.stop_loss_method || !rm.take_profit_method || !(typeof rm.risk_per_trade_pct === "number" && rm.risk_per_trade_pct > 0))
    out.push("`risk_management` needs stop_loss_method, take_profit_method and risk_per_trade_pct (> 0, ≤ 5)");
  return out;
}

export default function AiTesterPage() {
  const token = tokenStore.get() as string;

  const [card, setCard] = useState<CardData | null>(null);
  const [instruments, setInstruments] = useState<InstrumentMappingView[]>([]);
  const [savedStrategies, setSavedStrategies] = useState<Strategy[]>([]);

  // Panel 1: strategy input -> analysis
  const [prompt, setPrompt] = useState(
    "Trend pullback on EURUSD and GBPUSD on M5 during the London session, using EMA 20 and EMA 50, ATR 14 for the stop, risk 1% per trade, max 5 trades a day, max spread 2 pips."
  );
  const [analysis, setAnalysis] = useState<AIStrategyAnalysis | null>(null);
  const [converted, setConverted] = useState(false);
  const [cacheHit, setCacheHit] = useState(false);
  const [specJson, setSpecJson] = useState("");
  const [lastSavedSpecJson, setLastSavedSpecJson] = useState<string | null>(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);

  // Panel 2: save strategy
  const [savedStrategyId, setSavedStrategyId] = useState<string | null>(null);
  const [savedVersion, setSavedVersion] = useState<string | null>(null);

  // Panel 3: real-data backtest settings
  const [symbol, setSymbol] = useState("EURUSD");
  const [timeout, setTimeoutTf] = useState("M5");
  const [dateFrom, setDateFrom] = useState("2026-01-01");
  const [dateTo, setDateTo] = useState("2026-02-01");
  const [balance, setBalance] = useState(100000);
  const [execModel, setExecModel] = useState(EXEC_MODELS[0]);
  const [commission, setCommission] = useState(2);
  const [slippagePips, setSlippagePips] = useState(0.3);
  const [swapOn, setSwapOn] = useState(true);
  const [swapPoints, setSwapPoints] = useState(0.2);

  // Results
  const [preview, setPreview] = useState<ValidationPreview | null>(null);
  const [run, setRun] = useState<ValidationRun | null>(null);
  const [chart, setChart] = useState<RealBacktestChart | null>(null);
  const [metrics, setMetrics] = useState<ValidationMetrics | null>(null);
  const [recentRuns, setRecentRuns] = useState<ValidationRun[]>([]);

  const providerReady = card?.connection_status === "CONNECTED";
  const connId = instruments[0]?.connection_id ?? "";
  const runReady = providerReady && savedStrategyId !== null && !busy;

  // live spec validation
  const specObj = useMemo<Record<string, unknown> | null>(() => {
    if (!specJson.trim()) return null;
    try {
      return JSON.parse(specJson) as Record<string, unknown>;
    } catch {
      return null;
    }
  }, [specJson]);
  const specValidJson = specObj !== null;
  const missing = useMemo(() => missingFields(specObj), [specObj]);

  const markers = useMemo(() => (chart ? toMarkers(chart) : []), [chart]);
  const overlays = useMemo(() => (chart ? toOverlays(chart) : []), [chart]);

  const specDirty = lastSavedSpecJson !== null && specJson !== lastSavedSpecJson;
  const invalid = analysis?.testability_status === "INVALID";

  async function load() {
    try {
      setCard(await getExnessStatusCard(token));
    } catch {
      /* no provider */
    }
    try {
      setInstruments(await getExnessInstruments(token));
    } catch {
      /* none */
    }
    try {
      setSavedStrategies(await getStrategies(token));
    } catch {
      /* none */
    }
    try {
      setRecentRuns(await listRealBacktests(token, 5));
    } catch {
      /* none */
    }
  }

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function onAnalyze(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setMsg(null);
    setBusy("analyze");
    setRun(null);
    setChart(null);
    setMetrics(null);
    const text = prompt.trim();
    if (text.length < 10) {
      setError("Describe the strategy in at least 10 characters.");
      setBusy("");
      return;
    }
    try {
      const res: StrategyAnalysis = await analyzeStrategy(token, text);
      setAnalysis(res.analysis);
      setConverted(res.converted);
      setCacheHit(res.cache_hit);
      setSpecJson(JSON.stringify(res.strategy_spec ?? convertAnalysisToSpec(res.analysis), null, 2));
      setLastSavedSpecJson(null);
      setMsg(
        res.cache_hit
          ? "Returned from cache (identical text already analyzed)."
          : `Analyzed (source: ${res.provider_used}).`
      );
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "analysis failed");
    } finally {
      setBusy("");
    }
  }

  async function saveSpec(bodyName?: string) {
    if (!specValidJson) {
      setError("The strategy spec is not valid JSON. Fix the syntax before saving.");
      return null;
    }
    if (missing.length) {
      setError(`Cannot save yet — ${missing[0].toLowerCase()}.`);
      return null;
    }
    if (invalid) {
      setError("The analyzer flagged this description as unsafe (martingale/grid/no stop-loss). Edit the text and re-analyze instead of saving.");
      return null;
    }
    if (!savedStrategyId) {
      const created = await saveStrategy(token, { name: bodyName || "AI Strategy", spec: specObj });
      setSavedStrategyId(created.id);
      setSavedVersion(created.current_version);
      setLastSavedSpecJson(specJson);
      setMsg(`Strategy "${created.name}" v${created.current_version} created.`);
      return created;
    }
    const v = await addStrategyVersion(token, savedStrategyId, { spec: specObj });
    setSavedVersion(v.version);
    setLastSavedSpecJson(specJson);
    setMsg(`Saved a new version v${v.version}.`);
    return { id: savedStrategyId, version: v.version };
  }

  async function onSaveStrategy(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setMsg(null);
    if (!analysis) {
      setError("Analyze a strategy first.");
      return;
    }
    setBusy("save");
    try {
      await saveSpec(analysis.name || "AI Strategy");
      setSavedStrategies(await getStrategies(token));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "save failed");
    } finally {
      setBusy("");
    }
  }

  async function onPreview(e: FormEvent) {
    e.preventDefault();
    setError(null);
    if (!savedStrategyId) {
      setError("Save the strategy first.");
      return;
    }
    try {
      const p = await realBacktestPreview(token, {
        connection_id: connId,
        strategy_id: savedStrategyId,
        provider_symbol: symbol,
        provider: "exness",
        timeout,
        start_time_utc: toIso(dateFrom),
        end_time_utc: toIso(dateTo),
      });
      setPreview(p);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "preview failed");
    }
  }

  async function onRun(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setMsg(null);
    if (!providerReady || !savedStrategyId) {
      setError("A connected provider and a saved strategy are required.");
      return;
    }
    setBusy("run");
    try {
      const created = await createRealBacktest(token, {
        idempotency_key: `aib-${Date.now()}`,
        connection_id: connId,
        strategy_id: savedStrategyId,
        provider_symbol: symbol,
        canonical_symbol: symbol,
        provider: "exness",
        timeout,
        start_time_utc: toIso(dateFrom),
        end_time_utc: toIso(dateTo),
        cost: {
          spread_model: "provider_bid_ask",
          commission_model: "fixed_per_lot",
          commission_per_lot: commission,
          slippage_model: "fixed_adverse",
          fixed_slippage_pips: slippagePips,
          swap_enabled: swapOn,
          swap_points_per_night: swapPoints,
          account_currency: "USD",
          starting_balance: balance,
          execution_model: execModel,
        },
      });
      setRun(created);
      await Promise.all([loadChartAndMetrics(created.id), refreshRuns()]);
      setMsg(`Backtest ${created.run_status} (${created.candle_count} candles).`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "backtest failed");
    } finally {
      setBusy("");
    }
  }

  async function loadChartAndMetrics(runId: string) {
    try {
      const c = await getRealBacktestChart(token, runId);
      setChart(c);
    } catch {
      /* chart optional */
    }
    try {
      const m = await getRealBacktestMetrics(token, runId);
      setMetrics(m);
    } catch {
      /* metrics optional */
    }
  }

  async function refreshRuns() {
    try {
      setRecentRuns(await listRealBacktests(token, 5));
    } catch {
      /* ignore */
    }
  }

  async function selectRun(runId: string) {
    setBusy("select");
    setError(null);
    try {
      const r = await getRealBacktest(token, runId);
      setRun(r);
      await loadChartAndMetrics(runId);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "load failed");
    } finally {
      setBusy("");
    }
  }

  const optionalPairs = useMemo(() => {
    const extra = analysis?.recommended_symbols?.length
      ? analysis.recommended_symbols.slice(0, 6)
      : ["EURUSD", "GBPUSD", "USDJPY", "USDCAD"];
    return Array.from(new Set([...instruments.map((i) => i.canonical_symbol), ...extra]));
  }, [instruments, analysis]);

  return (
    <div className="space-y-6">
      <SectionTitle>AI Strategy Tester</SectionTitle>
      <p className="text-sm text-text-dim -mt-3 mb-2">
        Describe a strategy in plain English. The analyzer converts it into strict testable rules once
        (cached by text), you review/edit the spec, then run a deterministic backtest on real historical data.
      </p>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        {/* Panel 1: strategy input */}
        <Card title="1 · Strategy input">
          <form onSubmit={onAnalyze} className="space-y-3">
            <textarea
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              rows={7}
              className={`${INPUT} font-mono resize-y`}
              placeholder="Describe your entry/exit rules, symbols, timeframe, session, stop-loss and risk."
              maxLength={4000}
            />
            <div className="flex items-center justify-between text-xs text-text-dim">
              <button
                type="submit"
                disabled={busy === "analyze" || prompt.trim().length < 10}
                className="rounded-lg bg-accent px-3 py-2 text-sm font-medium text-bg disabled:opacity-50"
              >
                {busy === "analyze" ? "Analyzing…" : cacheHit ? "Re-analyze" : "Analyze strategy"}
              </button>
              <span>
                {prompt.length}/4000{cacheHit && <Badge label="cached" tone="warn" />}
              </span>
            </div>
            {analysis && (
              <div className="text-xs text-text-dim border-t border-border pt-2 space-y-1">
                <div className="flex flex-wrap gap-2">
                  <Badge
                    label={analysis.testability_status}
                    tone={analysis.testability_status === "VALID" ? "good" : analysis.testability_status === "INVALID" ? "bad" : "warn"}
                  />
                  <Badge label={converted ? "converted to DSL" : "manual spec needed"} tone={converted ? "good" : "warn"} />
                  <Badge label={analysis.strategy_family} tone="default" />
                  <Badge label={analysis.timeframe} tone="accent" />
                </div>
                {analysis.name && <p className="font-medium text-text">{analysis.name}</p>}
                {analysis.description && <p>{analysis.description}</p>}
                {analysis.warnings.length > 0 && (
                  <ul className="text-warn space-y-0.5">
                    {analysis.warnings.map((w, i) => (
                      <li key={i}>· {w}</li>
                    ))}
                  </ul>
                )}
              </div>
            )}
          </form>
        </Card>

        {/* Panel 2: strategy spec review/edit */}
        <Card title="2 · Strategy spec (review & edit)">
          {!analysis ? (
            <p className="text-sm text-text-dim">No analysis yet. Run the analyzer first.</p>
          ) : (
            <div className="space-y-3">
              {invalid && (
                <p className="text-xs text-danger">
                  The analyzer flagged this description as unsafe (martingale, grid, or no stop-loss). Edit the text
                  and re-analyze — this spec will not be saved.
                </p>
              )}
              {!converted && !invalid && (
                <p className="text-xs text-warn">
                  The analyzer could not fully convert this description. Complete the missing fields below using the
                  allowed DSL, then save.
                </p>
              )}
              <textarea
                value={specJson}
                onChange={(e) => setSpecJson(e.target.value)}
                rows={12}
                className={`${INPUT} font-mono text-xs resize-y`}
                spellCheck={false}
                aria-label="Strategy spec JSON"
              />
              <div className="text-xs space-y-1">
                <p className={specValidJson ? "text-text-dim" : "text-danger"}>
                  {specValidJson ? "Valid JSON" : "Invalid JSON — fix the syntax before saving"}
                </p>
                {specValidJson && missing.length > 0 && (
                  <ul className="text-warn space-y-0.5">
                    {missing.map((m, i) => (
                      <li key={i}>· {m}</li>
                    ))}
                  </ul>
                )}
                {specValidJson && missing.length === 0 && <p className="text-accent">Ready to save — all required fields present.</p>}
                {!converted && (
                  <p className="text-text-dim mt-1">
                    Rule expressions use the allow-list: {DSL_ALLOWED.join(" ")}.
                  </p>
                )}
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <button
                  type="button"
                  onClick={onSaveStrategy}
                  disabled={busy === "save" || invalid || !specValidJson || missing.length > 0}
                  className="rounded-lg bg-accent px-3 py-2 text-sm font-medium text-bg disabled:opacity-40"
                >
                  {busy === "save"
                    ? "Saving…"
                    : savedStrategyId
                    ? `Save new version${analysis.name ? ` · ${analysis.name}` : ""}`
                    : "Save as strategy"}
                </button>
              </div>
              {savedStrategyId && (
                <div className="text-xs flex items-center gap-2">
                  <Badge label={`v${savedVersion || "?"}`} tone="good" />
                  <span className="text-text-dim">saved strategy ·</span>
                  {specDirty ? (
                    <span className="text-warn">unsaved edits — save a new version</span>
                  ) : (
                    <span className="text-text-dim">spec matches saved version</span>
                  )}
                </div>
              )}
              {analysis.testability_status === "NEEDS_USER_INPUT" && !invalid && (
                <details className="text-xs">
                  <summary className="text-text-dim cursor-pointer">Why it needs input · assumptions & failures</summary>
                  <ul className="mt-2 space-y-1 text-text-dim">
                    {analysis.assumptions.map((a, i) => (
                      <li key={i}>· assume: {a}</li>
                    ))}
                    {analysis.failure_conditions.map((f, i) => (
                      <li key={i}>· watch: {f}</li>
                    ))}
                  </ul>
                </details>
              )}
            </div>
          )}
        </Card>

        {/* Panel 3: real-data backtest settings */}
        <Card title="3 · Real-data backtest">
          <form onSubmit={onRun} className="space-y-3">
            <ProviderConnectionStatusCard card={card} />
            {!providerReady && (
              <p className="text-xs text-warn">
                A connected provider is required — real data is never silently replaced with mock data.
              </p>
            )}

            <div className="grid grid-cols-2 gap-2">
              <label className="text-xs text-text-dim">
                Symbol
                <select value={symbol} onChange={(e) => setSymbol(e.target.value)} className={INPUT}>
                  {optionalPairs.map((s) => (
                    <option key={s} value={s}>
                      {s}
                    </option>
                  ))}
                </select>
              </label>
              <label className="text-xs text-text-dim">
                Timeframe
                <select value={timeout} onChange={(e) => setTimeoutTf(e.target.value)} className={INPUT}>
                  {ALLOWED_TIMEFRAMES.map((tf) => (
                    <option key={tf} value={tf}>
                      {tf}
                    </option>
                  ))}
                </select>
              </label>
            </div>

            <div className="grid grid-cols-2 gap-2">
              <label className="text-xs text-text-dim">
                From
                <input type="date" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} className={INPUT} />
              </label>
              <label className="text-xs text-text-dim">
                To
                <input type="date" value={dateTo} onChange={(e) => setDateTo(e.target.value)} className={INPUT} />
              </label>
            </div>

            <div className="grid grid-cols-2 gap-2">
              <label className="text-xs text-text-dim">
                Starting balance
                <input type="number" value={balance} onChange={(e) => setBalance(Number(e.target.value))} className={INPUT} />
              </label>
              <label className="text-xs text-text-dim">
                Execution model
                <select value={execModel} onChange={(e) => setExecModel(e.target.value)} className={INPUT}>
                  {EXEC_MODELS.map((m) => (
                    <option key={m} value={m}>
                      {m}
                    </option>
                  ))}
                </select>
              </label>
            </div>

            <div className="grid grid-cols-3 gap-2">
              <label className="text-xs text-text-dim">
                Commission/ lot
                <input type="number" step="0.1" value={commission} onChange={(e) => setCommission(Number(e.target.value))} className={INPUT} />
              </label>
              <label className="text-xs text-text-dim">
                Slippage pips
                <input type="number" step="0.1" value={slippagePips} onChange={(e) => setSlippagePips(Number(e.target.value))} className={INPUT} />
              </label>
              <label className="text-xs text-text-dim">
                Swap pts/night
                <input type="number" step="0.1" value={swapPoints} onChange={(e) => setSwapPoints(Number(e.target.value))} className={INPUT} />
              </label>
            </div>

            <label className="flex items-center gap-2 text-xs text-text-dim">
              <input type="checkbox" checked={swapOn} onChange={(e) => setSwapOn(e.target.checked)} />
              Apply swap
            </label>

            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={onPreview}
                disabled={!savedStrategyId || busy === "preview"}
                className="rounded-lg border border-border px-3 py-2 text-sm text-text-dim hover:text-text disabled:opacity-40"
              >
                {busy === "preview" ? "Previewing…" : "Preview"}
              </button>
              <button
                type="submit"
                disabled={!runReady}
                className="rounded-lg bg-accent px-3 py-2 text-sm font-medium text-bg disabled:opacity-40"
              >
                {busy === "run" ? "Running…" : "Run backtest"}
              </button>
            </div>
            {preview && (
              <div className="text-xs space-y-1">
                <div className="flex flex-wrap gap-2">
                  <Badge label={`provider: ${preview.provider_status}`} tone={preview.provider_status === "CONNECTED" ? "good" : "warn"} />
                  <Badge label={`symbol: ${preview.symbol_mapping_status}`} tone="default" />
                  <Badge label={`~${preview.estimated_candles} candles`} tone="default" />
                </div>
                {preview.incompatibilities.length > 0 && (
                  <ul className="text-warn">
                    {preview.incompatibilities.map((inc, i) => (
                      <li key={i}>· {inc}</li>
                    ))}
                  </ul>
                )}
              </div>
            )}
          </form>
        </Card>
      </div>

      {(error || msg) && (
        <div
          className={`rounded-lg border px-4 py-2 text-sm ${
            error ? "border-danger/30 text-danger" : "border-border text-text-dim"
          }`}
        >
          {error || msg}
        </div>
      )}

      {/* Results */}
      {run && (
        <Card
          title={`Results · ${run.canonical_symbol} ${run.timeout} · ${run.run_status}${run.strategy_version ? ` · v${run.strategy_version}` : ""}`}
          className="!p-0"
        >
          <div className="p-4 space-y-4">
            <div className="flex flex-wrap items-center gap-2">
              <Badge label={run.run_status} tone={statusTone(run.run_status)} />
              <Badge label={`data: ${run.source_data_type}`} tone="default" />
              <Badge label={`exec: ${run.execution_model}`} tone="accent" />
              <Badge label={`~${run.candle_count} candles`} tone="default" />
              {run.data_quality_score != null && (
                <Badge label={`quality ${run.data_quality_score.toFixed(2)}`} tone="good" />
              )}
            </div>
            {run.warnings && run.warnings.length > 0 && (
              <ul className="text-xs text-warn space-y-1">
                {run.warnings.map((w, i) => (
                  <li key={i}>· {w}</li>
                ))}
              </ul>
            )}
            {run.error_safe && <p className="text-xs text-danger">{run.error_safe}</p>}
          </div>

          {chart && chart.candles.length > 0 && (
            <div className="px-4 pb-4">
              <CandlestickChart
                candles={toCandles(chart.candles)}
                markers={markers}
                overlays={overlays}
                gaps={chart.gaps}
                height={340}
              />
              <p className="text-[11px] text-text-dim mt-1">
                Triangles = entries/exits · crosses = stop/target · shaded bands = data gaps · lines =
                strategy indicator overlays.
              </p>
            </div>
          )}
        </Card>
      )}

      {metrics && (
        <div>
          <h3 className="text-sm font-medium mb-2 text-text-dim uppercase tracking-wide">Metrics</h3>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            {Object.entries(metrics.metrics || {})
              .slice(0, 12)
              .map(([k, v]) => (
                <Stat key={k} label={k} value={typeof v === "number" ? v.toFixed(2) : String(v)} tone={metricTone(k, Number(v) || 0)} />
              ))}
          </div>
        </div>
      )}

      {chart && chart.trades.length > 0 && (
        <Card title={`Trades · ${chart.trades.length}`}>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead className="text-left text-text-dim">
                <tr>
                  <th className="py-1.5 pr-2">Side</th>
                  <th className="py-1.5 pr-2">Entry</th>
                  <th className="py-1.5 pr-2">Exit</th>
                  <th className="py-1.5 pr-2 text-right">Entry px</th>
                  <th className="py-1.5 pr-2 text-right">Stop</th>
                  <th className="py-1.5 pr-2 text-right">Target</th>
                  <th className="py-1.5 pr-2 text-right">Net P&L</th>
                  <th className="py-1.5 pr-2 text-right">Pips</th>
                  <th className="py-1.5 pr-2">Exit reason</th>
                </tr>
              </thead>
              <tbody>
                {chart.trades.map((t) => (
                  <tr key={t.id} className="border-t border-border">
                    <td className={`py-1.5 pr-2 capitalize ${t.side === "long" ? "text-accent" : "text-danger"}`}>{t.side}</td>
                    <td className="py-1.5 pr-2">{fmtTs(t.entry_ts)}</td>
                    <td className="py-1.5 pr-2">{fmtTs(t.exit_ts)}</td>
                    <td className="py-1.5 pr-2 text-right">{t.entry_price}</td>
                    <td className="py-1.5 pr-2 text-right">{t.stop ?? "—"}</td>
                    <td className="py-1.5 pr-2 text-right">{t.target ?? "—"}</td>
                    <td className={`py-1.5 pr-2 text-right ${t.net_pnl >= 0 ? "text-accent" : "text-danger"}`}>{t.net_pnl.toFixed(2)}</td>
                    <td className="py-1.5 pr-2 text-right">{t.pips?.toFixed(1)}</td>
                    <td className="py-1.5 pr-2">{t.exit_reason ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}

      {recentRuns.length > 0 && (
        <Card title="Recent backtests">
          <div className="space-y-2">
            {recentRuns.map((r) => (
              <button
                key={r.id}
                type="button"
                onClick={() => selectRun(r.id)}
                className="w-full text-left rounded-lg border border-border px-3 py-2 text-sm hover:border-accent"
              >
                <div className="flex flex-wrap items-center gap-2">
                  <Badge label={r.run_status} tone={statusTone(r.run_status)} />
                  <span className="text-text">
                    {r.canonical_symbol} {r.timeout}
                  </span>
                  <span className="text-text-dim text-xs">{r.candle_count} candles</span>
                  {busy === "select" && <span className="text-text-dim text-xs animate-pulse">loading…</span>}
                </div>
              </button>
            ))}
          </div>
        </Card>
      )}

      {/* Data-source honesty */}
      {!providerReady && (
        <p className="text-xs text-text-dim">
          Data source status: {card ? card.connection_status : "NO PROVIDER CONNECTED"} — backtesting requires a
          connected, health-checked provider. No silent fallback to mock data is performed.
        </p>
      )}
    </div>
  );
}

// Fallback used only when the analyzer could not return a converted spec (e.g.
// NEEDS_USER_INPUT): builds a shape-safe skeleton from the reviewed analysis.
// The user completes the missing strict fields before saving. This module never
// executes any user/AI-provided code.
function convertAnalysisToSpec(analysis: AIStrategyAnalysis): Record<string, unknown> {
  const session = analysis.sessions_utc?.[0] || { name: "Full", start: "00:00", end: "23:59" };
  const family = FAMILY_MAP[analysis.strategy_family] ?? "trend_pullback";
  const rules =
    analysis.entry_rules?.map((r, i) => ({
      id: `${r.side}_rule_${i + 1}`,
      description: r.rule,
      expression: "",
    })) || [];
  return {
    name: analysis.name || "AI Strategy",
    version: "1.0.0",
    strategy_family: family,
    supported_pairs: analysis.recommended_symbols?.length ? analysis.recommended_symbols.slice(0, 6) : [],
    supported_timeframes: analysis.timeframe ? [analysis.timeframe] : [],
    sessions_utc: [session],
    market_regime: { preferred: [], avoid: [] },
    indicators: analysis.indicators || [],
    entry_rules: rules,
    exit_rules:
      analysis.exit_rules?.map((r, i) => ({ id: `exit_rule_${i + 1}`, description: r.rule, expression: "" })) || [],
    risk_management: {
      risk_per_trade_pct: analysis.risk_rules?.risk_per_trade_pct ?? 0.25,
      max_daily_loss_pct: analysis.risk_rules?.max_daily_loss_pct ?? 1.0,
      max_consecutive_losses: 3,
      max_open_positions: 1,
      max_trades_per_day: analysis.risk_rules?.max_trades_per_day ?? 5,
      stop_loss_method: analysis.stop_loss?.type === "FIXED" || analysis.stop_loss?.type === "STRUCTURE" ? analysis.stop_loss?.type : "ATR",
      stop_loss_parameters: {
        atr_period: analysis.stop_loss?.atr_period ?? 14,
        atr_multiplier: analysis.stop_loss?.multiplier ?? 1.2,
      },
      take_profit_method: analysis.take_profit?.type === "RISK_REWARD" ? "risk_reward" : (analysis.take_profit?.type ?? "risk_reward"),
      take_profit_parameters: { risk_reward_ratio: analysis.take_profit?.ratio ?? 1.5 },
    },
    execution_filters: {
      max_spread_pips: analysis.risk_rules?.max_spread_pips ?? 1.2,
      max_slippage_pips: 0.5,
      minimum_atr_pips: 3.0,
      news_blackout_minutes_before: 15,
      news_blackout_minutes_after: 15,
    },
    assumptions: analysis.assumptions || [],
    failure_modes: analysis.failure_conditions || [],
    plain_english_explanation: analysis.description || "",
    confidence_notes: "AI Strategy Tester: complete the strict spec fields before saving — the analyzer could not fully convert this description.",
  };
}