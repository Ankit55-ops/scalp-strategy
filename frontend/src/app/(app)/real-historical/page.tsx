"use client";

import Link from "next/link";
import { FormEvent, useEffect, useMemo, useState } from "react";
import {
  ApiError,
  cancelValidationRun,
  createValidationRun,
  exportValidationRun,
  getExnessInstruments,
  getExnessStatusCard,
  getStrategies,
  getValidationCandles,
  getValidationEquity,
  getValidationMetrics,
  getValidationQuality,
  getValidationRun,
  getValidationSignals,
  getValidationTrades,
  listValidationRuns,
  previewValidation,
  tokenStore,
} from "@/lib/api";
import { Badge, Card, SectionTitle, Stat } from "@/components/ui";
import { ProviderConnectionStatusCard } from "@/components/ProviderConnectionStatusCard";
import CandlestickChart, { ChartMarker } from "@/components/chart/CandlestickChart";
import EquityChart from "@/components/EquityChart";
import type {
  CandleView,
  InstrumentMappingView,
  ProviderConnectionStatusCard as CardData,
  Strategy,
  ValidationMetrics,
  ValidationPreview,
  ValidationQuality,
  ValidationRun,
  ValidationSignal,
  ValidationTrade,
} from "@/types";

const INPUT =
  "w-full rounded-lg border border-border bg-bg px-3 py-2 text-sm outline-none focus:border-accent";

const EXEC_MODELS = [
  "BID_ASK_HISTORICAL_WHERE_AVAILABLE",
  "NEXT_CANDLE_OPEN",
  "SIGNAL_PRICE",
  "ESTIMATED_SPREAD_FROM_MID",
];

function toSecs(dateVal: string): number {
  return new Date(`${dateVal}T00:00:00Z`).getTime() / 1000;
}

function toIso(dateVal: string): string {
  return new Date(`${dateVal}T00:00:00Z`).toISOString();
}

function fmtTs(ts: number): string {
  return new Date(ts * 1000).toISOString().slice(0, 16).replace("T", " ");
}

function runTone(status: string): "good" | "bad" | "warn" | "default" {
  if (status === "COMPLETED") return "good";
  if (status === "FAILED") return "bad";
  if (status === "RUNNING" || status === "QUEUED") return "warn";
  return "default";
}

export default function RealHistoricalPage() {
  const [card, setCard] = useState<CardData | null>(null);
  const [instruments, setInstruments] = useState<InstrumentMappingView[]>([]);
  const [strategies, setStrategies] = useState<Strategy[]>([]);

  const token = tokenStore.get() as string;

  // run list
  const [runs, setRuns] = useState<ValidationRun[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);

  // config form
  const [strategyId, setStrategyId] = useState("");
  const [symbol, setSymbol] = useState("EURUSD");
  const [timeout, setTimeoutTf] = useState("M5");
  const [dateFrom, setDateFrom] = useState("2026-01-01");
  const [dateTo, setDateTo] = useState("2026-04-01");
  const [balance, setBalance] = useState(100000);
  const [execModel, setExecModel] = useState(EXEC_MODELS[0]);
  const [commission, setCommission] = useState(2);
  const [slippagePips, setSlippagePips] = useState(0.3);
  const [spreadModel, setSpreadModel] = useState("provider_bid_ask");
  const [swapOn, setSwapOn] = useState(true);
  const [swapPoints, setSwapPoints] = useState(0.2);

  const [preview, setPreview] = useState<ValidationPreview | null>(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);

  // selected run results
  const [run, setRun] = useState<ValidationRun | null>(null);
  const [metrics, setMetrics] = useState<ValidationMetrics | null>(null);
  const [quality, setQuality] = useState<ValidationQuality | null>(null);
  const [trades, setTrades] = useState<ValidationTrade[]>([]);
  const [signals, setSignals] = useState<ValidationSignal[]>([]);
  const [equity, setEquity] = useState<{ ts: number; balance: number }[]>([]);
  const [chartCandles, setChartCandles] = useState<CandleView[]>([]);

  const connId = instruments[0]?.connection_id ?? "";

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
      setStrategies(await getStrategies(token));
    } catch {
      /* none */
    }
    try {
      setRuns(await listValidationRuns(token, 20));
    } catch {
      /* none */
    }
  }

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // runs refresh
  async function refreshRuns() {
    try {
      setRuns(await listValidationRuns(token, 20));
    } catch {
      /* ignore */
    }
  }

  async function onPreview(e: FormEvent) {
    e.preventDefault();
    setError(null);
    if (card?.connection_status !== "CONNECTED") {
      setError("Connect an Exness/MT5 provider first (Settings → Data providers).");
      return;
    }
    setBusy("preview");
    try {
      const p = await previewValidation(token, {
        connection_id: connId,
        strategy_id: strategyId,
        provider_symbol: symbol,
        canonical_symbol: symbol,
        timeout,
        start_time_utc: toIso(dateFrom),
        end_time_utc: toIso(dateTo),
      });
      setPreview(p);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "preview failed");
    } finally {
      setBusy("");
    }
  }

  async function onRun(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setMsg(null);
    if (card?.connection_status !== "CONNECTED") {
      setError("Connect an Exness/MT5 provider first.");
      return;
    }
    setBusy("run");
    try {
      const created = await createValidationRun(token, {
        idempotency_key: `rhv-${Date.now()}`,
        connection_id: connId,
        strategy_id: strategyId,
        provider_symbol: symbol,
        canonical_symbol: symbol,
        timeout,
        start_time_utc: toIso(dateFrom),
        end_time_utc: toIso(dateTo),
        execution_model: execModel,
        cost: {
          spread_model: spreadModel,
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
      setMsg(`Run created: ${created.run_status}.`);
      await Promise.all([refreshRuns(), selectRun(created.id)]);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "run failed");
    } finally {
      setBusy("");
    }
  }

  // eslint-disable-next-line react-hooks/exhaustive-deps
  async function selectRun(id: string) {
    setSelectedId(id);
    setRun(null);
    setMetrics(null);
    setQuality(null);
    setTrades([]);
    setSignals([]);
    setEquity([]);
    setChartCandles([]);
    try {
      const r = await getValidationRun(token, id);
      setRun(r);
      const [m, q, t, s, eq, cv] = await Promise.all([
        getValidationMetrics(token, id).catch(() => null),
        getValidationQuality(token, id).catch(() => null),
        getValidationTrades(token, id).catch(() => []),
        getValidationSignals(token, id).catch(() => []),
        getValidationEquity(token, id).catch(() => ({ equity_curve: [] })),
        getValidationCandles(token, id, 800).catch(() => ({ candles: [] })),
      ]);
      setMetrics(m);
      setQuality(q);
      setTrades(t);
      setSignals(s);
      setEquity(eq.equity_curve ?? eq.equity_curve);
      setChartCandles(cv.candles ?? []);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "load run failed");
    }
  }

  async function onExport() {
    if (!selectedId) return;
    setBusy("export");
    setError(null);
    try {
      const data = await exportValidationRun(token, selectedId, { format: "json" });
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = `validation-${selectedId.slice(0, 8)}.json`;
      a.click();
      URL.revokeObjectURL(a.href);
      setMsg("Export downloaded (credentials redacted server-side).");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "export failed");
    } finally {
      setBusy("");
    }
  }

  async function onCancel() {
    if (!selectedId) return;
    setBusy("cancel");
    try {
      await cancelValidationRun(token, selectedId);
      setMsg("Run cancelled.");
      await refreshRuns();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "cancel failed");
    } finally {
      setBusy("");
    }
  }

  const markers = useMemo<ChartMarker[]>(() => {
    const rows: ChartMarker[] = [];
    for (const t of trades.slice(0, 600)) {
      rows.push({ ts: t.entry_ts, price: t.entry_price, type: "entry", side: t.side as never });
      if (t.exit_ts && t.exit_price) rows.push({ ts: t.exit_ts, price: t.exit_price, type: "exit", side: t.side as never });
      if (t.stop) rows.push({ ts: t.entry_ts, price: t.stop, type: "stop" });
      if (t.target) rows.push({ ts: t.entry_ts, price: t.target, type: "target" });
    }
    return rows;
  }, [trades]);

  const m = metrics?.metrics ?? {};
  const net = Number(m.net_profit ?? 0);

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <SectionTitle>Backtest Lab · Real Historical Data</SectionTitle>
        <Link href="/backtests" className="text-sm text-accent hover:underline">
          ← Backtests
        </Link>
      </div>

      <ProviderConnectionStatusCard
        card={card}
        onConnect={() => (window.location.href = "/settings/providers")}
      />

      <div className="grid lg:grid-cols-5 gap-4 mt-4">
        <Card title="Validate against real historical data" className="lg:col-span-3">
          <form onSubmit={onRun} className="space-y-3 text-sm">
            <div className="grid grid-cols-2 gap-3">
              <label className="col-span-2">
                <span className="text-xs text-text-dim block mb-1">Strategy</span>
                <select value={strategyId} onChange={(e) => setStrategyId(e.target.value)} required className={INPUT}>
                  <option value="">Select…</option>
                  {strategies.map((s) => (
                    <option key={s.id} value={s.id}>
                      {s.name}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                <span className="text-xs text-text-dim block mb-1">Provider symbol</span>
                <select value={symbol} onChange={(e) => setSymbol(e.target.value)} className={INPUT}>
                  {(instruments.length ? instruments.map((i) => i.provider_symbol) : ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD"]).map(
                    (s) => (
                      <option key={s} value={s}>
                        {s}
                      </option>
                    )
                  )}
                </select>
              </label>
              <label>
                <span className="text-xs text-text-dim block mb-1">Timeframe</span>
                <select value={timeout} onChange={(e) => setTimeoutTf(e.target.value)} className={INPUT}>
                  <option value="M5">M5</option>
                  <option value="M15">M15</option>
                  <option value="H1">H1</option>
                  <option value="H4">H4</option>
                </select>
              </label>
              <label>
                <span className="text-xs text-text-dim block mb-1">From</span>
                <input type="date" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} className={INPUT} />
              </label>
              <label>
                <span className="text-xs text-text-dim block mb-1">To</span>
                <input type="date" value={dateTo} onChange={(e) => setDateTo(e.target.value)} className={INPUT} />
              </label>
              <label>
                <span className="text-xs text-text-dim block mb-1">Starting balance</span>
                <input type="number" value={balance} onChange={(e) => setBalance(Number(e.target.value))} className={INPUT} />
              </label>
              <label>
                <span className="text-xs text-text-dim block mb-1">Execution model</span>
                <select value={execModel} onChange={(e) => setExecModel(e.target.value)} className={INPUT}>
                  {EXEC_MODELS.map((x) => (
                    <option key={x} value={x}>
                      {x.replace(/_/g, " ")}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                <span className="text-xs text-text-dim block mb-1">Commission per lot</span>
                <input type="number" step="0.1" value={commission} onChange={(e) => setCommission(Number(e.target.value))} className={INPUT} />
              </label>
              <label>
                <span className="text-xs text-text-dim block mb-1">Slippage pips</span>
                <input type="number" step="0.1" value={slippagePips} onChange={(e) => setSlippagePips(Number(e.target.value))} className={INPUT} />
              </label>
              <label>
                <span className="text-xs text-text-dim block mb-1">Swap points / night</span>
                <input type="number" step="0.1" value={swapPoints} onChange={(e) => setSwapPoints(Number(e.target.value))} className={INPUT} disabled={!swapOn} />
              </label>
              <label className="flex items-end gap-2 pb-2">
                <input type="checkbox" checked={swapOn} onChange={(e) => setSwapOn(e.target.checked)} className="accent-accent" />
                <span className="text-xs">Include swap</span>
              </label>
            </div>
            <div className="flex gap-2">
              <button
                type="button"
                onClick={onPreview}
                disabled={!!busy}
                className="rounded-lg border border-border px-4 py-2 font-medium disabled:opacity-50"
              >
                {busy === "preview" ? "Checking…" : "Preview coverage"}
              </button>
              <button
                disabled={!!busy}
                className="rounded-lg bg-accent px-4 py-2 font-medium text-bg disabled:opacity-50"
              >
                {busy === "run" ? "Validating…" : "Run validation"}
              </button>
            </div>
            {preview && (
              <div className="flex gap-2 flex-wrap items-center text-xs">
                <Badge label={`provider ${preview.provider_status}`} tone={preview.provider_status === "CONNECTED" ? "good" : "warn"} />
                <Badge label={`mapping ${preview.symbol_mapping_status}`} />
                <Badge label={`coverage ${preview.historical_coverage_status}`} />
                <Badge label={`~${preview.estimated_candles} candles · ${preview.required_warmup_candles} warmup`} tone="accent" />
                {preview.incompatibilities.length > 0 && (
                  <span className="text-danger">incompatibilities: {preview.incompatibilities.join(", ")}</span>
                )}
              </div>
            )}
            {msg && <p className="text-xs text-accent">{msg}</p>}
            {error && <p className="text-xs text-danger">{error}</p>}
          </form>
        </Card>

        <Card title={`Validation runs (${runs.length})`} className="lg:col-span-2">
          {runs.length === 0 ? (
            <p className="text-sm text-text-dim">No validation runs yet.</p>
          ) : (
            <div className="max-h-80 overflow-auto">
              <table className="w-full text-xs">
                <thead className="sticky top-0 bg-panel text-text-dim">
                  <tr className="text-left">
                    <th className="py-1.5 pr-2">Status</th>
                    <th className="py-1.5 pr-2">Symbol · TF</th>
                    <th className="py-1.5 pr-2">Candles</th>
                    <th className="py-1.5 pr-2">Quality</th>
                    <th className="py-1.5">Run</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  {runs.map((r) => (
                    <tr key={r.id} onClick={() => selectRun(r.id)} className="cursor-pointer hover:bg-panel2">
                      <td className="py-1.5 pr-2">
                        <Badge label={r.run_status} tone={runTone(r.run_status)} />
                      </td>
                      <td className="py-1.5 pr-2">
                        {r.provider_symbol} · {r.timeout}
                      </td>
                      <td className="py-1.5 pr-2">{r.candle_count}</td>
                      <td className="py-1.5 pr-2">
                        {r.data_quality_score != null ? `${(r.data_quality_score * 100).toFixed(0)}%` : "—"}
                      </td>
                      <td className="py-1.5 text-text-dim">{fmtTs(r.start_time_utc)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <p className="text-xs text-text-dim mt-3">
            Real historical candles come through your broker connection (server-side). Results are reproducible from
            the stored source hash.
          </p>
        </Card>
      </div>

      {run && (
        <div className="mt-4 space-y-4">
          <Card title={`Run results · ${run.provider_symbol} ${run.timeout} · ${run.run_status}`}>
            <div className="flex gap-2 items-center flex-wrap mb-3">
              <Badge label={run.run_status} tone={runTone(run.run_status)} />
              <Badge label={`data: ${run.source_data_type}`} tone="accent" />
              <Badge label={`exec: ${run.execution_model.replace(/_/g, " ")}`} />
              <Badge label={`${run.candle_count} candles · ${run.missing_candle_count} missing`} />
              <button
                onClick={onExport}
                disabled={!!busy}
                className="ml-auto rounded-lg border border-border px-3 py-1 text-xs disabled:opacity-50"
              >
                {busy === "export" ? "Exporting…" : "Export"}
              </button>
              {(run.run_status === "QUEUED" || run.run_status === "RUNNING") && (
                <button
                  onClick={onCancel}
                  disabled={!!busy}
                  className="rounded-lg border border-danger/40 text-danger px-3 py-1 text-xs disabled:opacity-50"
                >
                  Cancel
                </button>
              )}
              <button onClick={refreshRuns} className="rounded-lg border border-border px-3 py-1 text-xs">
                Refresh status
              </button>
            </div>
            {run.error_safe && <p className="text-xs text-danger mb-2">{run.error_safe}</p>}

            <div className="grid grid-cols-2 lg:grid-cols-6 gap-3 mb-4">
              <Stat label="Net P&L" value={`$${net.toLocaleString(undefined, { maximumFractionDigits: 2 })}`} tone={net >= 0 ? "good" : "bad"} />
              <Stat label="Return" value={`${Number(m.net_return_pct ?? 0).toFixed(2)}%`} />
              <Stat label="Trades" value={String(Number(m.num_trades ?? trades.length ?? 0))} />
              <Stat label="Win rate" value={`${(Number(m.win_rate ?? 0) * 100).toFixed(1)}%`} />
              <Stat label="Profit factor" value={`${Number(m.profit_factor ?? 0).toFixed(2)}`} />
              <Stat label="Max DD" value={`${Number(m.max_drawdown_pct ?? 0).toFixed(2)}%`} tone="warn" />
            </div>

            {quality && (
              <div className="flex gap-2 flex-wrap items-center text-xs mb-4">
                <Badge label={`quality: ${quality.quality_status}`} tone={quality.quality_status === "PASS" ? "good" : quality.quality_status === "PASS_WITH_WARNINGS" ? "warn" : "bad"} />
                <Badge label={`data: ${quality.data_type}`} />
                <Badge label={`bid/ask: ${quality.bid_ask_availability}`} />
                <Badge label={`spread: ${quality.spread_availability}`} />
                <Badge label={`cost confidence: ${quality.cost_model_confidence}`} />
                <Badge label={`gaps: ${quality.gap_count}`} />
                <Badge label={`dups removed: ${quality.duplicate_candles_removed}`} />
                {quality.feed_delay_warning && <span className="text-danger">{quality.feed_delay_warning}</span>}
              </div>
            )}

            <div className="grid lg:grid-cols-2 gap-4">
              <Card title="Price + trades">
                {chartCandles.length > 0 ? (
                  <CandlestickChart candles={chartCandles} markers={markers} height={280} showVolume={false} />
                ) : (
                  <p className="text-sm text-text-dim">No candle data to chart.</p>
                )}
              </Card>
              <Card title="Equity curve">
                {equity.length > 1 ? (
                  <EquityChart points={equity.map((p) => ({ ts: p.ts, equity: p.balance }))} />
                ) : (
                  <p className="text-sm text-text-dim">
                    {equity.length ? "Flat (no closed trades)." : "No equity data."}
                  </p>
                )}
              </Card>
            </div>
          </Card>

          <Card title={`Trades (${trades.length}) · bid/ask aware, costs included`}>
            {trades.length === 0 ? (
              <p className="text-sm text-text-dim">No trades recorded.</p>
            ) : (
              <div className="max-h-96 overflow-auto">
                <table className="w-full text-xs">
                  <thead className="sticky top-0 bg-panel text-text-dim">
                    <tr className="text-left">
                      <th className="py-2 pr-2">Side</th>
                      <th className="py-2 pr-2">Entry (basis)</th>
                      <th className="py-2 pr-2">Exit (basis)</th>
                      <th className="py-2 pr-2">Stop</th>
                      <th className="py-2 pr-2">Target</th>
                      <th className="py-2 pr-2 text-right">Net P&L</th>
                      <th className="py-2 pr-2 text-right">Spread</th>
                      <th className="py-2 pr-2 text-right">Slippage</th>
                      <th className="py-2 pr-2 text-right">Comm</th>
                      <th className="py-2 pr-2 text-right">Swap</th>
                      <th className="py-2">Exit reason</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-border">
                    {trades.map((t, i) => (
                      <tr key={i}>
                        <td className={`py-1.5 pr-2 capitalize ${t.side === "long" ? "text-accent" : "text-danger"}`}>{t.side}</td>
                        <td className="py-1.5 pr-2">
                          {t.entry_price} <span className="text-text-dim">({t.entry_price_basis})</span>
                        </td>
                        <td className="py-1.5 pr-2">
                          {t.exit_price ?? "—"} {t.exit_price && <span className="text-text-dim">({t.exit_price_basis})</span>}
                        </td>
                        <td className="py-1.5 pr-2 text-text-dim">{t.stop ?? "—"}</td>
                        <td className="py-1.5 pr-2 text-text-dim">{t.target ?? "—"}</td>
                        <td className={`py-1.5 pr-2 text-right ${t.net_pnl >= 0 ? "text-accent" : "text-danger"}`}>{t.net_pnl.toFixed(2)}</td>
                        <td className="py-1.5 pr-2 text-right text-text-dim">{t.spread_cost.toFixed(2)}</td>
                        <td className="py-1.5 pr-2 text-right text-text-dim">{t.slippage_cost.toFixed(2)}</td>
                        <td className="py-1.5 pr-2 text-right text-text-dim">{t.commission.toFixed(2)}</td>
                        <td className="py-1.5 pr-2 text-right text-text-dim">{t.swap.toFixed(2)}</td>
                        <td className="py-1.5">{t.exit_reason ?? "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>
        </div>
      )}
    </div>
  );
}