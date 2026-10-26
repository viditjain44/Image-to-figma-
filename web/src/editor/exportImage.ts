// Draws the design onto a <canvas> the same way the editor shows it, for PNG / JPG download.
// (Drawn by hand rather than screenshotting the DOM, so web fonts and images come out exactly.)
import { fileUrl } from "../api";
import { loadFontsFor, parseStyle } from "../fonts";
import type { Doc, Layer } from "../types";

export type ImageFormat = "png" | "jpg";

function cssFont(family: string, style: string, size: number) {
  const { weight, italic } = parseStyle(style);
  return `${italic ? "italic " : ""}${weight} ${size}px "${family}", Inter, system-ui, sans-serif`;
}

function loadImage(src: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error(`Could not load ${src}`));
    img.src = fileUrl(src);
  });
}

interface Run { text: string; font: string; color: string }

/** Splits one line of a text layer into runs that share a font and colour (mixed styles). */
function runsFor(l: Layer, lineStart: number, line: string): Run[] {
  const runs: Run[] = [];
  for (let i = 0; i < line.length; i++) {
    const at = lineStart + i;
    let family = l.font_family, style = l.font_style, color = l.color;
    for (const r of l.ranges) {
      if (at < r.start || at >= r.end) continue;
      if (r.font_family || r.font_style) { family = r.font_family || family; style = r.font_style || style; }
      if (r.color) color = r.color;
    }
    const font = cssFont(family, style, l.font_size);
    const last = runs[runs.length - 1];
    if (last && last.font === font && last.color === color) last.text += line[i];
    else runs.push({ text: line[i], font, color });
  }
  return runs;
}

/** Draws a text layer; `box` is where its lines are laid out (the layer itself, or a button's inside). */
function drawText(ctx: CanvasRenderingContext2D, l: Layer, lineHeight: number, box: { x: number; y: number; w?: number; h?: number }) {
  const base = cssFont(l.font_family, l.font_style, l.font_size);
  ctx.font = base;
  const m = ctx.measureText("Hg");
  const ascent = m.fontBoundingBoxAscent ?? l.font_size * 0.9;
  const descent = m.fontBoundingBoxDescent ?? l.font_size * 0.25;
  const lh = lineHeight || ascent + descent; // CSS "normal" ≈ the font's own ascent + descent
  const spacing = l.letter_spacing ? `${(l.letter_spacing / 100) * l.font_size}px` : "0px";
  if ("letterSpacing" in ctx) (ctx as unknown as { letterSpacing: string }).letterSpacing = spacing;

  const lines = l.text.split("\n");
  let offset = 0;
  const laid = lines.map((line) => {
    const runs = runsFor(l, offset, line);
    offset += line.length + 1;
    const widths = runs.map((r) => { ctx.font = r.font; return ctx.measureText(r.text).width; });
    return { runs, widths, width: widths.reduce((a, b) => a + b, 0) };
  });
  const blockW = box.w ?? Math.max(0, ...laid.map((x) => x.width));
  const blockH = lh * lines.length;
  const top = box.h !== undefined ? box.y + (box.h - blockH) / 2 : box.y; // buttons centre vertically

  ctx.textBaseline = "alphabetic";
  laid.forEach(({ runs, widths, width }, i) => {
    let x = box.x + (l.align === "CENTER" ? (blockW - width) / 2 : l.align === "RIGHT" ? blockW - width : 0);
    // CSS puts the glyphs in the middle of each line box (half-leading above and below)
    const baseline = top + i * lh + (lh - (ascent + descent)) / 2 + ascent;
    runs.forEach((r, j) => {
      ctx.font = r.font;
      ctx.fillStyle = r.color || "#000";
      ctx.fillText(r.text, x, baseline);
      x += widths[j];
    });
  });
}

/** Renders the visible layers of the design at `scale`× and returns the file as a Blob. */
export async function renderDesign(doc: Doc, format: ImageFormat, scale = 1): Promise<Blob> {
  const layers = doc.layers.filter((l) => l.visible);
  await loadFontsFor(layers.flatMap((l) => [
    ...(l.font_family ? [{ family: l.font_family, style: l.font_style }] : []),
    ...l.ranges.filter((r) => r.font_family || r.font_style)
      .map((r) => ({ family: r.font_family || l.font_family, style: r.font_style || l.font_style })),
  ]));
  const images = new Map<string, HTMLImageElement>();
  await Promise.all([
    ...layers.filter((l) => l.type === "image" && l.src).map(async (l) => images.set(l.src, await loadImage(l.src))),
    ...layers.filter((l) => l.icon_src).map(async (l) => images.set(l.icon_src!, await loadImage(l.icon_src!))),
  ]);

  const canvas = document.createElement("canvas");
  canvas.width = Math.round(doc.width * scale);
  canvas.height = Math.round(doc.height * scale);
  const ctx = canvas.getContext("2d")!;
  ctx.scale(scale, scale);
  ctx.fillStyle = "#ffffff"; // the frame is white, like in the editor
  ctx.fillRect(0, 0, doc.width, doc.height);
  ctx.imageSmoothingQuality = "high";

  for (const l of layers) {
    ctx.save();
    if (l.type === "image") {
      const img = images.get(l.src);
      if (img) ctx.drawImage(img, l.x, l.y, l.w, l.h);
    } else if (l.type === "rect" || l.type === "button" || l.type === "pill") {
      const radius = Math.min(l.corner_radius, l.w / 2, l.h / 2);
      ctx.fillStyle = l.fill || "#D9D9D9";
      ctx.beginPath();
      ctx.roundRect(l.x, l.y, l.w, l.h, radius);
      ctx.fill();
      if (l.stroke && l.stroke_width) { // inside stroke
        const sw = l.stroke_width;
        ctx.strokeStyle = l.stroke;
        ctx.lineWidth = sw;
        ctx.beginPath();
        ctx.roundRect(l.x + sw / 2, l.y + sw / 2, l.w - sw, l.h - sw, Math.max(0, radius - sw / 2));
        ctx.stroke();
      }
      if (l.type !== "rect" && l.text) {
        const icon = l.icon_src ? images.get(l.icon_src) : undefined;
        if (icon && l.icon_w && l.icon_h) { // icon + gap + label, centred together like the auto-layout
          ctx.font = cssFont(l.font_family, l.font_style, l.font_size);
          const labelW = Math.max(...l.text.split("\n").map((t) => ctx.measureText(t).width));
          const total = l.icon_w + (l.gap ?? 0) + labelW;
          const left = l.x + (l.w - total) / 2;
          const iconX = l.icon_side === "right" ? left + labelW + (l.gap ?? 0) : left;
          const textX = l.icon_side === "right" ? left : left + l.icon_w + (l.gap ?? 0);
          ctx.drawImage(icon, iconX, l.y + (l.h - l.icon_h) / 2, l.icon_w, l.icon_h);
          drawText(ctx, { ...l, align: "LEFT" }, 0, { x: textX, y: l.y, w: labelW, h: l.h });
        } else {
          drawText(ctx, { ...l, align: "CENTER" }, 0, { x: l.x, y: l.y, w: l.w, h: l.h });
        }
      }
    } else if (l.type === "text") {
      drawText(ctx, l, l.line_height, { x: l.x, y: l.y });
    }
    ctx.restore();
  }

  return new Promise((resolve, reject) =>
    canvas.toBlob((b) => (b ? resolve(b) : reject(new Error("Could not create the image"))),
      format === "png" ? "image/png" : "image/jpeg", 0.92));
}

export function saveBlob(blob: Blob, name: string) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}
