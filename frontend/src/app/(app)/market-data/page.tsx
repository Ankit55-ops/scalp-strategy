"use client";

import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import { api, ApiError, tokenStore } from "@/lib/api";
import { Badge, Card, SectionTitle, Spinner } from "@/components/ui";
import CandlestickChart from "@/components/chart/CandlestickChart";
import type {
  CandleResponse,
  CandleView,
  FeedHealthRow,
  InstrumentView,
  LiveCandleStreamEvent,
  LiveQuoteStreamEvent,
  MarketDataStreamEvent,
  ProviderStatus,
  QuoteView,
} from "@/types";

const FEED_TONE: Record<string, "good" | "bad" | "warn" | "default"> = {
  LIVE: "good",
  DEGRADED: "warn",
  STALE: "bad",
  DISCONNECTED: "bad",
  RATE_LIMITED: "bad",
  MAINTENANCE: "warn",
  CONNECTING: "default",
};

const HEALTH_TONE: Record<string, "good" | "bad" | "default"> = {
  ok: "good",
};

const TIMEFRAMES = ["M1", "M5", "M15", "M30", "H1", "H4"];
const TF_SECONDS: Record<string, number> = { M1: 60, M5: 300, M15: 900, M30: 1800, H1: 3600, H4: 14400 };

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api";

function marketDataSocketUrl(): string {
  const base = API_URL.replace(/^http/, "ws");
  return `${base}/ws/market-data`;
}

export default function MarketDataPage() {
  const [status, setStatus] = useState<ProviderStatus | null>(null);
  const [instruments, setInstruments] = useState<InstrumentView[]>([]);
  const [feed, setFeed] = useState<FeedHealthRow[]>([]);
  const [quote, setQuote] = useState<QuoteView | null>(null);
  const [qlookup, setQlookup] = useState("EURUSD");
  const [ingestion, setIngestion] = useState<{ running: boolean; provider: string }>({ running: false, provider: "unknown" });
  const [streaming, setStreaming] = useState(false);
  const [broadcast, setBroadcast] = useState("");

  // chart state
  const [chartSymbol, setChartSymbol] = useState("EURUSD");
  const [timeframe, setTimeframe] = useState("M5");
  const [candles, setCandles] = useState<CandleView[]>([]);
  const [chartSource, setChartSource] = useState("");
  const [lastPrice, setLastPrice] = useState<number | null>(null);
  const [chartBusy, setChartBusy] = useState(false);
  const [chartError, setChartError] = useState<string | null>(null);

  // connect form
  const [cProvider, setCProvider] = useState("oanda");
  const [cKey, setCKey] = useState("");
  const [cAccount, setCAccount] = useState("");
  const [cEnv, setCEnv] = useState("practice");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const candlesRef = useRef<CandleView[]>([]);
  const chartSymbolRef = useRef(chartSymbol);
  const timeframeRef = useRef(timeframe);
  const wsRef = useRef<WebSocket | null>(null);
  const lastRefetchTs = useRef(0);
  candlesRef.current = candles;
  chartSymbolRef.current = chartSymbol;
  timeframeRef.current = timeframe;

  async function load() {
    await Promise.all([
      api<ProviderStatus>("/market-data/providers/status", { token: tokenStore.get() }).then(setStatus).catch(() => {}),
      api<InstrumentView[]>("/market-data/instruments", { token: tokenStore.get() }).then(setInstruments).catch(() => {}),
      api<FeedHealthRow[]>("/market-data/feed-health", { token: tokenStore.get() }).then(setFeed).catch(() => {}),
    ]);
  }

  useEffect(() => {
    load();
  }, []);

  async function fetchCandles() {
    setChartBusy(true);
    setChartError(null);
    try {
      const sym = chartSymbol.replace("/", "").replace("_", "");
      const res = await api<CandleResponse>(`/market-data/candles/${sym}?timeframe=${timeframe}`, { token: tokenStore.get() });
      candlesRef.current = res.candles;
      setCandles(res.candles);
      setChartSource(`provider ${res.provider} · ${res.count} bars · ${res.start.slice(0, 10)} → ${res.end.slice(0, 10)}`);
    } catch (err) {
      setChartError(err instanceof ApiError ? err.message : "candles unavailable");
    } finally {
      setChartBusy(false);
    }
  }

  useEffect(() => {
    fetchCandles();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chartSymbol, timeframe]);

  // fold a live quote into the in-progress candle
  function foldLiveQuote(ev: LiveQuoteStreamEvent) {
    const sym = ev.symbol.toUpperCase();
    if (sym !== chartSymbolRef.current.toUpperCase()) return;
    const price = ev.mid;
    if (typeof price !== "number" || !isFinite(price)) return;
    setLastPrice(price);
    const list = candlesRef.current;
    if (!list.length) return;
    const tfSec = TF_SECONDS[timeframeRef.current] ?? 300;
    const bucket = Math.floor(ev.ts / tfSec) * tfSec;
    const last = list[list.length - 1];
    if (bucket === last.ts) {
      const next = [...list];
      next[next.length - 1] = {
        ...last,
        high: Math.max(last.high, price),
        low: Math.min(last.low, price),
        close: price,
      };
      candlesRef.current = next;
      setCandles(next);
    } else if (bucket > last.ts) {
      // bar rolled over — refresh history once (debounced)
      const now = Date.now();
      if (now - lastRefetchTs.current > 10_000) {
        lastRefetchTs.current = now;
        fetchCandles();
      }
    }
  }

  function foldLiveCandle(ev: LiveCandleStreamEvent) {
    const sym = ev.symbol.toUpperCase();
    if (sym !== chartSymbolRef.current.toUpperCase() || ev.timeframe !== timeframeRef.current) return;
    setLastPrice(ev.close);
    const list = candlesRef.current;
    if (!list.length) return;
    let next: CandleView[] = list;
    const idx = list.findIndex((c) => c.ts === ev.ts);
    if (idx >= 0) {
      next = [...list];
      next[idx] = { ...next[idx], open: ev.open, high: ev.high, low: ev.low, close: ev.close, volume: ev.volume, is_complete: ev.is_complete };
    } else if (ev.ts > (list[list.length - 1]?.ts ?? 0)) {
      next = [...list, { symbol: ev.symbol, timeframe: ev.timeframe, ts: ev.ts, open: ev.open, high: ev.high, low: ev.low, close: ev.close, volume: ev.volume, source: "live", is_complete: ev.is_complete }];
      if (next.length > 300) next = next.slice(-300);
    }
    candlesRef.current = next;
    setCandles(next);
  }

  // WS market-data stream
  useEffect(() => {
    let disposed = false;
    let ws: WebSocket | null = null;
    let retry: ReturnType<typeof setTimeout> | null = null;

    function connect() {
      const token = tokenStore.get();
      if (!token || disposed) return;
      // JWT travels in the Sec-WebSocket-Protocol subprotocol (browsers cannot
      // set arbitrary headers on the upgrade request).
      ws = new WebSocket(marketDataSocketUrl(), token);
      wsRef.current = ws;
      ws.onopen = () => !disposed && setStreaming(true);
      ws.onmessage = (msg) => {
        if (disposed) return;
        try {
          const ev = JSON.parse(msg.data) as MarketDataStreamEvent;
          if (ev.type === "snapshot") {
            setIngestion(ev.data.ingestion);
            setBroadcast(`connected · ${ev.data.ingestion.running ? `ingesting via ${ev.data.ingestion.provider}` : "ingestion idle"}`);
            return;
          }
          if (ev.type === "quote") {
            foldLiveQuote(ev);
            return;
          }
          if (ev.type === "candle_update" || ev.type === "candle_close") {
            foldLiveCandle(ev);
            return;
          }
          if (ev.type === "feed_health") {
            setFeed((prev) => {
              const i = prev.findIndex((r) => r.symbol === ev.symbol && r.provider === ev.provider);
              const row = { symbol: ev.symbol, provider: ev.provider, feed_status: ev.feed_status, last_quote_ts: Date.now() / 1000, latency_ms: ev.latency_ms ?? null, last_error: null };
              if (i >= 0) {
                const next = [...prev];
                next[i] = row;
                return next;
              }
              return [...prev, row];
            });
          }
        } catch {
          /* ignore malformed frames */
        }
      };
      ws.onclose = () => {
        if (disposed) return;
        setStreaming(false);
        retry = setTimeout(connect, 3000);
      };
    }

    connect();
    return () => {
      disposed = true;
      if (retry) clearTimeout(retry);
      ws?.close();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function lookupQuote(e: FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      setQuote(await api<QuoteView>(`/market-data/quotes/${qlookup.replace("/", "").replace("_", "")}`, { token: tokenStore.get() }));
    } catch (err) {
      setQuote(null);
      setError(err instanceof ApiError ? err.message : "quote lookup failed");
    }
  }

  async function connect(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setMsg(null);
    setError(null);
    try {
      await api(`/market-data/providers/connect`, {
        method: "POST",
        token: tokenStore.get(),
        body: {
          provider: cProvider,
          api_key: cKey,
          account_id: cAccount || undefined,
          env: cEnv,
        },
      });
      setMsg("Connected. The provider is now the active source for this workspace.");
      setCKey("");
      await load();
      await fetchCandles();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "connect failed");
    } finally {
      setBusy(false);
    }
  }

  const chartCandles = useMemo(() => candles, [candles]);

  if (!status) return <Spinner />;

  const healthTone = HEALTH_TONE[status.health.status] ?? "bad";

  return (
    <div>
      <div className="flex items-center gap-3 mb-4">
        <SectionTitle>Market Data</SectionTitle>
        <Badge label={status.active_provider_label} tone="accent" />
        <Badge label={`basis: ${status.bid_ask_basis}`} />
        <Badge label={streaming ? "stream: live" : "stream: off"} tone={streaming ? "good" : "warn"} />
        {broadcast && <span className="text-xs text-text-dim">{broadcast}</span>}
        <button onClick={load} className="text-xs text-accent hover:underline">
          refresh
        </button>
      </div>

      <Card title="Live chart" className="mb-4">
        <div className="flex flex-wrap items-center gap-2 mb-3 text-sm">
          <input
            defaultValue={chartSymbol}
            onBlur={(e) => setChartSymbol(e.target.value.trim().toUpperCase() || "EURUSD")}
            onKeyDown={(e) => {
              if (e.key === "Enter") setChartSymbol((e.target as HTMLInputElement).value.trim().toUpperCase() || "EURUSD");
            }}
            className="w-32 rounded-lg border border-border bg-bg px-3 py-1.5 outline-none focus:border-accent"
            aria-label="Chart symbol"
          />
          <div className="flex gap-1">
            {TIMEFRAMES.map((tf) => (
              <button
                key={tf}
                onClick={() => setTimeframe(tf)}
                className={`px-2.5 py-1.5 rounded-lg border ${
                  timeframe === tf ? "border-accent text-accent bg-accent/10" : "border-border text-text-dim hover:text-text"
                }`}
              >
                {tf}
              </button>
            ))}
          </div>
          <button onClick={fetchCandles} className="text-xs text-accent hover:underline ml-1">
            {chartBusy ? "loading…" : "refresh"}
          </button>
        </div>
        {chartError && <p className="text-xs text-danger mb-2">{chartError}</p>}
        {chartCandles.length === 0 && !chartBusy && <p className="text-xs text-text-dim">No candles. Start ingestion or fetch a symbol’s history.</p>}
        <CandlestickChart candles={chartCandles} livePrice={lastPrice} />
        <p className="text-xs text-text-dim mt-2">{chartSource}{lastPrice != null ? ` · last ${lastPrice}` : ""}</p>
      </Card>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-4">
        <Card title="Provider status">
          <div className="grid grid-cols-2 gap-3 text-sm">
            <Stat label="Health" value={status.health.status} tone={healthTone} />
            <Stat label="Latency" value={status.health.latency_ms != null ? `${status.health.latency_ms} ms` : "n/a"} />
            <Stat label="Env selection" value={status.env_selected} />
            <Stat label="Stale threshold" value={`${status.stale_threshold_seconds}s`} />
            <Stat label="Ingestion" value={ingestion.running ? `on · ${ingestion.provider}` : "off"} tone={ingestion.running ? "good" : "default"} />
          </div>
          {status.health.detail && <p className="text-xs text-text-dim mt-2">{status.health.detail}</p>}
          <p className="text-xs text-danger mt-3">
            Live trading is disabled. Provider keys are encrypted server-side and never reach the browser.
          </p>
        </Card>

        <Card title="Connect a licensed provider">
          <form onSubmit={connect} className="space-y-3 text-sm">
            <div className="flex gap-2">
              {["oanda", "twelvedata"].map((p) => (
                <button
                  key={p}
                  type="button"
                  onClick={() => setCProvider(p)}
                  className={`px-3 py-1.5 rounded-lg capitalize ${
                    cProvider === p ? "bg-accent/15 text-accent" : "text-text-dim hover:text-text"
                  }`}
                >
                  {p}
                </button>
              ))}
            </div>
            <input
              type="password"
              required
              placeholder={`${cProvider} API key`}
              value={cKey}
              onChange={(e) => setCKey(e.target.value)}
              className="w-full rounded-lg border border-border bg-bg px-3 py-2 text-sm outline-none focus:border-accent"
            />
            {cProvider === "oanda" && (
              <>
                <input
                  type="text"
                  required
                  placeholder="Account ID"
                  value={cAccount}
                  onChange={(e) => setCAccount(e.target.value)}
                  className="w-full rounded-lg border border-border bg-bg px-3 py-2 text-sm outline-none focus:border-accent"
                />
                <select
                  value={cEnv}
                  onChange={(e) => setCEnv(e.target.value)}
                  className="w-full rounded-lg border border-border bg-bg px-3 py-2 text-sm outline-none"
                >
                  <option value="practice">Practice (api-fxpractice)</option>
                  <option value="live">Live (api-fxtrade)</option>
                </select>
              </>
            )}
            <button
              type="submit"
              disabled={busy}
              className="w-full rounded-lg bg-accent/90 text-bg font-semibold py-2 text-sm hover:bg-accent disabled:opacity-50"
            >
              {busy ? "Validating…" : "Connect & validate"}
            </button>
            {msg && <div className="text-xs text-good">{msg}</div>}
            {error && <div className="text-xs text-danger">{error}</div>}
          </form>
        </Card>
      </div>

      <Card title="Connections" className="mb-4">
        {Object.keys(status.connections).length === 0 ? (
          <p className="text-xs text-text-dim">No provider connections recorded.</p>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-text-dim text-xs">
                <th className="py-1">Provider</th>
                <th>Status</th>
                <th>Latency</th>
                <th>Error</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(status.connections).map(([p, c]) => (
                <tr key={p} className="border-t border-border">
                  <td className="py-1">{p}</td>
                  <td>
                    <Badge label={c.status} tone={c.status === "connected" ? "good" : "bad"} />
                  </td>
                  <td>{c.latency_ms != null ? `${c.latency_ms} ms` : "—"}</td>
                  <td className="text-xs text-danger">{c.error ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-4">
        <Card title="Live quote">
          <form onSubmit={lookupQuote} className="flex gap-2 text-sm mb-3">
            <input
              value={qlookup}
              onChange={(e) => setQlookup(e.target.value)}
              className="flex-1 rounded-lg border border-border bg-bg px-3 py-2 text-sm outline-none focus:border-accent"
            />
            <button className="rounded-lg bg-accent/90 text-bg px-3 py-2 text-sm font-semibold hover:bg-accent">Fetch</button>
          </form>
          {error && <p className="text-xs text-danger mb-2">{error}</p>}
          {quote && (
            <div className="grid grid-cols-2 gap-2 text-sm">
              <Stat label="Bid" value={quote.bid} />
              <Stat label="Ask" value={quote.ask} />
              <Stat label="Spread" value={`${quote.spread_pips} pips`} />
              <Stat label="Mid" value={quote.mid} />
              <Stat label="Feed" value={quote.feed_state ?? "unknown"} tone={FEED_TONE[quote.feed_state ?? ""] ?? "default"} />
              <Stat label="Market" value={quote.market_status} />
              <Stat label="Latency" value={quote.latency_ms != null ? `${quote.latency_ms} ms` : "n/a"} />
              <Stat label="Basis" value={quote.bid_ask_basis ?? quote.source} />
            </div>
          )}
        </Card>

        <Card title="Instruments">
          <p className="text-xs text-text-dim mb-2">
            {instruments.length} instruments via {status.active_provider_label}.
          </p>
          <div className="max-h-64 overflow-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-text-dim text-xs">
                  <th className="py-1">Symbol</th>
                  <th>Provider</th>
                  <th>Pip</th>
                  <th>Delay</th>
                </tr>
              </thead>
              <tbody>
                {instruments.map((i) => (
                  <tr key={i.canonical_symbol} className="border-t border-border">
                    <td className="py-1">
                      {i.display_symbol} <span className="text-text-dim text-xs">{i.canonical_symbol}</span>
                    </td>
                    <td className="text-xs">{i.provider_symbol}</td>
                    <td className="text-xs">{i.pip_size}</td>
                    <td className="text-xs">
                      <Badge label={i.data_delay_status} tone={i.data_delay_status === "realtime" ? "good" : "warn"} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      </div>

      <Card title="Feed health">
        {feed.length === 0 ? (
          <p className="text-xs text-text-dim">No feed-health rows yet — fetch a quote to register one.</p>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-text-dim text-xs">
                <th className="py-1">Symbol</th>
                <th>Provider</th>
                <th>State</th>
                <th>Last quote</th>
                <th>Latency</th>
                <th>Error</th>
              </tr>
            </thead>
            <tbody>
              {feed.map((r) => (
                <tr key={r.symbol + r.provider} className="border-t border-border">
                  <td className="py-1">{r.symbol}</td>
                  <td className="text-xs">{r.provider}</td>
                  <td>
                    <Badge label={r.feed_status} tone={FEED_TONE[r.feed_status] ?? "default"} />
                  </td>
                  <td className="text-xs">{r.last_quote_ts ? new Date(r.last_quote_ts * 1000).toISOString().slice(11, 19) : "—"}</td>
                  <td className="text-xs">{r.latency_ms != null ? `${r.latency_ms} ms` : "—"}</td>
                  <td className="text-xs text-danger">{r.last_error ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  );
}

function Stat({ label, value, tone = "default" }: { label: string; value: string | number; tone?: "default" | "good" | "bad" | "warn" }) {
  return (
    <div>
      <div className="text-xs text-text-dim">{label}</div>
      <div className={`text-lg font-semibold mt-0.5 ${tone === "good" ? "text-good" : tone === "bad" ? "text-danger" : tone === "warn" ? "text-warn" : ""}`}>
        {value}
      </div>
    </div>
  );
}