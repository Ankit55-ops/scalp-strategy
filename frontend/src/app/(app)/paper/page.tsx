"use client";

import { FormEvent, useEffect, useState } from "react";
import { api, tokenStore } from "@/lib/api";
import { Badge, Card, SectionTitle, Spinner, Stat } from "@/components/ui";
import type { PaperStatus, Strategy } from "@/types";

export default function PaperPage() {
  const [status, setStatus] = useState<PaperStatus | null>(null);
  const [strategies, setStrategies] = useState<Strategy[]>([]);
  const [strategyId, setStrategyId] = useState("");
  const [side, setSide] = useState<"buy" | "sell">("buy");
  const [price, setPrice] = useState(1.1);
  const [size, setSize] = useState(10000);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<Record<string, unknown> | null>(null);

  async function load() {
    try {
      setStatus(await api<PaperStatus>("/paper-trading/status", { token: tokenStore.get() }));
    } catch {
      setStatus(null);
    }
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
    setResult(null);
    try {
      const res = await api<Record<string, unknown>>("/paper-trading/order", {
        method: "POST",
        token: tokenStore.get(),
        body: { strategy_id: strategyId, side, symbol: "EURUSD", price, size_units: size },
      });
      setResult(res);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "order failed (risk engine may have rejected it)");
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
        <Stat label="Balance" value={`$${status.balance.toLocaleString(undefined, { maximumFractionDigits: 0 })}`} />
        <Stat label="Equity" value={`$${status.equity.toLocaleString(undefined, { maximumFractionDigits: 0 })}`} tone={status.equity >= status.balance ? "good" : "bad"} />
        <Stat label="Open positions" value={status.open_positions} />
        <Stat label="Closed trades" value={status.closed_trades} />
        <Stat label="Mode" value={<span className="text-base"><Badge label="simulated" tone="accent" /></span>} />
      </div>

      <div className="grid lg:grid-cols-2 gap-4">
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
                <select value={side} onChange={(e) => setSide(e.target.value as "buy" | "sell")} className="w-full rounded-lg border border-border bg-bg px-3 py-2">
                  <option value="buy">Buy</option>
                  <option value="sell">Sell</option>
                </select>
              </label>
              <label>
                <span className="text-xs text-text-dim block mb-1">Size (units)</span>
                <input type="number" value={size} onChange={(e) => setSize(Number(e.target.value))} className="w-full rounded-lg border border-border bg-bg px-3 py-2" />
              </label>
            </div>
            <label>
              <span className="text-xs text-text-dim block mb-1">Reference price</span>
              <input type="number" step="0.00001" value={price} onChange={(e) => setPrice(Number(e.target.value))} className="w-full rounded-lg border border-border bg-bg px-3 py-2" />
            </label>
            {error && <div className="text-xs text-danger">{error}</div>}
            <button className="rounded-lg bg-accent/90 text-bg px-4 py-2 font-semibold hover:bg-accent">Submit order</button>
          </form>
          {result && (
            <pre className="mt-4 text-xs text-text-dim bg-bg rounded-lg p-3 overflow-auto">{JSON.stringify(result, null, 2)}</pre>
          )}
        </Card>

        <Card title="Risk note">
          <p className="text-sm leading-relaxed text-text-dim">
            Every paper order passes through the same <strong>RiskEngine</strong> as live orders would: kill switches,
            session, news blackout, spread ceiling, stop-distance floor, open-position cap, daily loss limit and
            correlated-exposure ceiling. A rejected order never reaches the simulated broker.
          </p>
        </Card>
      </div>
    </div>
  );
}