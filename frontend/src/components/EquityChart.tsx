"use client";

import { useEffect, useRef } from "react";
import { createChart, ColorType, IChartApi, ISeriesApi, LineData, UTCTimestamp } from "lightweight-charts";

export default function EquityChart({ points }: { points: { ts: number; equity: number }[] }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<{ chart: IChartApi; line: ISeriesApi<"Line"> } | null>(null);

  useEffect(() => {
    if (!containerRef.current || points.length < 2) return;
    const chart = createChart(containerRef.current, {
      height: 320,
      layout: {
        background: { type: ColorType.Solid, color: "#11161d" },
        textColor: "#8b98a7",
        fontSize: 11,
      },
      grid: {
        vertLines: { color: "#1c2530" },
        horzLines: { color: "#1c2530" },
      },
      rightPriceScale: { borderColor: "#232c38" },
      timeScale: { borderColor: "#232c38", timeVisible: true },
      crosshair: { mode: 0 },
    });
    const line = chart.addLineSeries({
      color: "#2dd4bf",
      lineWidth: 2,
      priceFormat: { type: "price", precision: 2, minMove: 0.01 },
    });
    const data: LineData[] = points.map((p) => ({
      time: p.ts as UTCTimestamp,
      value: p.equity,
    }));
    line.setData(data);
    chart.timeScale().fitContent();
    chartRef.current = { chart, line };
    return () => {
      chart.remove();
      chartRef.current = null;
    };
  }, [points]);

  return <div ref={containerRef} className="w-full" />;
}