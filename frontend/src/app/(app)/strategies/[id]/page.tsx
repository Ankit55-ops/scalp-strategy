"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { api, tokenStore } from "@/lib/api";
import { Badge, Card, SectionTitle, Spinner } from "@/components/ui";
import type { Spec } from "@/types";

type Detail = {
  id: string;
  name: string;
  strategy_family: string;
  current_version: string;
  status: string;
  spec: Spec;
};

export default function StrategyDetailPage() {
  const { id } = useParams<{ id: string }>();
  const [detail, setDetail] = useState<Detail | null>(null);

  useEffect(() => {
    api<Detail>(`/strategies/${id}`, { token: tokenStore.get() })
      .then(setDetail)
      .catch(() => setDetail(null));
  }, [id]);

  if (!detail) return <Spinner />;
  const spec = detail.spec;

  return (
    <div>
      <SectionTitle>{detail.name}</SectionTitle>
      <div className="flex gap-2 mb-4">
        <Badge label={detail.strategy_family} tone="accent" />
        <Badge label={`v${detail.current_version}`} />
        <Badge label={detail.status} tone="good" />
      </div>

      <div className="grid lg:grid-cols-2 gap-4 mb-4">
        <Card title="Explanation">
          <p className="text-sm leading-relaxed">{spec.plain_english_explanation || "No plain-English explanation provided."}</p>
          {spec.confidence_notes && <p className="text-xs text-text-dim mt-2">{spec.confidence_notes}</p>}
        </Card>
        <Card title="Execution profile">
          <dl className="text-sm space-y-1.5">
            <Row k="Pairs" v={spec.supported_pairs.join(", ")} />
            <Row k="Timeframes" v={spec.supported_timeframes.join(", ")} />
            <Row k="Sessions" v={spec.sessions_utc.map((s) => `${s.name} ${s.start}-${s.end}`).join(", ")} />
            <Row k="Risk/trade" v={`${spec.risk_management.risk_per_trade_pct}%`} />
            <Row k="Daily loss cap" v={`${spec.risk_management.max_daily_loss_pct}%`} />
            <Row k="Stop method" v={`${spec.risk_management.stop_loss_method} ${JSON.stringify(spec.risk_management.stop_loss_parameters)}`} />
            <Row k="Max trades/day" v={String(spec.risk_management.max_trades_per_day)} />
          </dl>
        </Card>
      </div>

      <div className="grid lg:grid-cols-2 gap-4">
        <Card title="Entry rules">
          <ul className="space-y-2">
            {spec.entry_rules.map((r) => (
              <li key={r.id} className="text-sm">
                <div className="text-text-dim">{r.description || r.id}</div>
                <code className="text-accent text-xs block mt-0.5">{r.expression}</code>
              </li>
            ))}
          </ul>
        </Card>
        <Card title="Exit rules">
          <ul className="space-y-2">
            {spec.exit_rules.map((r) => (
              <li key={r.id} className="text-sm">
                <div className="text-text-dim">{r.description || r.id}</div>
                <code className="text-accent text-xs block mt-0.5">{r.expression}</code>
              </li>
            ))}
          </ul>
        </Card>
      </div>

      {spec.assumptions.length > 0 && (
        <Card title="Assumptions" className="mt-4">
          <ul className="list-disc pl-5 text-sm space-y-1">
            {spec.assumptions.map((a, i) => (
              <li key={i}>{a}</li>
            ))}
          </ul>
        </Card>
      )}
      {spec.failure_modes.length > 0 && (
        <Card title="Failure modes" className="mt-4">
          <ul className="list-disc pl-5 text-sm space-y-1">
            {spec.failure_modes.map((f, i) => (
              <li key={i}>{f}</li>
            ))}
          </ul>
        </Card>
      )}
    </div>
  );
}

function Row({ k, v }: { k: string; v: string }) {
  return (
    <div className="flex justify-between gap-4">
      <dt className="text-text-dim">{k}</dt>
      <dd className="text-right">{v}</dd>
    </div>
  );
}