"use client";

import { FormEvent, useEffect, useState } from "react";
import Link from "next/link";
import { api, ApiError, tokenStore } from "@/lib/api";
import { Badge, Card, SectionTitle } from "@/components/ui";
import type { Alert, Broker, Deployment, Strategy } from "@/types";

export default function SettingsPage() {
  const [strategies, setStrategies] = useState<Strategy[]>([]);
  const [brokers, setBrokers] = useState<Broker[]>([]);
  const [deployments, setDeployments] = useState<Deployment[]>([]);
  const [alerts, setAlerts] = useState<Alert[]>([]);

  const [selected, setSelected] = useState("");
  const [result, setResult] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  // broker connect form
  const [bLabel, setBLabel] = useState("My Sandbox");
  const [bProvider, setBProvider] = useState("simulated");
  const [bApiKey, setBApiKey] = useState("");
  const [bSandbox, setBSandbox] = useState(true);

  // alerts
  const [alertsTab, setAlertsTab] = useState(false);

  async function loadAll() {
    await Promise.all([
      api<Broker[]>("/brokers", { token: tokenStore.get() }).then(setBrokers).catch(() => {}),
      api<Deployment[]>("/live-deployments", { token: tokenStore.get() }).then(setDeployments).catch(() => {}),
      api<Alert[]>("/alerts", { token: tokenStore.get() }).then(setAlerts).catch(() => {}),
    ]);
  }

  useEffect(() => {
    loadAll();
    api<Strategy[]>("/strategies", { token: tokenStore.get() }).then(setStrategies).catch(() => {});
  }, []);

  async function requestLive() {
    setError(null);
    setResult(null);
    try {
      const conn = brokers.find((b) => b.is_sandbox) ?? brokers[0];
      if (!conn) {
        setError("Connect a (sandbox) broker first.");
        return;
      }
      await api("/live-deployments/request", {
        method: "POST",
        token: tokenStore.get(),
        body: { strategy_id: selected, broker_connection_id: conn.id, risk_acknowledged: true },
      });
      setResult("Deployment request submitted for review. Live execution stays disabled until pre-flight checks pass.");
      await loadAll();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "request failed");
    }
  }

  async function connectBroker(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setResult(null);
    try {
      const conn = await api<Broker>("/brokers/connect", {
        method: "POST",
        token: tokenStore.get(),
        body: { provider: bProvider, label: bLabel, api_key: bApiKey || undefined, sandbox: bSandbox },
      });
      setResult(`Broker "${conn.label}" connected (${conn.is_sandbox ? "sandbox" : "alert: non-sandbox"}).`);
      setBLabel("My Sandbox");
      setBApiKey("");
      await loadAll();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "connect failed");
    }
  }

  async function testBroker(id: string) {
    setError(null);
    setResult(null);
    try {
      const r = await api<{ ok: boolean; message: string; symbols: string[] }>(`/brokers/${id}/test`, {
        method: "POST",
        token: tokenStore.get(),
        body: {},
      });
      setResult(`Test: ${r.message} (${r.symbols.length} symbols)`);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "test failed");
    }
  }

  async function deleteBroker(id: string) {
    try {
      await api(`/brokers/${id}`, { method: "DELETE", token: tokenStore.get() });
      await loadAll();
    } catch {
      setError("could not delete broker");
    }
  }

  async function markRead(id: string) {
    await api(`/alerts/${id}/read`, { method: "POST", token: tokenStore.get() });
    await loadAll();
  }

  async function approveDeployment(id: string) {
    setError(null);
    try {
      const r = await api<{ approved: boolean; status: string; reasons?: string[] }>(`/live-deployments/${id}/approve`, {
        method: "POST",
        token: tokenStore.get(),
        body: { confirm: true },
      });
      if (r.approved) {
        setResult(`Deployment approved (status: ${r.status}).`);
      } else {
        setError(`Pre-flight check failed: ${(r.reasons ?? []).join("; ")}`);
      }
      await loadAll();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "approval failed");
    }
  }

  const unread = alerts.filter((a) => !a.is_read).length;

  return (
    <div>
      <SectionTitle>Settings</SectionTitle>

      <div className="grid xl:grid-cols-2 gap-4 mb-6">
        <Card title={`Broker connections (${brokers.length})`}>
          <form onSubmit={connectBroker} className="space-y-3 text-sm mb-4">
            <div className="grid grid-cols-2 gap-3">
              <label>
                <span className="text-xs text-text-dim block mb-1">Label</span>
                <input value={bLabel} onChange={(e) => setBLabel(e.target.value)} className="w-full rounded-lg border border-border bg-bg px-3 py-2" />
              </label>
              <label>
                <span className="text-xs text-text-dim block mb-1">Provider</span>
                <select value={bProvider} onChange={(e) => setBProvider(e.target.value)} className="w-full rounded-lg border border-border bg-bg px-3 py-2">
                  <option value="simulated">simulated</option>
                </select>
              </label>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <label>
                <span className="text-xs text-text-dim block mb-1">API key (encrypted at rest)</span>
                <input value={bApiKey} onChange={(e) => setBApiKey(e.target.value)} className="w-full rounded-lg border border-border bg-bg px-3 py-2" />
              </label>
              <label className="flex items-end gap-2 pb-2">
                <input type="checkbox" checked={bSandbox} onChange={(e) => setBSandbox(e.target.checked)} className="accent-accent" />
                <span className="text-xs">Sandbox account</span>
              </label>
            </div>
            <button className="rounded-lg bg-accent/90 text-bg px-4 py-2 font-semibold hover:bg-accent">Connect broker</button>
          </form>

          {brokers.length === 0 ? (
            <p className="text-sm text-text-dim">No broker connections yet.</p>
          ) : (
            <ul className="divide-y divide-border">
              {brokers.map((b) => (
                <li key={b.id} className="py-2 flex items-center justify-between gap-3 text-sm">
                  <div className="min-w-0">
                    <span className="font-medium">{b.label}</span>
                    <span className="text-text-dim text-xs ml-2">
                      {b.provider} · {b.is_sandbox ? "sandbox" : "non-sandbox"}
                    </span>
                  </div>
                  <div className="flex items-center gap-3 shrink-0">
                    <Badge label={b.status} tone={b.status === "connected" ? "good" : "warn"} />
                    <button onClick={() => testBroker(b.id)} className="text-xs text-accent underline">test</button>
                    <button onClick={() => deleteBroker(b.id)} className="text-xs text-danger underline">delete</button>
                  </div>
                </li>
              ))}
            </ul>
          )}
          <p className="text-xs text-text-dim mt-3">
            <strong>Live execution is disabled by default.</strong> Only sandbox adapters are implemented; even a
            connected account cannot place real orders.
          </p>
        </Card>

        <Card title="Alerts">
          <div className="flex items-center justify-between mb-3">
            <span className="text-sm text-text-dim">{unread} unread</span>
            <button
              onClick={() => setAlertsTab(!alertsTab)}
              className="text-xs text-accent underline"
            >
              {alertsTab ? "hide" : "show"} all
            </button>
          </div>
          {alerts.length === 0 ? (
            <p className="text-sm text-text-dim">No alerts yet.</p>
          ) : (
            <ul className="divide-y divide-border max-h-72 overflow-auto">
              {(alertsTab ? alerts : alerts.filter((a) => !a.is_read)).map((a) => (
                <li key={a.id} className="py-2 text-sm">
                  <div className="flex items-center gap-2">
                    <Badge label={a.level} tone={a.level === "warning" ? "warn" : a.level === "critical" ? "bad" : "default"} />
                    <span className={a.is_read ? "text-text-dim" : "font-medium"}>{a.title}</span>
                    {!a.is_read && (
                      <button onClick={() => markRead(a.id)} className="ml-auto text-xs text-accent underline">mark read</button>
                    )}
                  </div>
                  {a.message && <div className="text-xs text-text-dim mt-1">{a.message}</div>}
                </li>
              ))}
              {!alertsTab && unread === 0 && alerts.length > 0 && (
                <li className="py-2 text-xs text-text-dim">All caught up.</li>
              )}
            </ul>
          )}
        </Card>
      </div>

      <Card title="Live deployment" className="mb-6">
        <p className="text-sm text-text-dim leading-relaxed mb-4">
          Requesting live deployment creates a <strong>pending review</strong> record. Approval requires an active risk
          profile, a risk acknowledgment, and at least the minimum paper-trade track record. Approval only marks the
          record — no real order path exists without a non-sandbox broker adapter.
        </p>
        <div className="flex items-end gap-3 mb-5">
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

        {deployments.length === 0 ? (
          <p className="text-sm text-text-dim">No deployment requests yet.</p>
        ) : (
          <ul className="divide-y divide-border">
            {deployments.map((d) => {
              const track = d.checks?.paper_track_record;
              const ok = track?.passed ?? false;
              return (
                <li key={d.id} className="py-2 text-sm">
                  <div className="flex items-center justify-between gap-3">
                    <div className="min-w-0">
                      <span className="font-medium">{d.strategy_name || d.strategy_id.slice(0, 8)}</span>
                      <span className="text-xs text-text-dim ml-2">via {d.broker_label || "?"}</span>
                    </div>
                    <Badge
                      label={d.status}
                      tone={d.status === "approved_sandbox_only" || d.status === "approved" ? "good" : d.status === "rejected" || d.status === "disabled" ? "bad" : d.status === "blocked" ? "warn" : "default"}
                    />
                  </div>
                  <div className="text-xs text-text-dim mt-1">
                    Track record: {track ? `${track.closed_trades ?? 0}/${track.required_min_trades ?? "?"} trades (${ok ? "pass" : "fail"})` : "not evaluated"} · created {new Date(d.created_at).toLocaleDateString()}
                  </div>
                  {d.status === "pending_review" && (
                    <button onClick={() => approveDeployment(d.id)} className="mt-2 text-xs text-accent underline">
                      approve
                    </button>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </Card>

      {result && <div className="text-xs text-accent mb-4">{result}</div>}
      {error && <div className="text-xs text-danger mb-4">{error}</div>}

      <Card title="About">
        <p className="text-sm text-text-dim leading-relaxed">
          FX Scalper Lab is an educational research & paper-trading platform for forex scalping hypotheses.
          Backtested and simulated results are not indicative of future performance. Read more in{" "}
          <Link className="text-accent underline" href="/docs/DISCLAIMER" target="_blank">DISCLAIMER</Link>.
        </p>
        <p className="text-xs text-text-dim mt-3">API endpoints are documented at <code className="text-accent">/docs</code> on the backend.</p>
      </Card>
    </div>
  );
}