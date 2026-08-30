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
  const [strategySpec, setStrategySpec] = useState<unknown | null>(null);
  const [cacheHit, setCacheHit] = useState(false);
  const [editableJson, setEditableJson] = useState("");
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

  // markers/overlays memoized once chart is loaded
  const markers = useMemo(() => (chart ? toMarkers(chart) : []), [chart]);
  const overlays = useMemo(() => (chart ? toOverlays(chart) : []), [chart]);

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
    try {
      const res: StrategyAnalysis = await analyzeStrategy(token, prompt.trim());
      setAnalysis(res.analysis);
      setStrategySpec(res.converted ? res.strategy_spec : null);
      setCacheHit(res.cache_hit);
      setEditableJson(JSON.stringify(res.analysis, null, 2));
      setMsg(
        res.cache_hit
          ? "Returned from cache (identical text was already analyzed)."
          : `Analyzed (source: ${res.provider_used}).`
      );
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "analysis failed");
    } finally {
      setBusy("");
    }
  }

  function applyJson() {
    setError(null);
    try {
      setAnalysis(JSON.parse(editableJson) as AIStrategyAnalysis);
      setMsg("Reviewed/edited AI rules applied. Save them as a strategy version before running.");
    } catch {
      setError("The edited JSON is not valid. Check the syntax before saving.");
    }
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
      if (!savedStrategyId) {
        const created = await saveStrategy(token, {
          name: analysis.name || "AI Strategy",
          spec: strategySpec ?? convertAnalysisToSpec(analysis),
        });
        setSavedStrategyId(created.id);
        setSavedVersion(created.current_version);
        setMsg(`Strategy "${created.name}" v${created.current_version} created.`);
      } else {
        const v = await addStrategyVersion(token, savedStrategyId, {
          spec: strategySpec ?? convertAnalysisToSpec(analysis),
        });
        setSavedVersion(v.version);
        setMsg(`Saved a new version v${v.version}.`);
      }
      setSavedStrategies(await getStrategies(token));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "save failed");
    } finally {
      setBusy("");
    }
  }

  async function onNewVersion(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setMsg(null);
    if (!analysis || !savedStrategyId) return;
    setBusy("version");
    try {
      const v = await addStrategyVersion(token, savedStrategyId, {
        spec: strategySpec ?? convertAnalysisToSpec(analysis),
      });
      setSavedVersion(v.version);
      setMsg(`Saved v${v.version} for this strategy.`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "version save failed");
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
      const res = await fetch(`${process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api"}/real-backtests?limit=5`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (res.ok) setRecentRuns((await res.json()) as ValidationRun[]);
    } catch {
      /* ignore */
    }
  }

  async function selectRun(runId: string) {
    setBusy("run");
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

  return (
    <div className="space-y-6">
      <SectionTitle>AI Strategy Tester</SectionTitle>
      <p className="text-sm text-text-dim -mt-3 mb-2">
        Describe a strategy in plain English. The analyzer converts it into strict testable rules once
        (cached by text), you review/edit them, then run a deterministic backtest on real historical data.
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
            <div className="flex items-center gap-2">
              <button
                type="submit"
                disabled={busy === "analyze"}
                className="rounded-lg bg-accent px-3 py-2 text-sm font-medium text-bg disabled:opacity-50"
              >
                {busy === "analyze" ? "Analyzing…" : cacheHit ? "Re-analyze" : "Analyze strategy"}
              </button>
              {cacheHit && <Badge label="cached" tone="warn" />}
            </div>
          </form>
        </Card>

        {/* Panel 2: AI rules review/edit */}
        <Card title="2 · AI rules (review & edit)">
          {!analysis ? (
            <p className="text-sm text-text-dim">No analysis yet. Run the analyzer first.</p>
          ) : (
            <div className="space-y-3">
              <div className="flex flex-wrap items-center gap-2">
                <Badge
                  label={analysis.testability_status}
                  tone={
                    analysis.testability_status === "VALID"
                      ? "good"
                      : analysis.testability_status === "INVALID"
                      ? "bad"
                      : "warn"
                  }
                />
                <Badge label={analysis.strategy_family} tone="default" />
                <Badge label={analysis.timeframe} tone="accent" />
              </div>
              {analysis.warnings.length > 0 && (
                <ul className="text-xs text-warn space-y-1">
                  {analysis.warnings.map((w, i) => (
                    <li key={i}>· {w}</li>
                  ))}
                </ul>
              )}
              <textarea
                value={editableJson}
                onChange={(e) => setEditableJson(e.target.value)}
                rows={9}
                className={`${INPUT} font-mono text-xs resize-y`}
                spellCheck={false}
              />
              <div className="flex flex-wrap items-center gap-2">
                <button
                  type="button"
                  onClick={applyJson}
                  className="rounded-lg border border-border px-3 py-1.5 text-xs text-text-dim hover:text-text"
                >
                  Apply edits
                </button>
                <button
                  type="button"
                  onClick={onSaveStrategy}
                  disabled={busy === "save"}
                  className="rounded-lg bg-accent px-3 py-2 text-sm font-medium text-bg disabled:opacity-50"
                >
                  {busy === "save"
                    ? "Saving…"
                    : savedStrategyId
                    ? `Save new version ${analysis.name}`
                    : "Save as strategy"}
                </button>
              </div>
              {savedStrategyId && (
                <div className="flex items-center gap-2 text-xs">
                  <Badge label={`v${savedVersion || "?"}`} tone="good" />
                  <span className="text-text-dim">saved strategy</span>
                </div>
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
                <select value={symbol} onChange={(e) => setSymbol(e.target.value)} className={INPUT} disabled={!instruments.length}>
                  {instruments.length
                    ? instruments.map((i) => (
                        <option key={i.provider_symbol} value={i.canonical_symbol}>
                          {i.canonical_symbol} (mapped)
                        </option>
                      ))
                    : ["EURUSD", "GBPUSD", "USDJPY", "USDCAD"].map((s) => (
                        <option key={s} value={s}>
                          {s}
                        </option>
                      ))}
                </select>
              </label>
              <label className="text-xs text-text-dim">
                Timeframe
                <select value={timeout} onChange={(e) => setTimeoutTf(e.target.value)} className={INPUT}>
                  {["M1", "M5", "M15", "M30", "H1", "H4", "D1"].map((tf) => (
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
                disabled={!savedStrategyId}
                className="rounded-lg border border-border px-3 py-2 text-sm text-text-dim hover:text-text disabled:opacity-40"
              >
                Preview
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
          title={`Results · ${run.provider_symbol} ${run.timeout} · ${run.run_status}`}
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
                  <span className="text-text">{r.canonical_symbol} {r.timeout}</span>
                  <span className="text-text-dim text-xs">{r.candle_count} candles</span>
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

// The analyzer endpoint returns an already-converted, DSL-valid strategy spec
// when the analysis was testable; otherwise we build the request body from the
// reviewed analysis fields (the backend persists it through the safe spec
// schema). This module never executes any user/AI-provided code.
function convertAnalysisToSpec(analysis: AIStrategyAnalysis): Record<string, unknown> {
  const session = analysis.sessions_utc?.[0] || { name: "Full", start: "00:00", end: "23:59" };
  const rules =
    analysis.entry_rules?.map((r, i) => ({
      id: `${r.side}_rule_${i + 1}`,
      description: r.rule,
      expression: "",
    })) || [];
  const spec: Record<string, unknown> = {
    name: analysis.name || "AI Strategy",
    version: "1.0.0",
    strategy_family: analysis.strategy_family,
    supported_pairs: analysis.recommended_symbols?.length ? analysis.recommended_symbols.slice(0, 6) : ["EURUSD"],
    supported_timeframes: [analysis.timeframe],
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
    confidence_notes: "AI Strategy Tester: review expressions before saving if the analyzer could not convert.",
  };
  return spec;
}