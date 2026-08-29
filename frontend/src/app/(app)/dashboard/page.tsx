"use client";

import { useEffect, useState } from "react";
import { api, tokenStore } from "@/lib/api";
import { Card, Stat, SectionTitle, Spinner } from "@/components/ui";

type DashboardData = {
  total_strategies: number;
  total_backtests: number;
  paper_balance: number;
  paper_equity: number;
  recent_strategies: { id: string; name: string; strategy_family: string; status: string }[];
  recent_backtests: { job_id: string; status: string }[];
};

export default function DashboardPage() {
  const [data, setData] = useState<DashboardData | null>(null);

  useEffect(() => {
    api<DashboardData>("/dashboard", { token: tokenStore.get() })
      .then(setData)
      .catch(() => setData(null));
  }, []);

  if (!data) return <Spinner />;

  return (
    <div>
      <SectionTitle>Dashboard</SectionTitle>
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
        <Stat label="Strategies" value={data.total_strategies} />
        <Stat label="Backtests" value={data.total_backtests} />
        <Stat
          label="Paper balance"
          value={`$${data.paper_balance.toLocaleString(undefined, { maximumFractionDigits: 0 })}`}
          tone={data.paper_equity >= data.paper_balance ? "good" : "bad"}
        />
        <Stat label="Paper equity" value={`$${data.paper_equity.toLocaleString(undefined, { maximumFractionDigits: 0 })}`} />
      </div>

      <div className="grid lg:grid-cols-2 gap-4">
        <Card title="Recent strategies">
          {data.recent_strategies.length === 0 ? (
            <p className="text-sm text-text-dim">None yet — create one in Strategy Studio.</p>
          ) : (
            <ul className="divide-y divide-border">
              {data.recent_strategies.map((s) => (
                <li key={s.id} className="py-2 flex items-center justify-between text-sm">
                  <span>{s.name}</span>
                  <span className="text-xs uppercase text-text-dim">{s.status}</span>
                </li>
              ))}
            </ul>
          )}
        </Card>
        <Card title="Recent backtests">
          {data.recent_backtests.length === 0 ? (
            <p className="text-sm text-text-dim">None yet — run one in the Backtest Lab.</p>
          ) : (
            <ul className="divide-y divide-border">
              {data.recent_backtests.map((b) => (
                <li key={b.job_id} className="py-2 text-sm flex items-center justify-between">
                  <span className="text-text-dim">{b.job_id.slice(0, 8)}…</span>
                  <span className="text-xs uppercase">{b.status}</span>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
    </div>
  );
}