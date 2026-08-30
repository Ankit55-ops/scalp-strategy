"use client";

import Link from "next/link";
import { FormEvent, useEffect, useState } from "react";
import {
  ApiError,
  connectExness,
  disconnectExness,
  getExnessInstruments,
  getExnessStatusCard,
  issueExnessPairing,
  testExnessConnection,
  tokenStore,
  verifyExnessGateway,
} from "@/lib/api";
import { Badge, Card, SectionTitle } from "@/components/ui";
import { ProviderConnectionStatusCard } from "@/components/ProviderConnectionStatusCard";
import type {
  ExnessCapabilityReport,
  InstrumentMappingView,
  PairingTokenOut,
  ProviderConnectionStatusCard as CardData,
} from "@/types";

const INPUT =
  "w-full rounded-lg border border-border bg-bg px-3 py-2 text-sm outline-none focus:border-accent";
const MODES = [
  { value: "server_side_mt5", label: "Server-side MT5" },
  { value: "gateway_agent", label: "Read-only gateway agent (advanced)" },
];

export default function ProvidersPage() {
  const [card, setCard] = useState<CardData | null>(null);
  const [instruments, setInstruments] = useState<InstrumentMappingView[]>([]);
  const [mode, setMode] = useState<"server_side_mt5" | "gateway_agent">("server_side_mt5");
  const [env, setEnv] = useState<"demo" | "real">("demo");
  const [login, setLogin] = useState("");
  const [password, setPassword] = useState("");
  const [server, setServer] = useState("Exness-MT5Trial");
  const [gatewayUrl, setGatewayUrl] = useState("");
  const [deviceName, setDeviceName] = useState("my-trading-machine");
  const [pairingCode, setPairingCode] = useState("");
  const [gatewayId, setGatewayId] = useState("");
  const [pairingToken, setPairingToken] = useState("");
  const [issued, setIssued] = useState<PairingTokenOut | null>(null);
  const [capability, setCapability] = useState<ExnessCapabilityReport | null>(null);
  const [busy, setBusy] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const token = tokenStore.get() as string;

  async function load() {
    try {
      setCard(await getExnessStatusCard(token));
    } catch {
      /* keep previous card */
    }
    try {
      setInstruments(await getExnessInstruments(token));
    } catch {
      /* no instruments yet */
    }
  }

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function onTest(e: FormEvent) {
    e.preventDefault();
    setBusy("test");
    setCapability(null);
    setMsg(null);
    setError(null);
    try {
      const report = await testExnessConnection(token, {
        mode: mode === "gateway_agent" ? "gateway" : "server_side",
        environment: env,
        login: login || undefined,
        password: password || undefined,
        server: server || undefined,
        gateway_url: mode === "gateway_agent" ? gatewayUrl || undefined : undefined,
        pairing_code: mode === "gateway_agent" ? pairingCode || undefined : undefined,
        device_name: mode === "gateway_agent" ? deviceName || undefined : undefined,
      });
      setCapability(report);
      setMsg(`Test connection: ${report.connection_status} (${report.account_label || report.account_environment})`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "test connection failed");
    } finally {
      setBusy("");
    }
  }

  async function onConnect(e: FormEvent) {
    e.preventDefault();
    setBusy("connect");
    setMsg(null);
    setError(null);
    try {
      const out = await connectExness(token, {
        connection_mode: mode === "gateway_agent" ? "mt5_gateway_agent" : "server_side_mt5",
        display_name: "My MT5",
        environment: env,
        login: login || undefined,
        password: password || undefined,
        server: server || undefined,
        use_read_only: true,
        gateway_url: mode === "gateway_agent" ? gatewayUrl || undefined : undefined,
        pairing_code: mode === "gateway_agent" ? pairingCode || undefined : undefined,
        device_name: mode === "gateway_agent" ? deviceName || undefined : undefined,
        confirm_read_only: true,
        idempotency_key: `conn-${Date.now()}`,
      });
      setMsg(`Connected (${out.connection.status}). Live trading stays disabled; data is read-only.`);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "connect failed");
    } finally {
      setBusy("");
    }
  }

  async function onPair() {
    if (!gatewayUrl || !deviceName) {
      setError("Enter the gateway device URL and device name.");
      return;
    }
    setBusy("pair");
    setMsg(null);
    setError(null);
    try {
      const out = await issueExnessPairing(token, {
        gateway_url: gatewayUrl,
        device_name: deviceName,
        pairing_code: pairingCode || undefined,
        idempotency_key: `pair-${Date.now()}`,
      });
      setIssued(out);
      setGatewayId(out.gateway_id);
      setPairingToken(out.pairing_token);
      setMsg("Token issued (short-lived). Paste it into the gateway agent to pair, then verify.");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "pairing failed");
    } finally {
      setBusy("");
    }
  }

  async function onVerify() {
    if (!gatewayId || !pairingToken) {
      setError("Issue a pairing token first.");
      return;
    }
    setBusy("verify");
    setMsg(null);
    setError(null);
    try {
      const out = await verifyExnessGateway(token, gatewayId, pairingToken);
      setMsg(`Gateway status: ${out.status ?? out.detail ?? "verified"}. Run Test connection, then Connect.`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "verify failed");
    } finally {
      setBusy("");
    }
  }

  async function onDisconnect() {
    setBusy("dc");
    setMsg(null);
    setError(null);
    try {
      const out = await disconnectExness(token, {});
      setMsg(out.disconnected ? "Disconnected. Credentials were destroyed server-side." : out.detail || "disconnect");
      setCapability(null);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "disconnect failed");
    } finally {
      setBusy("");
    }
  }

  function capBadge(key: string, value: string) {
    const tone = value === "available" ? "good" : value === "disabled" ? "warn" : "default";
    return <Badge key={key} label={`${key.replace(/_/g, " ")}: ${value}`} tone={tone} />;
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <SectionTitle>Data providers</SectionTitle>
        <Link href="/settings" className="text-sm text-accent hover:underline">
          ← Settings
        </Link>
      </div>

      <ProviderConnectionStatusCard card={card} busy={busy === "connect"} />

      <div className="grid lg:grid-cols-2 gap-4 mt-4">
        <Card title="Connect Exness via MetaTrader 5">
          <form onSubmit={onTest} className="space-y-3 text-sm">
            <div>
              <span className="text-xs text-text-dim block mb-1">Connection mode</span>
              <div className="flex gap-2">
                {MODES.map((m) => (
                  <button
                    key={m.value}
                    type="button"
                    onClick={() => setMode(m.value as never)}
                    className={`px-3 py-1.5 rounded-lg text-xs ${
                      mode === m.value ? "bg-accent/15 text-accent" : "text-text-dim hover:text-text"
                    }`}
                  >
                    {m.label}
                  </button>
                ))}
              </div>
            </div>

            <div className="grid grid-cols-2 gap-3">
              <label>
                <span className="text-xs text-text-dim block mb-1">Account</span>
                <select value={env} onChange={(e) => setEnv(e.target.value as "demo" | "real")} className={INPUT}>
                  <option value="demo">Demo</option>
                  <option value="real">Real</option>
                </select>
              </label>
              <label>
                <span className="text-xs text-text-dim block mb-1">Server</span>
                <input value={server} onChange={(e) => setServer(e.target.value)} className={INPUT} />
              </label>
              <label>
                <span className="text-xs text-text-dim block mb-1">MT5 login</span>
                <input
                  value={login}
                  onChange={(e) => setLogin(e.target.value)}
                  className={INPUT}
                  disabled={mode === "gateway_agent"}
                />
              </label>
              <label>
                <span className="text-xs text-text-dim block mb-1">MT5 password (encrypted at rest)</span>
                <input
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  className={INPUT}
                  disabled={mode === "gateway_agent"}
                />
              </label>
            </div>

            {mode === "gateway_agent" && (
              <>
                <label>
                  <span className="text-xs text-text-dim block mb-1">Gateway agent URL</span>
                  <input value={gatewayUrl} onChange={(e) => setGatewayUrl(e.target.value)} className={INPUT} placeholder="wss://gateway.example.local" />
                </label>
                <label>
                  <span className="text-xs text-text-dim block mb-1">Device name</span>
                  <input value={deviceName} onChange={(e) => setDeviceName(e.target.value)} className={INPUT} />
                </label>
                <label>
                  <span className="text-xs text-text-dim block mb-1">Pairing code (from the gateway agent)</span>
                  <input value={pairingCode} onChange={(e) => setPairingCode(e.target.value)} className={INPUT} />
                </label>
                <div className="flex gap-2">
                  <button
                    type="button"
                    onClick={onPair}
                    disabled={!!busy}
                    className="rounded-lg border border-border px-3 py-1.5 text-xs disabled:opacity-50"
                  >
                    {busy === "pair" ? "Issuing…" : "Issue pairing token"}
                  </button>
                  <button
                    type="button"
                    onClick={onVerify}
                    disabled={!!busy}
                    className="rounded-lg border border-border px-3 py-1.5 text-xs disabled:opacity-50"
                  >
                    {busy === "verify" ? "Verifying…" : "Verify gateway"}
                  </button>
                </div>
                {issued && (
                  <p className="text-xs text-text-dim">
                    Gateway {issued.gateway_id.slice(0, 8)}… token expires{" "}
                    {new Date(issued.expires_at_utc).toLocaleString()} UTC · paste into the gateway terminal.
                  </p>
                )}
              </>
            )}

            <div className="flex gap-2">
              <button
                className="rounded-lg border border-border px-4 py-2 font-medium text-sm disabled:opacity-50"
                disabled={!!busy}
              >
                {busy === "test" ? "Testing…" : "Test connection"}
              </button>
              <button
                onClick={onConnect}
                disabled={!!busy}
                className="rounded-lg bg-accent px-4 py-2 font-medium text-sm text-bg disabled:opacity-50"
              >
                {busy === "connect" ? "Connecting…" : "Connect (read-only)"}
              </button>
              {card?.connection_status === "CONNECTED" && (
                <button
                  type="button"
                  onClick={onDisconnect}
                  disabled={!!busy}
                  className="rounded-lg border border-danger/40 text-danger px-4 py-2 font-medium text-sm disabled:opacity-50"
                >
                  {busy === "dc" ? "Disconnecting…" : "Disconnect"}
                </button>
              )}
            </div>
          </form>

          {msg && <p className="text-xs text-accent mt-3">{msg}</p>}
          {error && <p className="text-xs text-danger mt-3">{error}</p>}

          <p className="text-xs text-text-dim mt-3">
            Credentials are encrypted server-side and never reach the browser. Live trading stays disabled; this
            connection is read-only for research and historical data.
          </p>
        </Card>

        <div className="space-y-4">
          {capability && (
            <Card title="Capability report">
              <div className="flex gap-2 flex-wrap mb-2">
                <Badge label={`${capability.connection_status}`} tone={capability.connection_status === "CONNECTED" ? "good" : "warn"} />
                <Badge label={`${capability.account_environment}`} />
                <Badge label={`${capability.instrument_count} instruments`} />
                <Badge label={`latency ${capability.latency_ms ?? "n/a"} ms`} />
                {capability.account_label && <Badge label={capability.account_label} tone="accent" />}
              </div>
              <div className="flex gap-1.5 flex-wrap">
                {Object.entries(capability.capabilities).map(([k, v]) => capBadge(k, v))}
              </div>
              <p className="text-xs text-text-dim mt-3">
                Quotes: {capability.quote_availability} · Historical data: {capability.historical_data_available ? "yes" : "no"} · Live trading: {capability.live_trading_status}
                {capability.detail ? ` · ${capability.detail}` : ""}
              </p>
            </Card>
          )}

          <Card title={`Discovered instruments (${instruments.length})`}>
            {instruments.length === 0 ? (
              <p className="text-sm text-text-dim">Connect a provider to discover symbols.</p>
            ) : (
              <div className="flex gap-1.5 flex-wrap">
                {instruments.map((i) => (
                  <Badge key={i.provider_symbol} label={`${i.provider_symbol} → ${i.canonical_symbol}`} tone="accent" />
                ))}
              </div>
            )}
            <p className="text-xs text-text-dim mt-3">
              Symbol discovery is server-side and mapped against the strategy spec. Real instrument metadata arrives
              from your broker connection.
            </p>
          </Card>
        </div>
      </div>
    </div>
  );
}