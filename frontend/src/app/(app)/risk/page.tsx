"use client";

import { FormEvent, useEffect, useState } from "react";
import { api, ApiError, tokenStore } from "@/lib/api";
import { Badge, Card, SectionTitle, Spinner } from "@/components/ui";
import type { RiskProfile } from "@/types";

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

type KSRow = {
  id: string;
  scope: string;
  resource_id: string;
  reason: string | null;
  created_at: string;
};

export default function RiskPage() {
  const [ks, setKs] = useState<KSStatus | null>(null);
  const [engagements, setEngagements] = useState<KSRow[]>([]);
  const [profiles, setProfiles] = useState<RiskProfile[]>([]);
  const [events, setEvents] = useState<RiskEvent[]>([]);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function load() {
    try {
      setKs(await api<KSStatus>("/risk/kill-switch", { token: tokenStore.get() }));
    } catch {
      setKs({ global: false, strategy: {}, pair: {} });
    }
    try {
      setEngagements(await api<KSRow[]>("/risk/kill-switch/engagements", { token: tokenStore.get() }));
    } catch {
      setEngagements([]);
    }
    try {
      setProfiles(await api<RiskProfile[]>("/risk/profiles", { token: tokenStore.get() }));
    } catch {
      setProfiles([]);
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

  async function toggle(scope: "global" | "strategy" | "pair", resourceId: string, enabled: boolean) {
    setMessage(null);
    setError(null);
    try {
      await api("/risk/kill-switch", {
        method: "POST",
        token: tokenStore.get(),
        body: { scope, resource_id: resourceId, enabled, reason: "Changed from Risk Center" },
      });
      await load();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "kill switch update failed");
    }
  }

  if (!ks) return <Spinner />;

  const active = profiles.find((p) => p.is_active);

  return (
    <div>
      <SectionTitle>Risk Center</SectionTitle>
      {message && <div className="text-sm text-accent mb-4">{message}</div>}
      {error && <div className="text-sm text-danger mb-4">{error}</div>}

      <div className="grid lg:grid-cols-2 gap-4 mb-6">
        <Card title="Kill switches">
          <p className="text-xs text-text-dim mb-4">
            Kill switches halt <strong>new entries</strong> immediately at the risk engine. Engaged switches
            persist in the database (per workspace) until explicitly disarmed by an operator or an automated
            monitor. Existing stops/targets still manage open positions.
          </p>
          <div className="space-y-2">
            <SwitchRow
              label="Global (all strategies)"
              enabled={ks.global}
              onToggle={() => toggle("global", "global", !ks.global)}
            />
            {Object.entries(ks.strategy).map(([id]) => (
              <SwitchRow key={`strategy-${id}`} label={`Strategy ${id.slice(0, 8)}`} enabled={true} onToggle={() => toggle("strategy", id, false)} />
            ))}
            {Object.entries(ks.pair).map(([sym]) => (
              <SwitchRow key={`pair-${sym}`} label={`Pair ${sym}`} enabled={true} onToggle={() => toggle("pair", sym, false)} />
            ))}
          </div>
          {engagements.length === 0 ? (
            <p className="text-xs text-text-dim mt-3">No switches engaged — trading is armed.</p>
          ) : (
            <ul className="text-xs text-text-dim mt-3 space-y-1">
              {engagements.map((e) => (
                <li key={e.id}>
                  {e.scope} · {e.resource_id}
                  {e.reason ? ` — ${e.reason}` : ""}
                </li>
              ))}
            </ul>
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
                        tone={e.severity === "critical" ? "bad" : e.severity === "warning" ? "warn" : "default"}
                      />
                    </td>
                    <td className="py-2 pr-3">{e.symbol ?? "—"}</td>
                    <td className="py-2 text-text-dim truncate max-w-xs">
                      {typeof e.details === "string" ? e.details : JSON.stringify(e.details ?? {})}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>
      </div>

      <RiskProfiles
        profiles={profiles}
        active={active}
        onChanged={load}
        onMessage={setMessage}
        onError={setError}
      />
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

const FIELDS: { key: keyof RiskProfile; label: string; step: number }[] = [
  { key: "risk_per_trade_pct", label: "Risk / trade %", step: 0.05 },
  { key: "max_daily_loss_pct", label: "Max daily loss %", step: 0.1 },
  { key: "max_weekly_loss_pct", label: "Max weekly loss %", step: 0.1 },
  { key: "max_drawdown_pct", label: "Max drawdown %", step: 0.5 },
  { key: "max_consecutive_losses", label: "Max consecutive losses", step: 1 },
  { key: "max_open_positions", label: "Max open positions", step: 1 },
  { key: "max_trades_per_day", label: "Max trades / day", step: 1 },
  { key: "max_spread_pips", label: "Max spread pips", step: 0.1 },
];

function RiskProfiles({
  profiles,
  active,
  onChanged,
  onMessage,
  onError,
}: {
  profiles: RiskProfile[];
  active: RiskProfile | undefined;
  onChanged: () => Promise<void>;
  onMessage: (m: string) => void;
  onError: (e: string) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<Record<string, number>>({});
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");

  async function op(call: () => Promise<unknown>, ok: string) {
    onError("");
    try {
      await call();
      onMessage(ok);
      await onChanged();
    } catch (e) {
      onError(e instanceof ApiError ? e.message : "operation failed");
    }
  }

  function beginEdit(p: RiskProfile) {
    setEditing(true);
    setDraft(Object.fromEntries(FIELDS.map((f) => [f.key, p[f.key] as number])));
  }

  async function save() {
    const body = Object.fromEntries(Object.entries(draft).map(([k, v]) => [k, Number(v)]));
    await op(
      () => api(`/risk/profiles/${active!.id}`, { method: "PATCH", token: tokenStore.get(), body }),
      "Risk profile updated. It is now enforced on the next order.",
    );
    setEditing(false);
  }

  async function create(e: FormEvent) {
    e.preventDefault();
    await op(
      () =>
        api("/risk/profiles", {
          method: "POST",
          token: tokenStore.get(),
          body: { name, is_active: profiles.length === 0, ...Object.fromEntries(FIELDS.map((f) => [f.key, Number(draft[f.key] ?? 0.25)])) },
        }),
      "Risk profile created.",
    );
    setCreating(false);
    setName("");
    setDraft({});
  }

  async function activate(id: string, n: string) {
    await op(
      () => api(`/risk/profiles/${id}/activate`, { method: "POST", token: tokenStore.get() }),
      `"${n}" is now the active risk profile.`,
    );
  }

  async function remove(id: string, n: string) {
    await op(
      () => api(`/risk/profiles/${id}`, { method: "DELETE", token: tokenStore.get() }),
      `"${n}" deleted.`,
    );
  }

  return (
    <Card title="Risk profiles" className="mb-6">
      <p className="text-xs text-text-dim mb-4">
        The <strong>active</strong> risk profile is enforced by the engine on every order. Create fully-configured
        profiles, then activate one. Orders are rejected with &quot;no risk profile&quot; until one exists.
      </p>

      {active && !editing && (
        <div className="rounded-lg border border-accent/40 bg-accent/5 px-4 py-3 mb-4">
          <div className="flex items-center justify-between mb-2">
            <div>
              <Badge label="active" tone="accent" /> <span className="text-sm font-medium ml-2">{active.name}</span>
            </div>
            <button onClick={() => beginEdit(active)} className="text-xs text-accent underline">
              edit
            </button>
          </div>
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-y-1.5 text-xs text-text-dim">
            {FIELDS.map((f) => (
              <div key={f.key}>
                {f.label}: <span className="text-text">{active[f.key]}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {active && editing && (
        <div className="rounded-lg border border-warn/40 bg-warn/5 px-4 py-3 mb-4">
          <div className="text-sm font-medium mb-2">Editing {active.name}</div>
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 mb-3">
            {FIELDS.map((f) => (
              <label key={f.key} className="block">
                <span className="text-xs text-text-dim block mb-1">{f.label}</span>
                <input
                  type="number"
                  step={f.step}
                  value={draft[f.key] ?? ""}
                  onChange={(e) => setDraft({ ...draft, [f.key]: Number(e.target.value) })}
                  className="w-full rounded-lg border border-border bg-bg px-3 py-1.5 text-sm"
                />
              </label>
            ))}
          </div>
          <div className="flex gap-2">
            <button onClick={save} className="rounded-lg bg-accent/90 text-bg px-3 py-1.5 text-xs font-semibold hover:bg-accent">
              Save profile
            </button>
            <button onClick={() => setEditing(false)} className="rounded-lg border border-border px-3 py-1.5 text-xs">
              Cancel
            </button>
          </div>
        </div>
      )}

      {!creating ? (
        <button onClick={() => { setCreating(true); setDraft({ risk_per_trade_pct: 0.25, max_daily_loss_pct: 1, max_weekly_loss_pct: 3, max_drawdown_pct: 10, max_consecutive_losses: 3, max_open_positions: 1, max_trades_per_day: 5, max_spread_pips: 1.2 }); }} className="mb-3 rounded-lg border border-border px-3 py-1.5 text-xs">
          + New risk profile
        </button>
      ) : (
        <form onSubmit={create} className="rounded-lg border border-border px-4 py-3 mb-3">
          <label className="block mb-2">
            <span className="text-xs text-text-dim block mb-1">Name</span>
            <input value={name} onChange={(e) => setName(e.target.value)} required className="w-full rounded-lg border border-border bg-bg px-3 py-1.5 text-sm" />
          </label>
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 mb-3">
            {FIELDS.map((f) => (
              <label key={f.key} className="block">
                <span className="text-xs text-text-dim block mb-1">{f.label}</span>
                <input
                  type="number"
                  step={f.step}
                  value={draft[f.key] ?? ""}
                  onChange={(e) => setDraft({ ...draft, [f.key]: Number(e.target.value) })}
                  className="w-full rounded-lg border border-border bg-bg px-3 py-1.5 text-sm"
                />
              </label>
            ))}
          </div>
          <button className="rounded-lg bg-accent/90 text-bg px-3 py-1.5 text-xs font-semibold hover:bg-accent">Create profile</button>
        </form>
      )}

      {profiles.length === 0 ? (
        <p className="text-sm text-text-dim">No risk profiles yet — create one to unlock order approval.</p>
      ) : (
        <ul className="divide-y divide-border">
          {profiles.map((p) => (
            <li key={p.id} className="py-2 flex items-center justify-between gap-3 text-sm">
              <div>
                <span className="font-medium">{p.name}</span>
                <span className="text-xs text-text-dim ml-2">
                  {p.risk_per_trade_pct}%/trade · {p.max_open_positions} pos · {p.max_trades_per_day}/day
                </span>
              </div>
              <div className="flex items-center gap-3 shrink-0">
                {p.is_active ? (
                  <Badge label="active" tone="accent" />
                ) : (
                  <>
                    <button onClick={() => activate(p.id, p.name)} className="text-xs text-accent underline">activate</button>
                    <button onClick={() => remove(p.id, p.name)} className="text-xs text-danger underline">delete</button>
                  </>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}