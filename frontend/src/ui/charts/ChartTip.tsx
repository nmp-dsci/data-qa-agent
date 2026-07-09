// ChartTip — a lightweight hover tooltip shared by the visx charts. Charts keep
// tooltip state locally and render this over a relatively-positioned plot div;
// left/top are pixel coordinates within that div (svg width×height at 0,0), so
// they line up with the mark the pointer is over.

export interface TipRow {
  label: string;
  value: string;
  color?: string;
}

export interface TipState {
  left: number;
  top: number;
  title?: string;
  rows: TipRow[];
}

export function ChartTip({ tip, width }: { tip: TipState | null; width: number }) {
  if (!tip) return null;
  // Flip to the left of the cursor when near the right edge so it stays on-chart.
  const flip = tip.left > width * 0.6;
  return (
    <div
      className="chart-tip"
      style={{
        left: tip.left,
        top: tip.top,
        transform: flip ? "translate(calc(-100% - 12px), -50%)" : "translate(12px, -50%)",
      }}
    >
      {tip.title && <div className="chart-tip-title">{tip.title}</div>}
      {tip.rows.map((r, i) => (
        <div key={i} className="chart-tip-row">
          {r.color && <span className="chart-tip-dot" style={{ background: r.color }} />}
          <span className="chart-tip-label">{r.label}</span>
          <span className="chart-tip-val">{r.value}</span>
        </div>
      ))}
    </div>
  );
}
