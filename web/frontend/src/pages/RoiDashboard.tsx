import { useEffect, useRef, useState, type PointerEvent } from "react";
import type { RoiInputs, RoiResult } from "../api";
import { formatters } from "../roi";

/** The ROI as a small dashboard: the first-year return, when the automation pays back, and what a month costs
with and without it. Needs the savings, so it shows only once the runs per month and cost per hour are known. */
export default function RoiDashboard({ inputs, result }: { inputs: RoiInputs; result: RoiResult }) {
  if (result.savings === null || result.net_savings === null) return null;
  const { money } = formatters(inputs.currency);
  const build = inputs.build_cost ?? 0;
  const firstYear = result.net_savings * 12 - build;
  let payback = "never pays back: running it costs more than it saves";
  if (result.pays_back) payback = build === 0 ? "pays back at once (no build cost)" : `pays back in ${monthsText(result.payback_months!)}`;

  return (
    <div className="roi-dash">
      <div className="roi-hero">
        <span className="roi-hero-label">Return in the first year</span>
        <span className={`roi-hero-value${firstYear < 0 ? " negative" : ""}`}>{money(firstYear)}</span>
        <span className="subtle small">
          Net savings over 12 months, after the {money(build)} to build it · {payback}
        </span>
      </div>
      <div className="roi-charts">
        <BreakEven inputs={inputs} result={result} />
        <MonthlyCosts inputs={inputs} result={result} />
      </div>
    </div>
  );
}

function monthsText(months: number): string {
  if (months < 1) return "under a month";
  const n = Math.round(months * 10) / 10;
  return `${n} month${n === 1 ? "" : "s"}`;
}

/** The chart's width follows its box. */
function useWidth<T extends HTMLElement>(): [React.RefObject<T | null>, number] {
  const ref = useRef<T>(null);
  const [width, setWidth] = useState(0);
  useEffect(() => {
    if (!ref.current) return;
    const observer = new ResizeObserver(([entry]) => setWidth(Math.round(entry.contentRect.width)));
    observer.observe(ref.current);
    return () => observer.disconnect();
  }, []);
  return [ref, width];
}

/** Clean axis ticks: 0 and a few round steps covering [min, max]. */
function ticks(min: number, max: number, count = 4): number[] {
  const span = Math.max(max - min, 1);
  const raw = span / count;
  const power = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * power).find((s) => s >= raw)!;
  const out = [Math.floor(min / step) * step];
  while (out[out.length - 1] < max) out.push(Math.round((out[out.length - 1] + step) * 100) / 100);
  return out;
}

/** An axis's labels, all in one style: in full under 10,000, compact (10K, 1.5M) on an axis that reaches it. */
function axisLabel(values: number[], currency: RoiInputs["currency"]): (value: number) => string {
  const { money } = formatters(currency);
  const symbol = money(0).replace(/[\d.,\s]+/g, "").trim();
  const reach = Math.max(...values.map(Math.abs));
  const unit = reach >= 1_000_000 ? [1_000_000, "M"] as const : reach >= 10_000 ? [1000, "K"] as const : null;
  return (value) => {
    if (!unit) return money(value).replace(/[.,]00$/, "");
    const [size, suffix] = unit;
    const n = new Intl.NumberFormat(currency === "USD" ? "en-US" : "pt-BR", { maximumFractionDigits: 1 })
      .format(Math.abs(value) / size);
    return `${value < 0 ? "-" : ""}${symbol} ${n}${value === 0 ? "" : suffix}`;
  };
}

/** Where the money stands month by month: minus the build cost at the start, plus the net savings each month. */
function BreakEven({ inputs, result }: { inputs: RoiInputs; result: RoiResult }) {
  const [box, width] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const build = inputs.build_cost ?? 0;
  const net = result.net_savings!;
  // Long enough to see the payback and some months after it; a year at least, three at most.
  const months = Math.min(36, Math.max(12, result.pays_back ? Math.ceil((result.payback_months! * 1.5) / 6) * 6 : 12));
  const points = Array.from({ length: months + 1 }, (_, m) => ({ month: m, value: net * m - build }));
  const values = points.map((p) => p.value);
  const yTicks = ticks(Math.min(0, ...values), Math.max(0, ...values));
  const [lo, hi] = [yTicks[0], yTicks[yTicks.length - 1]];
  const label = axisLabel(yTicks, inputs.currency);

  const height = 220, left = 64, right = 16, top = 12, bottom = 28;
  const plotW = Math.max(width - left - right, 10), plotH = height - top - bottom;
  const x = (m: number) => left + (m / months) * plotW;
  const y = (v: number) => top + (1 - (v - lo) / (hi - lo || 1)) * plotH;
  const line = points.map((p, i) => `${i ? "L" : "M"}${x(p.month)},${y(p.value)}`).join("");
  const area = `${line}L${x(months)},${y(0)}L${x(0)},${y(0)}Z`;
  const xTicks = Array.from({ length: months / 6 + 1 }, (_, i) => i * 6);
  const payback = result.pays_back && build > 0 && result.payback_months! <= months ? result.payback_months! : null;

  function move(e: PointerEvent<SVGSVGElement>) {
    const bounds = e.currentTarget.getBoundingClientRect();
    const m = Math.round(((e.clientX - bounds.left - left) / plotW) * months);
    setHover(Math.min(months, Math.max(0, m)));
  }
  const shown = hover === null ? null : points[hover];
  const { money } = formatters(inputs.currency);

  return (
    <figure className="roi-chart roi-chart-wide">
      <figcaption>
        <strong>When it pays back</strong>
        <span className="subtle small">Money saved so far, after the cost to build it</span>
      </figcaption>
      <div ref={box} className="roi-plot">
        {width > 0 && (
          <svg width={width} height={height} role="img" onPointerMove={move} onPointerLeave={() => setHover(null)}
               aria-label={`Starts at ${money(-build)}, changes by ${money(net)} a month${
                 payback !== null ? `, and reaches zero after ${monthsText(payback)}` : ""}.`}>
            {yTicks.map((t) => (
              <g key={t}>
                <line x1={left} x2={left + plotW} y1={y(t)} y2={y(t)} className={t === 0 ? "axis-zero" : "grid"} />
                <text x={left - 8} y={y(t)} dy="0.32em" textAnchor="end" className="tick">{label(t)}</text>
              </g>
            ))}
            {xTicks.map((m) => (
              <text key={m} x={x(m)} y={height - 8} textAnchor="middle" className="tick">
                {m === 0 ? "Start" : `${m} mo`}
              </text>
            ))}
            <path d={area} className="series-area" />
            <path d={line} className="series-line" />
            {payback !== null && (
              <g>
                <line x1={x(payback)} x2={x(payback)} y1={top} y2={y(0)} className="marker-line" />
                <circle cx={x(payback)} cy={y(0)} r={5} className="series-dot" />
                {/* Under the dot, on whichever side the text fits (about 7px a character at 12px); shorter if neither. */}
                {(() => {
                  const long = `Pays back · ${monthsText(payback)}`;
                  const fits = (text: string) => ({
                    right: x(payback) + 8 + text.length * 7 <= left + plotW, left: x(payback) - 8 - text.length * 7 >= left,
                  });
                  const text = fits(long).right || fits(long).left ? long : "Pays back";
                  const right = fits(text).right;
                  return (
                    <text x={x(payback)} y={y(0)} dx={right ? 8 : -8} dy={18} textAnchor={right ? "start" : "end"}
                          className="marker-label">{text}</text>
                  );
                })()}
              </g>
            )}
            {shown && (
              <g pointerEvents="none">
                <line x1={x(shown.month)} x2={x(shown.month)} y1={top} y2={top + plotH} className="crosshair" />
                <circle cx={x(shown.month)} cy={y(shown.value)} r={5} className="series-dot" />
              </g>
            )}
          </svg>
        )}
        {shown && (
          <div className="roi-tooltip" style={{ left: Math.min(x(shown.month) + 10, width - 150), top: Math.max(y(shown.value) - 52, 0) }}>
            <span className="subtle small">{shown.month === 0 ? "At the start" : `After ${shown.month} month${shown.month === 1 ? "" : "s"}`}</span>
            <strong className={shown.value < 0 ? "negative" : undefined}>{money(shown.value)}</strong>
          </div>
        )}
      </div>
      <details className="roi-table">
        <summary>Figures by month</summary>
        <table>
          <thead><tr><th scope="col">Month</th><th scope="col">Saved so far</th></tr></thead>
          <tbody>
            {points.filter((p) => p.month % 3 === 0).map((p) => (
              <tr key={p.month}><td>{p.month === 0 ? "Start" : p.month}</td><td>{money(p.value)}</td></tr>
            ))}
          </tbody>
        </table>
      </details>
    </figure>
  );
}

/** A month of the process done by hand, against a month of the automation running. */
function MonthlyCosts({ inputs, result }: { inputs: RoiInputs; result: RoiResult }) {
  const { money } = formatters(inputs.currency);
  const manual = result.savings!, automated = inputs.monthly_cost ?? 0;
  const max = Math.max(manual, automated, 1);
  const rows = [
    { label: "Done by hand", value: manual, note: `${formatters(inputs.currency).number(result.hours_saved ?? 0)} h of work`, emphasis: true },
    { label: "Automated", value: automated, note: "running costs", emphasis: false },
  ];
  const difference = manual - automated;
  return (
    <figure className="roi-chart">
      <figcaption>
        <strong>A month of this process</strong>
        <span className="subtle small">What it costs each way</span>
      </figcaption>
      <div className="roi-bars" role="img"
           aria-label={`Done by hand ${money(manual)} a month; automated ${money(automated)} a month.`}>
        {rows.map((r) => (
          <div key={r.label} className="roi-bar-row" title={`${r.label}: ${money(r.value)} a month`}>
            <span className="roi-bar-label">{r.label}<span className="subtle small"> · {r.note}</span></span>
            <span className="roi-bar-track">
              <span className={`roi-bar${r.emphasis ? "" : " muted"}`} style={{ width: `${(r.value / max) * 100}%` }} />
              <span className="roi-bar-value">{money(r.value)}</span>
            </span>
          </div>
        ))}
      </div>
      <p className={`roi-bars-total${difference < 0 ? " negative" : ""}`}>
        {difference >= 0 ? `Saves ${money(difference)} a month` : `Costs ${money(-difference)} more a month`}
      </p>
    </figure>
  );
}
