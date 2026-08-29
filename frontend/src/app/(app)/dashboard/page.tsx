"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { api, tokenStore } from "@/lib/api";
import { Badge, Card, SectionTitle, Spinner, Stat } from "@/components/ui";
import type { Overview, Strategy, BacktestList } from "@/types";

export default function DashboardPage() {
  const [overview, setOverview] = useState<Overview | null>(null);
  const [strategies, setStrategies] = useState<Strategy[]>([]);
  const [backtests, setBacktests] = useState<BacktestList["items"]>([]);

  useEffect(() => {
    api<Overview>("/dashboard/overview", { token: tokenStore.get() })
      .then(setOverview)
      .catch(() => setOverview(null));
    api<Strategy[]>("/strategies", { token: tokenStore.get() })
      .then((s) => setStrategies(s.slice(0, 5)))
      .catch(() => {});
    api<BacktestList>("/backtests", { token: tokenStore.get() })
      .then((b) => setBacktests((b.items ?? []).slice(0, 5)))
      .catch(() => {});
  }, []);

  if (!overview) return <Spinner />;

  const pa = overview.paper_account;
  const pnlTone = overview.daily_pnl >= 0 ? "good" : "bad";

  return (
    <div>
      <SectionTitle>Dashboard</SectionTitle>

      <div className="grid grid-cols-2 lg:grid-cols-5 gap-4 mb-6">
        <Stat label="Active strategies" value={overview.active_strategies} />
        <Stat label="Paper balance" value={`$${pa.balance.toLocaleString(undefined, { maximumFractionDigits: 0 })}`} />
        <Stat label="Paper equity" value={`$${pa.equity.toLocaleString(undefined, { maximumFractionDigits: 0 })}`} tone={pa.equity >= pa.balance ? "good" : "bad"} />
        <Stat label="Position size" value={pa.open_positions} />
        <Stat label="Risk alerts" value={overview.risk_alerts} tone={overview.risk_alerts > 0 ? "warn" : "default"} />
      </div>

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
        <Stat label="Daily P&L" value={`$${overview.daily_pnl.toLocaleString(undefined, { maximumFractionDigits: 0 })}`} tone={pnlTone} />
        <Stat label="Closed trades" value={pa.closed_trades} tone="default" />
        <Stat label="Data feed" value={<span className="text-base">{overview.data_feed.provider}</span>} />
        <Stat
          label="Global kill switch"
          value={<span className="text-base">{overview.kill_switch ? "ENGAGED" : "Armed"}</span>}
          tone={overview.kill_switch ? "bad" : "good"}
        />
      </div>

      <div className="grid lg:grid-cols-3 gap-4 mb-6">
        <Card title="Trading sessions (UTC)" className="lg:col-span-2">
          <ul className="divide-y divide-border">
            {overview.sessions.map((s) => (
              <li key={s.name} className="py-2 flex items-center justify-between text-sm">
                <span>{s.name}</span>
                <span className="text-xs text-text-dim">
                  {s.start}–{s.end}{" "}
                  <Badge label={s.active ? "open" : "closed"} tone={s.active ? "good" : "default"} />
                </span>
              </li>
            ))}
          </ul>
        </Card>
        <Card title="Environment">
          <ul className="text-xs text-text-dim space-y-1.5">
            <li>App: <code className="text-accent">{overview.config.app_env}</code></li>
            <li>LLM provider: <code className="text-accent">{overview.config.llm_provider}</code></li>
            <li>Symbols: {overview.data_feed.symbols}</li>
            <li>UTC now: {overview.utc_now.replace("T", " ").slice(0, 19)}</li>
          </ul>
        </Card>
      </div>

      <div className="grid lg:grid-cols-2 gap-4">
        <Card title="Recent strategies">
          {strategies.length === 0 ? (
            <p className="text-sm text-text-dim">
              None yet — <Link className="text-accent underline" href="/strategies">create one in Strategy Studio</Link>.
            </p>
          ) : (
            <ul className="divide-y divide-border">
              {strategies.map((s) => (
                <li key={s.id} className="py-2 flex items-center justify-between text-sm">
                  <Link href={`/strategies/${s.id}`} className="hover:text-accent">{s.name}</Link>
                  <Badge label={s.status} tone={s.status === "active" ? "good" : "default"} />
                </li>
              ))}
            </ul>
          )}
        </Card>
        <Card title="Recent backtests">
          {backtests.length === 0 ? (
            <p className="text-sm text-text-dim">
              None yet — <Link className="text-accent underline" href="/backtests">run one in the Backtest Lab</Link>.
            </p>
          ) : (
            <ul className="divide-y divide-border">
              {backtests.map((b) => (
                <li key={b.id} className="py-2 text-sm flex items-center justify-between">
                  <span className="text-text-dim truncate">{b.strategy_name || b.id.slice(0, 8)}</span>
                  <Badge
                    label={b.status}
                    tone={b.status === "completed" ? "good" : b.status === "failed" ? "bad" : "warn"}
                  />
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
    </div>
  );
}