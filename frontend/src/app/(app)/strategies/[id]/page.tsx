"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { api, ApiError, tokenStore } from "@/lib/api";
import { Badge, Card, SectionTitle, Spinner } from "@/components/ui";
import type { Spec, StrategyCheckReport } from "@/types";

type Detail = {
  id: string;
  name: string;
  strategy_family: string;
  current_version: string;
  status: string;
  spec: Spec;
};

const SEV_TONE: Record<string, "bad" | "warn" | "good" | "default"> = {
  fail: "bad",
  warn: "warn",
  pass: "good",
  info: "default",
};

export default function StrategyDetailPage() {
  const { id } = useParams<{ id: string }>();
  const [detail, setDetail] = useState<Detail | null>(null);
  const [report, setReport] = useState<StrategyCheckReport | null>(null);
  const [checking, setChecking] = useState(false);
  const [checkError, setCheckError] = useState<string | null>(null);

  async function runCheck() {
    setChecking(true);
    setCheckError(null);
    try {
      setReport(await api<StrategyCheckReport>(`/strategies/${id}/check`, { method: "POST", token: tokenStore.get(), body: {} }));
    } catch (e) {
      setCheckError(e instanceof ApiError ? e.message : "check failed");
    } finally {
      setChecking(false);
    }
  }

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

      <Card title="Strategy check" className="mb-4">
        <div className="flex items-center gap-3">
          <button
            onClick={runCheck}
            disabled={checking}
            className="rounded-lg bg-accent/90 text-bg px-4 py-2 text-sm font-semibold hover:bg-accent disabled:opacity-50"
          >
            {checking ? "Checking…" : report ? "Re-check strategy" : "Check strategy"}
          </button>
          {report && (
            <span className="flex items-center gap-2 text-sm">
              Verdict <Badge label={report.overall} tone={SEV_TONE[report.overall]} />
              <span className="text-text-dim text-xs">{report.summary}</span>
            </span>
          )}
        </div>
        {checkError && <div className="text-xs text-danger mt-3">{checkError}</div>}
        {report && (
          <>
            <ul className="mt-4 divide-y divide-border">
              {report.checks.map((c, i) => (
                <li key={i} className="py-2 flex items-start gap-3 text-sm">
                  <Badge label={c.severity} tone={SEV_TONE[c.severity]} />
                  <code className="text-text-dim text-xs mt-0.5 shrink-0">{c.check}</code>
                  <span>{c.detail}</span>
                </li>
              ))}
            </ul>
            {report.intrabar && (
              <div className="mt-4 rounded-lg border border-border p-3">
                <div className="flex items-center gap-2 mb-2">
                  <span className="text-xs text-text-dim">Intrabar preview</span>
                  <Badge label={report.intrabar["signal-label"]} tone={report.intrabar.side ? "good" : "default"} />
                  <Badge label={report.intrabar.state} />
                </div>
                <p className="text-sm">
                  {report.intrabar.side
                    ? `Provisional ${report.intrabar.side.toUpperCase()} signal at ${report.intrabar.price} — confirm on candle close.`
                    : report.intrabar.detail || "No provisional signal on the forming candle."}
                </p>
                {report.intrabar.rule_ids.length > 0 && (
                  <p className="text-xs text-accent mt-1">rules: {report.intrabar.rule_ids.join(", ")}</p>
                )}
                {report.intrabar.blocked_reason && (
                  <p className="text-xs text-danger mt-1">{report.intrabar.blocked_reason}</p>
                )}
              </div>
            )}
          </>
        )}
        <p className="text-xs text-text-dim mt-3">
          Runs offline static checks: DSL syntax, tautologies, exit-vs-entry, risk-parameter sanity, data
          availability, a review of the latest completed backtest (if any), and a provisional intrabar
          signal preview against the live feed.
        </p>
      </Card>

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