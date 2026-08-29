"use client";

import { FormEvent, useEffect, useState } from "react";
import { api, ApiError, tokenStore } from "@/lib/api";
import { Badge, Card, SectionTitle, Spinner, Stat } from "@/components/ui";
import type {
  PaperStatus,
  PaperPosition,
  PaperTrade,
  Strategy,
} from "@/types";

export default function PaperPage() {
  const [status, setStatus] = useState<PaperStatus | null>(null);
  const [strategies, setStrategies] = useState<Strategy[]>([]);
  const [strategyId, setStrategyId] = useState("");
  const [side, setSide] = useState<"long" | "short">("long");
  const [size, setSize] = useState<number | undefined>(undefined);
  const [positions, setPositions] = useState<PaperPosition[]>([]);
  const [trades, setTrades] = useState<PaperTrade[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);

  function stateTone(state?: string): "good" | "bad" | "warn" | "default" {
    switch (state) {
      case "ACTIVE":
        return "good";
      case "RISK_PAUSED":
      case "KILL_SWITCHED":
        return "bad";
      case "DATA_PAUSED":
        return "warn";
      default:
        return "default";
    }
  }

  async function load() {
    await Promise.all([
      api<PaperStatus>("/paper-trading/status", { token: tokenStore.get() })
        .then(setStatus)
        .catch(() => setStatus(null)),
      api<PaperPosition[]>("/paper-trading/positions", { token: tokenStore.get() })
        .then(setPositions)
        .catch(() => setPositions([])),
      api<PaperTrade[]>("/paper-trading/trades", { token: tokenStore.get() })
        .then(setTrades)
        .catch(() => setTrades([])),
    ]);
  }

  useEffect(() => {
    load();
    api<Strategy[]>("/strategies", { token: tokenStore.get() }).then(setStrategies).catch(() => {});
  }, []);

  async function start() {
    setError(null);
    await api("/paper-trading/start", { method: "POST", token: tokenStore.get(), body: { balance: 100000 } });
    await load();
  }

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setInfo(null);
    try {
      const res = await api<{ approved: boolean; reason?: string; position_id?: string }>("/paper-trading/order", {
        method: "POST",
        token: tokenStore.get(),
        body: {
          strategy_id: strategyId,
          side,
          ...(size ? { size_units: size } : {}),
        },
      });
      if (res.approved) {
        setInfo("Order approved and opened in the simulated account.");
      } else {
        setError(`Order rejected by risk engine: ${res.reason ?? "unknown"}`);
      }
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "order failed (risk engine may have rejected it)");
    }
  }

  async function closePosition(id: string) {
    setError(null);
    try {
      await api(`/paper-trading/positions/${id}/close`, { method: "POST", token: tokenStore.get(), body: {} });
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "close failed");
    }
  }

  if (!status) {
    return (
      <div>
        <SectionTitle>Paper Trading</SectionTitle>
        <Card>
          <p className="text-sm mb-4">Start a simulated account to begin paper trading. No real money, no real execution.</p>
          <button onClick={start} className="rounded-lg bg-accent/90 text-bg px-4 py-2 text-sm font-semibold hover:bg-accent">
            Start paper account
          </button>
        </Card>
      </div>
    );
  }

  return (
    <div>
      <SectionTitle>Paper Trading</SectionTitle>

      <div className="grid grid-cols-2 lg:grid-cols-5 gap-4 mb-6">
        <Stat label="Balance" value={`$${status.balance.toLocaleString(undefined, { maximumFractionDigits: 2 })}`} />
        <Stat label="Equity" value={`$${status.equity.toLocaleString(undefined, { maximumFractionDigits: 2 })}`} tone={status.equity >= status.balance ? "good" : "bad"} />
        <Stat label="Open positions" value={status.open_positions} />
        <Stat label="Closed trades" value={status.closed_trades} />
        <Stat label="Trading state" value={<span className="text-base"><Badge label={status.trading_state || "ACTIVE"} tone={stateTone(status.trading_state)} /></span>} />
      </div>

      {status.state_reason && <p className="text-xs text-text-dim mb-4">State: {status.state_reason}</p>}
      {typeof status.pending_orders === "number" && status.pending_orders > 0 && (
        <p className="text-xs text-warn mb-4">{status.pending_orders} pending order(s) awaiting approval.</p>
      )}

      <div className="grid lg:grid-cols-2 gap-4 mb-6">
        <Card title="Place simulated order">
          <form onSubmit={submit} className="space-y-3 text-sm">
            <label className="block">
              <span className="text-xs text-text-dim block mb-1">Strategy</span>
              <select value={strategyId} onChange={(e) => setStrategyId(e.target.value)} required className="w-full rounded-lg border border-border bg-bg px-3 py-2">
                <option value="">Select…</option>
                {strategies.map((s) => (
                  <option key={s.id} value={s.id}>{s.name}</option>
                ))}
              </select>
            </label>
            <div className="grid grid-cols-2 gap-3">
              <label>
                <span className="text-xs text-text-dim block mb-1">Side</span>
                <select value={side} onChange={(e) => setSide(e.target.value as "long" | "short")} className="w-full rounded-lg border border-border bg-bg px-3 py-2">
                  <option value="long">Long</option>
                  <option value="short">Short</option>
                </select>
              </label>
              <label>
                <span className="text-xs text-text-dim block mb-1">Size (units, optional)</span>
                <input type="number" value={size ?? ""} onChange={(e) => setSize(e.target.value ? Number(e.target.value) : undefined)} placeholder="auto from risk %" className="w-full rounded-lg border border-border bg-bg px-3 py-2" />
              </label>
            </div>
            <p className="text-xs text-text-dim">
              Stop/target sizing, position sizing and entry price are derived from the strategy&apos;s risk parameters
              and the live mock quote. Every order is gated by the RiskEngine.
            </p>
            {error && <div className="text-xs text-danger">{error}</div>}
            {info && <div className="text-xs text-accent">{info}</div>}
            <button className="rounded-lg bg-accent/90 text-bg px-4 py-2 font-semibold hover:bg-accent">Submit order</button>
          </form>
        </Card>

        <Card title="Open positions">
          {positions.length === 0 ? (
            <p className="text-sm text-text-dim">No open positions.</p>
          ) : (
            <ul className="divide-y divide-border">
              {positions.map((p) => (
                <li key={p.id} className="py-2 flex items-center justify-between gap-3">
                  <div className="min-w-0">
                    <div className="text-sm">
                      {p.symbol} <Badge label={p.side} tone={p.side === "long" ? "good" : "bad"} />{" "}
                      <span className="text-text-dim text-xs">{p.size_units.toLocaleString()}u</span>
                    </div>
                    <div className="text-xs text-text-dim mt-0.5">
                      @ {p.entry_price} · stop {p.stop_loss} · target {p.take_profit} · mark {p.mark_price}
                    </div>
                  </div>
                  <div className="text-right shrink-0">
                    <div className={`text-sm font-medium ${p.unrealized_pnl >= 0 ? "text-accent" : "text-danger"}`}>
                      ${p.unrealized_pnl.toLocaleString(undefined, { maximumFractionDigits: 2 })}
                    </div>
                    <button onClick={() => closePosition(p.id)} className="text-xs text-warn underline">
                      close
                    </button>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>

      <Card title="Trade history">
        {trades.length === 0 ? (
          <p className="text-sm text-text-dim">No closed trades yet.</p>
        ) : (
          <div className="max-h-96 overflow-auto">
            <table className="w-full text-xs">
              <thead className="sticky top-0 bg-panel text-text-dim">
                <tr className="text-left">
                  <th className="py-2 pr-3">Symbol</th>
                  <th className="py-2 pr-3">Side</th>
                  <th className="py-2 pr-3">Entry</th>
                  <th className="py-2 pr-3">Exit</th>
                  <th className="py-2 pr-3 text-right">Pips</th>
                  <th className="py-2 pr-3 text-right">Net P&L</th>
                  <th className="py-2">Reason</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {trades.map((t) => (
                  <tr key={t.id}>
                    <td className="py-1.5 pr-3">{t.symbol}</td>
                    <td className="py-1.5 pr-3 capitalize">{t.side}</td>
                    <td className="py-1.5 pr-3">{t.entry_price}</td>
                    <td className="py-1.5 pr-3">{t.exit_price}</td>
                    <td className="py-1.5 pr-3 text-right">{t.pips.toFixed(1)}</td>
                    <td className={`py-1.5 pr-3 text-right ${t.net_pnl >= 0 ? "text-accent" : "text-danger"}`}>
                      ${t.net_pnl.toLocaleString(undefined, { maximumFractionDigits: 2 })}
                    </td>
                    <td className="py-1.5">{t.exit_reason}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}