import { ReactNode } from "react";

export function Card({
  title,
  children,
  className = "",
}: {
  title?: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <div className={`rounded-xl border border-border bg-panel p-4 ${className}`}>
      {title && <h3 className="text-sm font-medium mb-3 text-text-dim uppercase tracking-wide">{title}</h3>}
      {children}
    </div>
  );
}

export function Stat({
  label,
  value,
  tone = "default",
}: {
  label: string;
  value: ReactNode;
  tone?: "default" | "good" | "bad" | "warn";
}) {
  const toneClass =
    tone === "good"
      ? "text-accent"
      : tone === "bad"
      ? "text-danger"
      : tone === "warn"
      ? "text-warn"
      : "text-text";
  return (
    <div className="rounded-xl border border-border bg-panel px-4 py-3">
      <div className="text-xs text-text-dim">{label}</div>
      <div className={`text-2xl font-semibold mt-1 ${toneClass}`}>{value}</div>
    </div>
  );
}

export function Badge({ label, tone = "default" }: { label: string; tone?: "default" | "good" | "bad" | "warn" | "accent" }) {
  const cls =
    tone === "good"
      ? "bg-accent/10 text-accent border-accent/30"
      : tone === "bad"
      ? "bg-danger/10 text-danger border-danger/30"
      : tone === "warn"
      ? "bg-warn/10 text-warn border-warn/30"
      : tone === "accent"
      ? "bg-accent/10 text-accent border-accent/30"
      : "bg-panel2 text-text-dim border-border";
  return <span className={`inline-flex items-center rounded-full border px-2 py-0.5 text-xs ${cls}`}>{label}</span>;
}

export function SectionTitle({ children }: { children: ReactNode }) {
  return <h2 className="text-xl font-semibold mb-4">{children}</h2>;
}

export function Spinner() {
  return <div className="text-sm text-text-dim animate-pulse">Loading…</div>;
}