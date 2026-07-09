// Export a rendered SVG chart as a PNG download, painted on the theme bg.
import { cssVar } from "./tokens";

export function downloadSvgAsPng(svg: SVGSVGElement, filename: string): void {
  const rect = svg.getBoundingClientRect();
  const scale = 2; // retina-crisp export
  const xml = new XMLSerializer().serializeToString(svg);
  const blob = new Blob([xml], { type: "image/svg+xml;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const img = new Image();
  img.onload = () => {
    const canvas = document.createElement("canvas");
    canvas.width = Math.max(1, rect.width * scale);
    canvas.height = Math.max(1, rect.height * scale);
    const ctx = canvas.getContext("2d");
    if (!ctx) return URL.revokeObjectURL(url);
    ctx.fillStyle = cssVar("--panel", "#171a21");
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
    URL.revokeObjectURL(url);
    const a = document.createElement("a");
    a.href = canvas.toDataURL("image/png");
    a.download = filename.endsWith(".png") ? filename : `${filename}.png`;
    a.click();
  };
  img.onerror = () => URL.revokeObjectURL(url);
  img.src = url;
}

/** Rows → CSV text (quotes fields containing separators/quotes/newlines). */
export function toCsv(columns: string[], rows: unknown[][]): string {
  const cell = (v: unknown): string => {
    const s = v == null ? "" : String(v);
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  return [columns.map(cell).join(","), ...rows.map((r) => r.map(cell).join(","))].join("\n");
}

export function downloadCsv(columns: string[], rows: unknown[][], filename: string): void {
  const blob = new Blob([toCsv(columns, rows)], { type: "text/csv;charset=utf-8" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = filename.endsWith(".csv") ? filename : `${filename}.csv`;
  a.click();
  URL.revokeObjectURL(a.href);
}
