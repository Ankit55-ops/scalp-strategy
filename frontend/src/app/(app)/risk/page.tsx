"use client";

import { useEffect, useState } from "react";
import { api, ApiError, tokenStore } from "@/lib/api";
import { Badge, Card, SectionTitle, Spinner } from "@/components/ui";

type KSStatus = {
  global: boolean;
  strategy: Record<string, boolean>;
  pair: Record<string, boolean>;
};

type RiskEvent = {
  id: string;
  event_type: string;
  severity: string;
  symbol: string | null;
  details: string | null;
  created_at: string;
};

export default function RiskPage() {
  const [ks, setKs] = useState<KSStatus | null>(null);
  const [events, setEvents] = useState<RiskEvent[]>([]);
  const [message, setMessage] = useState<string | null>(null);
  const [strategyId, setStrategyId] = useState("");

  async function load() {
    try {
      setKs(await api<KSStatus>("/risk/kill-switch", { token: tokenStore.get() }));
    } catch {
      setKs({ global: false, strategy: {}, pair: {} });
    }
    try {
      setEvents(await api<RiskEvent[]>("/risk/events?limit=50", { token: tokenStore.get() }));
    } catch {
      setEvents([]);
    }
  }

  useEffect(() => {
    load();
  }, []);

  async function toggle(scope: "global" | "strategy" | "pair", resourceId: string, enabled: boolean, key: string) {
    setMessage(null);
    try {
      await api("/risk/kill-switch", {
        method: "POST",
        token: tokenStore.get(),
        body: { scope, resource_id: resourceId, enabled, reason: "Changed from Risk Center" },
      });
      await load();
    } catch (e) {
      setMessage(e instanceof ApiError ? e.message : "kill switch update failed");
    }
  }

  if (!ks) return <Spinner />;

  return (
    <div>
      <SectionTitle>Risk Center</SectionTitle>
      {message && <div className="text-sm text-danger mb-4">{message}</div>}

      <Card title="Kill switches" className="mb-6">
        <p className="text-xs text-text-dim mb-4">
          Kill switches halt <strong>new entries</strong> immediately at the risk engine. Existing stops/targets still
          manage open positions. They are one-way: they must be manually re-armed.
        </p>
        <div className="space-y-2">
          <SwitchRow
            label="Global (all strategies)"
            enabled={ks.global}
            onToggle={() => toggle("global", "global", !ks.global, "global")}
          />
          {Object.entries(ks.strategy).map(([id, enabled]) => (
            <SwitchRow key={`strategy-${id}`} label={`Strategy ${id}`} enabled={enabled} onToggle={() => toggle("strategy", id, !enabled, id)} />
          ))}
          {Object.entries(ks.pair).map(([sym, enabled]) => (
            <SwitchRow key={`pair-${sym}`} label={`Pair ${sym}`} enabled={enabled} onToggle={() => toggle("pair", sym, !enabled, sym)} />
          ))}
        </div>
        {Object.keys(ks.strategy).length === 0 && Object.keys(ks.pair).length === 0 && (
          <p className="text-xs text-text-dim mt-3">No per-strategy or per-pair switches active.</p>
        )}
      </Card>

      <Card title="Recent risk events">
        {events.length === 0 ? (
          <p className="text-sm text-text-dim">No events recorded yet.</p>
        ) : (
          <table className="w-full text-xs">
            <thead>
              <tr className="text-left text-text-dim border-b border-border">
                <th className="py-2 pr-3">Time</th>
                <th className="py-2 pr-3">Type</th>
                <th className="py-2 pr-3">Severity</th>
                <th className="py-2 pr-3">Symbol</th>
                <th className="py-2">Details</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {events.map((e) => (
                <tr key={e.id}>
                  <td className="py-2 pr-3 text-text-dim">{new Date(e.created_at).toLocaleString()}</td>
                  <td className="py-2 pr-3">{e.event_type}</td>
                  <td className="py-2 pr-3">
                    <Badge
                      label={e.severity}
                      tone={e.severity === "critical" ? "bad" : e.severity === "warn" ? "warn" : "default"}
                    />
                  </td>
                  <td className="py-2 pr-3">{e.symbol ?? "—"}</td>
                  <td className="py-2 text-text-dim truncate max-w-xs">{e.details ?? ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  );
}

function SwitchRow({ label, enabled, onToggle }: { label: string; enabled: boolean; onToggle: () => void }) {
  return (
    <div className="flex items-center justify-between rounded-lg border border-border bg-bg px-4 py-2.5">
      <div className="flex items-center gap-3">
        <span className="text-sm">{label}</span>
        {enabled ? <Badge label="HALTED" tone="bad" /> : <Badge label="armed" tone="good" />}
      </div>
      <button
        onClick={onToggle}
        className={`text-xs px-3 py-1.5 rounded-lg border transition-colors ${
          enabled
            ? "border-warn text-warn hover:bg-warn/10"
            : "border-border text-text-dim hover:text-text hover:border-accent"
        }`}
      >
        {enabled ? "Disarm" : "Engage"}
      </button>
    </div>
  );
}