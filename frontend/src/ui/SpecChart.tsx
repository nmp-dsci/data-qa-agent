// SpecChart — renders the validated house chart-spec shapes with visx,
// replacing the vega-embed runtime entirely. The agent's chart validator
// (agent/chart.py) restricts specs to a small known shape — mark line|bar|
// area|point (or a layer list), encoding.{x,y,color,xOffset}.field, and
// data.values spliced in server-side — so lifting data + fields out here is
// deterministic. Also renders the SQL editor's builder specs (same shape).
// Legacy stored reports (feedback snapshots, old messages) keep rendering.
import { Bars, BarsData } from "./charts/Bars";
import { Trend, TrendData } from "./charts/Trend";

type Spec = Record<string, unknown>;

function specValues(spec: Spec): Record<string, unknown>[] {
  const data = spec["data"];
  if (data && typeof data === "object" && Array.isArray((data as Spec)["values"])) {
    return ((data as Spec)["values"] as unknown[]).filter(
      (v): v is Record<string, unknown> => v != null && typeof v === "object",
    );
  }
  return [];
}

function specMark(spec: Spec): string | null {
  const mark = spec["mark"];
  if (typeof mark === "string") return mark;
  if (mark && typeof mark === "object" && typeof (mark as Spec)["type"] === "string") {
    return (mark as Spec)["type"] as string;
  }
  const layers = spec["layer"];
  if (Array.isArray(layers) && layers.length > 0 && typeof layers[0] === "object") {
    return specMark(layers[0] as Spec);
  }
  return null;
}

function specEncoding(spec: Spec): Spec {
  const enc = spec["encoding"];
  if (enc && typeof enc === "object") return enc as Spec;
  const layers = spec["layer"];
  if (Array.isArray(layers) && layers.length > 0 && typeof layers[0] === "object") {
    const first = layers[0] as Spec;
    if (first["encoding"] && typeof first["encoding"] === "object") {
      return first["encoding"] as Spec;
    }
  }
  return {};
}

function encField(encoding: Spec, channel: string): string | null {
  const ch = encoding[channel];
  if (ch && typeof ch === "object" && typeof (ch as Spec)["field"] === "string") {
    return (ch as Spec)["field"] as string;
  }
  return null;
}

export function SpecChart({ spec }: { spec: Spec }) {
  const values = specValues(spec);
  const mark = specMark(spec);
  const encoding = specEncoding(spec);
  const title = typeof spec["title"] === "string" ? (spec["title"] as string) : null;

  if (values.length === 0 || !mark) {
    return <p className="muted">Chart unavailable (no embedded data).</p>;
  }

  if (mark === "line" || mark === "area" || mark === "point") {
    const series =
      encField(encoding, "color") ?? (values.some((v) => "series" in v) ? "series" : null);
    const data: TrendData = {
      x: encField(encoding, "x") ?? "month",
      y: encField(encoding, "y") ?? "value",
      series,
      title,
      rows: values,
    };
    return <Trend data={data} />;
  }

  if (mark === "bar" || mark === "arc") {
    const dimension = encField(encoding, "x");
    const measure = encField(encoding, "y");
    if (!dimension || !measure) {
      return <p className="muted">Chart unavailable (unsupported encoding).</p>;
    }
    const group = encField(encoding, "xOffset") ?? encField(encoding, "color");
    const data: BarsData = {
      dimension,
      measure,
      group: group !== dimension ? group : null,
      title,
      rows: values,
    };
    return <Bars data={data} />;
  }

  return <p className="muted">Chart unavailable (unsupported mark: {mark}).</p>;
}
