"use client";

import { FormEvent, useEffect, useState } from "react";
import Link from "next/link";
import { api, ApiError, tokenStore } from "@/lib/api";
import { Badge, Card, SectionTitle, Stat } from "@/components/ui";
import EquityChart from "@/components/EquityChart";
import type { BacktestSummary, Strategy, Trade } from "@/types";

export default function BacktestsPage() {
  const [strategies, setStrategies] = useState<Strategy[]>([]);
  const [strategyId, setStrategyId] = useState("");
  const [dateFrom, setDateFrom] = useState("2026-01-01");
  const [dateTo, setDateTo] = useState("2026-04-01");
  const [balance, setBalance] = useState(100000);
  const [mc, setMc] = useState(true);
  const [wf, setWf] = useState(false);
  const [spread, setSpread] = useState(0.8);

  const [summary, setSummary] = useState<BacktestSummary | null>(null);
  const [trades, setTrades] = useState<Trade[]>([]);
  const [equity, setEquity] = useState<{ ts: number; equity: number }[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api<Strategy[]>("/strategies", { token: tokenStore.get() }).then(setStrategies).catch(() => {});
  }, []);

  async function run(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    setSummary(null);
    setTrades([]);
    setEquity([]);
    try {
      const job = await api<{ id: string }>("/backtests", {
        method: "POST",
        token: tokenStore.get(),
        body: {
          strategy_id: strategyId,
          pairs: ["EURUSD"],
          timeframe: "M5",
          date_from: dateFrom,
          date_to: dateTo,
          balance,
          spread_pips: spread,
          run_monte_carlo: mc,
          run_walk_forward: wf,
        },
      });

      // Poll until the job settles (queued/running may be async via the RQ worker).
      let s = await api<BacktestSummary>(`/backtests/${job.id}`, { token: tokenStore.get() });
      for (let attempt = 0; attempt < 60; attempt++) {
        if (s.status === "completed" || s.status === "failed") break;
        await new Promise((r) => setTimeout(r, 750));
        s = await api<BacktestSummary>(`/backtests/${job.id}`, { token: tokenStore.get() });
      }
      if (s.status === "queued" || s.status === "running") {
        setError("Backtest is still processing in the background. Refresh to see progress.");
      }

      const [t, eCurve] = await Promise.all([
        api<Trade[]>(`/backtests/${job.id}/trades`, { token: tokenStore.get() }).catch(() => []),
        api<{ ts: number; equity: number }[]>(`/backtests/${job.id}/equity-curve`, { token: tokenStore.get() }).catch(() => []),
      ]);
      setSummary(s);
      setTrades(t);
      setEquity(eCurve);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "backtest failed");
    } finally {
      setBusy(false);
    }
  }

  const metrics = summary?.metrics ?? {};
  const classification = summary?.validation?.classification;

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <SectionTitle>Backtest Lab</SectionTitle>
        <Link href="/real-historical" className="text-sm text-accent hover:underline">
          Real Historical Data →
        </Link>
      </div>

      <Card title="Run a backtest" className="mb-6">
        <form onSubmit={run} className="grid md:grid-cols-6 gap-3 items-end">
          <label className="md:col-span-2">
            <span className="text-xs text-text-dim block mb-1">Strategy</span>
            <select
              value={strategyId}
              onChange={(e) => setStrategyId(e.target.value)}
              required
              className="w-full rounded-lg border border-border bg-bg px-3 py-2 text-sm"
            >
              <option value="">Select…</option>
              {strategies.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                </option>
              ))}
            </select>
          </label>
          <label>
            <span className="text-xs text-text-dim block mb-1">From</span>
            <input type="date" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} className="w-full rounded-lg border border-border bg-bg px-3 py-2 text-sm" />
          </label>
          <label>
            <span className="text-xs text-text-dim block mb-1">To</span>
            <input type="date" value={dateTo} onChange={(e) => setDateTo(e.target.value)} className="w-full rounded-lg border border-border bg-bg px-3 py-2 text-sm" />
          </label>
          <label>
            <span className="text-xs text-text-dim block mb-1">Balance</span>
            <input type="number" value={balance} onChange={(e) => setBalance(Number(e.target.value))} className="w-full rounded-lg border border-border bg-bg px-3 py-2 text-sm" />
          </label>
          <label>
            <span className="text-xs text-text-dim block mb-1">Spread (pips)</span>
            <input type="number" step="0.1" value={spread} onChange={(e) => setSpread(Number(e.target.value))} className="w-full rounded-lg border border-border bg-bg px-3 py-2 text-sm" />
          </label>
          <div className="md:col-span-4 flex items-center gap-6 text-sm">
            <label className="flex items-center gap-2">
              <input type="checkbox" checked={mc} onChange={(e) => setMc(e.target.checked)} className="accent-accent" />
              Monte Carlo
            </label>
            <label className="flex items-center gap-2">
              <input type="checkbox" checked={wf} onChange={(e) => setWf(e.target.checked)} className="accent-accent" />
              Walk-forward OOS
            </label>
            <button type="submit" disabled={busy} className="ml-auto rounded-lg bg-accent/90 text-bg px-4 py-2 text-sm font-semibold hover:bg-accent disabled:opacity-50">
              {busy ? "Running…" : "Run"}
            </button>
          </div>
        </form>
        {error && <div className="text-sm text-danger mt-3">{error}</div>}
      </Card>

      {summary && (
        <>
          <div className="grid grid-cols-2 lg:grid-cols-6 gap-4 mb-4">
            <Stat label="Status" value={<span className="text-base">{summary.status}</span>} />
            <Stat label="Trades" value={String(metrics.num_trades ?? "—")} />
            <Stat label="Net P&L" value={`$${Number(metrics.net_profit ?? 0).toLocaleString(undefined, { maximumFractionDigits: 2 })}`} tone={Number(metrics.net_profit ?? 0) >= 0 ? "good" : "bad"} />
            <Stat label="Profit factor" value={String(Number(metrics.profit_factor ?? 0).toFixed(2))} />
            <Stat label="Win rate" value={`${(Number(metrics.win_rate ?? 0) * 100).toFixed(1)}%`} />
            <Stat label="Max DD" value={`${Number(metrics.max_drawdown_pct ?? 0).toFixed(2)}%`} tone="warn" />
          </div>

          <div className="grid lg:grid-cols-3 gap-4 mb-4">
            <Card title="Eligibility" className="lg:col-span-1">
              <div className="flex items-center gap-2 mb-2">
                <Badge
                  label={classification?.status ?? "unknown"}
                  tone={classification?.status === "paper_trading_eligible" ? "good" : classification?.status === "rejected" ? "bad" : "warn"}
                />
                {typeof classification?.score === "number" && <span className="text-sm text-text-dim">score {classification.score}</span>}
              </div>
              <p className="text-xs text-text-dim">
                Classification is an <strong>eligibility</strong> label from historical + robustness data, not a profit
                forecast. See Risk Center.
              </p>
            </Card>
            <Card title="Monte Carlo" className="lg:col-span-1">
              {summary.robustness?.monte_carlo_trade_order ? (
                <pre className="text-xs text-text-dim whitespace-pre-wrap">{JSON.stringify(summary.robustness.monte_carlo_trade_order, null, 2)}</pre>
              ) : (
                <p className="text-sm text-text-dim">Not run.</p>
              )}
            </Card>
            <Card title="Walk-forward" className="lg:col-span-1">
              {summary.robustness?.walk_forward ? (
                <pre className="text-xs text-text-dim whitespace-pre-wrap">{JSON.stringify(summary.robustness.walk_forward, null, 2)}</pre>
              ) : (
                <p className="text-sm text-text-dim">Not run.</p>
              )}
            </Card>
          </div>

          <Card title="Equity curve" className="mb-4">
            {equity.length > 1 ? (
              <EquityChart points={equity} />
            ) : (
              <p className="text-sm text-text-dim">No equity data.</p>
            )}
          </Card>

          <Card title={`Trades (${trades.length})`}>
            {trades.length === 0 ? (
              <p className="text-sm text-text-dim">No trades recorded.</p>
            ) : (
              <div className="max-h-96 overflow-auto">
                <table className="w-full text-xs">
                  <thead className="sticky top-0 bg-panel text-text-dim">
                    <tr className="text-left">
                      <th className="py-2 pr-3">Side</th>
                      <th className="py-2 pr-3">Entry</th>
                      <th className="py-2 pr-3">Exit</th>
                      <th className="py-2 pr-3">Stop</th>
                      <th className="py-2 pr-3">Target</th>
                      <th className="py-2 pr-3 text-right">Net P&L</th>
                      <th className="py-2 pr-3 text-right">Pips</th>
                      <th className="py-2">Reason</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-border">
                    {trades.map((t, i) => (
                      <tr key={i}>
                        <td className="py-1.5 pr-3 capitalize">{t.side}</td>
                        <td className="py-1.5 pr-3">{t.entry_price}</td>
                        <td className="py-1.5 pr-3">{t.exit_price}</td>
                        <td className="py-1.5 pr-3 text-text-dim">{t.stop}</td>
                        <td className="py-1.5 pr-3 text-text-dim">{t.target}</td>
                        <td className={`py-1.5 pr-3 text-right ${t.net_pnl >= 0 ? "text-accent" : "text-danger"}`}>{t.net_pnl.toFixed(2)}</td>
                        <td className="py-1.5 pr-3 text-right">{t.pips.toFixed(1)}</td>
                        <td className="py-1.5">{t.exit_reason}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>
        </>
      )}
    </div>
  );
}