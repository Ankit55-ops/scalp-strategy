"use client";

import { useEffect, useRef } from "react";
import type { CandleView } from "@/types";

const BULL = "#16a34a";
const BEAR = "#ef4444";
const GRID = "rgba(148, 163, 184, 0.15)";
const TEXT = "rgba(148, 163, 184, 0.9)";

/**
 * Lightweight canvas candlestick chart. Redraws only when the candle array
 * changes, so live ticks can be folded into the last bar cheaply.
 */
export default function CandlestickChart({
  candles,
  livePrice,
  lastQuoteTs,
  height = 320,
  showVolume = true,
}: {
  candles: CandleView[];
  livePrice?: number | null;
  lastQuoteTs?: number | null;
  height?: number;
  showVolume?: boolean;
}) {
  const ref = useRef<HTMLCanvasElement>(null);
  const livePriceRef = useRef(livePrice);

  useEffect(() => {
    livePriceRef.current = livePrice;
  }, [livePrice]);

  useEffect(() => {
    const canvas = ref.current;
    if (!canvas) return;
    const draw = () => {
      const dpr = window.devicePixelRatio || 1;
      const rect = canvas.getBoundingClientRect();
      const W = Math.max(rect.width, 1);
      const H = Math.max(rect.height, 1);
      canvas.width = Math.round(W * dpr);
      canvas.height = Math.round(H * dpr);
      const ctx = canvas.getContext("2d");
      if (!ctx) return;
      ctx.save();
      ctx.scale(dpr, dpr);
      ctx.clearRect(0, 0, W, H);

      const padL = 12;
      const padR = 62;
      const padT = 12;
      const padB = showVolume ? 46 : 20;
      const plotW = W - padL - padR;
      const plotH = H - padT - padB;

      const data = candles.length
        ? candles
        : [
            {
              symbol: "—",
              timeframe: "",
              ts: 0,
              open: 0,
              high: 1,
              low: 0,
              close: 0,
              volume: 0,
              source: "",
              is_complete: false,
            },
          ];

      let lo = Math.min(...data.map((c) => c.low));
      let hi = Math.max(...data.map((c) => c.high));
      if (livePrice) {
        lo = Math.min(lo, livePrice);
        hi = Math.max(hi, livePrice);
      }
      const pad = Math.max((hi - lo) * 0.08, hi * 0.0002 || 0.01);
      lo -= pad;
      hi += pad;
      if (hi - lo < 1e-12) {
        hi += 0.001;
        lo -= 0.001;
      }
      const y = (price: number) => padT + ((hi - price) / (hi - lo)) * plotH;

      // grid + price labels
      ctx.font = "10px ui-monospace, monospace";
      ctx.fillStyle = TEXT;
      ctx.strokeStyle = GRID;
      ctx.lineWidth = 1;
      const rows = 5;
      for (let i = 0; i <= rows; i++) {
        const gy = padT + (plotH * i) / rows;
        ctx.beginPath();
        ctx.moveTo(padL, gy);
        ctx.lineTo(padL + plotW, gy);
        ctx.stroke();
        const price = hi - ((hi - lo) * i) / rows;
        const label = price >= 1000 ? price.toFixed(1) : price.toFixed(4);
        ctx.fillText(label, padL + plotW + 6, gy + 3);
      }

      // time labels
      ctx.textAlign = "left";
      for (let i = 0; i < data.length; i += Math.max(1, Math.floor(data.length / 6))) {
        const c = data[i];
        const cx = padL + (i + 0.5) * (plotW / data.length);
        const t = c.ts ? new Date(c.ts * 1000).toISOString().slice(11, 16) : "";
        ctx.fillText(t, cx - 12, H - padB + 18);
      }

      const cw = Math.max(1, Math.min(16, (plotW / data.length) * 0.7));
      data.forEach((c, i) => {
        if (!c.ts) return;
        const cx = padL + (i + 0.5) * (plotW / data.length);
        const bull = c.close >= c.open;
        const color = bull ? BULL : BEAR;
        ctx.strokeStyle = color;
        ctx.fillStyle = color;
        ctx.lineWidth = 1.2;
        // wick
        ctx.beginPath();
        ctx.moveTo(cx, y(c.high));
        ctx.lineTo(cx, y(c.low));
        ctx.stroke();
        // body
        const bodyTop = y(Math.max(c.open, c.close));
        const bodyH = Math.max(Math.abs(y(c.open) - y(c.close)), 1);
        ctx.fillRect(cx - cw / 2, bodyTop, cw, bodyH);

        // volume mini-bars
        if (showVolume && c.volume > 0) {
          const vMax = Math.max(...data.map((d) => d.volume || 0), 1e-9);
          const vh = ((c.volume || 0) / vMax) * 22;
          ctx.globalAlpha = 0.4;
          ctx.fillRect(cx - cw / 2, H - padB + 2 + (22 - vh), cw, vh);
          ctx.globalAlpha = 1;
        }
      });

      // live price line
      if (livePrice && livePriceRef.current != null) {
        ctx.strokeStyle = "rgba(59, 130, 246, 0.8)";
        ctx.setLineDash([4, 4]);
        ctx.beginPath();
        ctx.moveTo(padL, y(livePriceRef.current));
        ctx.lineTo(padL + plotW, y(livePriceRef.current));
        ctx.stroke();
        ctx.setLineDash([]);
        ctx.fillStyle = "rgba(59, 130, 246, 0.9)";
        ctx.fillText(livePriceRef.current.toFixed(livePriceRef.current >= 100 ? 1 : 4), padL + plotW + 6, y(livePriceRef.current) - 2);
      }

      ctx.restore();
    };

    draw();
    const ro = new ResizeObserver(() => draw());
    ro.observe(canvas);
    return () => ro.disconnect();
  }, [candles, livePrice, lastQuoteTs, height, showVolume]);

  return <canvas ref={ref} style={{ width: "100%", height }} className="block" />;
}