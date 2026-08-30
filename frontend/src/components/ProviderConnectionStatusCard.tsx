"use client";

import { Card, Badge, Stat } from "./ui";
import type { ProviderConnectionStatusCard as CardData } from "@/types";

export function ProviderConnectionStatusCard({
  card,
  onConnect,
  busy = false,
}: {
  card: CardData | null;
  onConnect?: () => void;
  busy?: boolean;
}) {
  if (!card) {
    return (
      <Card title="Provider status">
        <p className="text-sm text-text-dim">Loading provider status…</p>
      </Card>
    );
  }

  const statusTone =
    card.connection_status === "CONNECTED"
      ? "good"
      : card.connection_status === "CONNECTING" || card.connection_status === "AUTHENTICATING"
      ? "warn"
      : card.connection_status === "NOT_CONFIGURED"
      ? "default"
      : "bad";

  const envLabel =
    card.account_type === "demo"
      ? "Demo account"
      : card.account_type === "real"
      ? "Real account"
      : "Not connected";

  return (
    <Card title="Provider status">
      <div className="flex items-start justify-between gap-3 flex-wrap">
        <div>
          <div className="flex items-center gap-2 flex-wrap">
            <Badge label={card.connection_status.replace(/_/g, " ")} tone={statusTone} />
            {card.display_name && <span className="text-sm text-text">{card.display_name}</span>}
          </div>
          <p className="text-sm text-text-dim mt-1">{card.message}</p>
          <p className="text-xs text-text-dim mt-1">
            {card.selected_provider} · {envLabel}
            {card.connection_mode === "gateway_agent" ? " via read-only MT5 gateway agent" : " · server-side MT5"}
          </p>
        </div>
        {card.show_connect_button && (
          <button
            onClick={onConnect}
            disabled={busy}
            className="rounded-lg bg-accent px-3 py-1.5 text-sm font-medium text-bg hover:opacity-90 disabled:opacity-50"
          >
            {busy ? "Connecting…" : "Connect"}
          </button>
        )}
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mt-4 text-sm">
        <Stat label="Health" value={card.feed_health ?? "n/a"} tone={card.feed_health === "healthy" ? "good" : "default"} />
        <Stat
          label="Latency"
          value={card.latency_ms != null ? `${card.latency_ms} ms` : "n/a"}
        />
        <Stat label="Instruments" value={card.instrument_count || card.active_symbol_count} />
        <Stat label="Live trading" value={card.live_trading_status} tone="warn" />
      </div>

      {card.active_symbols.length > 0 && (
        <div className="mt-3">
          <span className="text-xs text-text-dim mr-2">Active symbols</span>
          <div className="flex gap-1.5 flex-wrap mt-1">
            {card.active_symbols.map((s) => (
              <Badge key={s} label={s} tone="accent" />
            ))}
          </div>
        </div>
      )}

      {(card.available_capabilities.length > 0 || card.unavailable_capabilities.length > 0) && (
        <div className="mt-3">
          <span className="text-xs text-text-dim mr-2">Capabilities</span>
          <div className="flex gap-1.5 flex-wrap mt-1">
            {card.available_capabilities.map((c) => (
              <Badge key={c} label={c.replace(/_/g, " ")} />
            ))}
            {card.unavailable_capabilities.map((c) => (
              <Badge key={c} label={`${c.replace(/_/g, " ")} · unavailable`} tone="warn" />
            ))}
          </div>
        </div>
      )}

      {card.last_successful_data_utc && (
        <p className="text-xs text-text-dim mt-3">
          Last successful data:{" "}
          {new Date(card.last_successful_data_utc).toLocaleString()} UTC
        </p>
      )}
    </Card>
  );
}