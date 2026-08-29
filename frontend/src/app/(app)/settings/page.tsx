"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { api, ApiError, tokenStore } from "@/lib/api";
import { Card, SectionTitle } from "@/components/ui";
import type { Strategy } from "@/types";

export default function SettingsPage() {
  const [strategies, setStrategies] = useState<Strategy[]>([]);
  const [selected, setSelected] = useState("");
  const [result, setResult] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api<Strategy[]>("/strategies", { token: tokenStore.get() }).then(setStrategies).catch(() => {});
  }, []);

  async function requestLive() {
    setError(null);
    setResult(null);
    try {
      await api("/live-deployments/request", {
        method: "POST",
        token: tokenStore.get(),
        body: { strategy_id: selected, broker_connection_id: "", risk_acknowledged: true, notes: "Requested from Settings" },
      });
      setResult("Deployment request accepted for review. Live execution is not enabled automatically.");
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "request failed");
    }
  }

  return (
    <div>
      <SectionTitle>Settings</SectionTitle>

      <Card title="Live deployment" className="mb-6">
        <p className="text-sm text-text-dim leading-relaxed mb-4">
          Live execution is <strong>disabled by default</strong>. Even a request here only produces a sandbox
          deployment review record — the platform has no real broker adapter installed, so nothing can reach a market.
        </p>
        <div className="flex items-end gap-3">
          <label className="flex-1 max-w-sm">
            <span className="text-xs text-text-dim block mb-1">Strategy</span>
            <select value={selected} onChange={(e) => setSelected(e.target.value)} className="w-full rounded-lg border border-border bg-bg px-3 py-2 text-sm">
              <option value="">Select…</option>
              {strategies.map((s) => (
                <option key={s.id} value={s.id}>{s.name}</option>
              ))}
            </select>
          </label>
          <button onClick={requestLive} disabled={!selected} className="rounded-lg border border-warn text-warn px-4 py-2 text-sm disabled:opacity-40 disabled:cursor-not-allowed">
            Request sandbox deployment
          </button>
        </div>
        {result && <div className="text-xs text-accent mt-3">{result}</div>}
        {error && <div className="text-xs text-danger mt-3">{error}</div>}
      </Card>

      <Card title="About">
        <p className="text-sm text-text-dim leading-relaxed">
          FX Scalper Lab is an educational research & paper-trading platform for forex scalping hypotheses.
          Backtested and simulated results are not indicative of future performance. Read more in{" "}
          <Link className="text-accent underline" href="/docs/DISCLAIMER" target="_blank">
            DISCLAIMER
          </Link>
          .
        </p>
        <p className="text-xs text-text-dim mt-3">API endpoints are documented at <code className="text-accent">/docs</code> on the backend.</p>
      </Card>
    </div>
  );
}