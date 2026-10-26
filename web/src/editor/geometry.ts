// Boxes, snapping and alignment, all in document (frame) pixels.
import type { Doc, Layer } from "../types";

export interface Rect { x: number; y: number; w: number; h: number }

export const rectOf = (l: Layer): Rect => ({ x: l.x, y: l.y, w: l.w, h: l.h });

export function union(rects: Rect[]): Rect | null {
  if (!rects.length) return null;
  const x = Math.min(...rects.map((r) => r.x));
  const y = Math.min(...rects.map((r) => r.y));
  return { x, y, w: Math.max(...rects.map((r) => r.x + r.w)) - x, h: Math.max(...rects.map((r) => r.y + r.h)) - y };
}

export const intersects = (a: Rect, b: Rect) =>
  a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;

export interface Guide { axis: "x" | "y"; pos: number; from: number; to: number }

/** Snap a moving box to the frame and other layers (edges and centres). Returns the offset to add and guides to draw. */
export function snap(box: Rect, doc: Doc, ignore: Set<string>, threshold: number) {
  const targetsX: { pos: number; r: Rect }[] = [];
  const targetsY: { pos: number; r: Rect }[] = [];
  const frame: Rect = { x: 0, y: 0, w: doc.width, h: doc.height };
  const others = [frame, ...doc.layers.filter((l) => l.visible && !ignore.has(l.id) && l.role !== "background").map(rectOf)];
  for (const r of others) {
    for (const pos of [r.x, r.x + r.w / 2, r.x + r.w]) targetsX.push({ pos, r });
    for (const pos of [r.y, r.y + r.h / 2, r.y + r.h]) targetsY.push({ pos, r });
  }
  const best = (edges: number[], targets: { pos: number; r: Rect }[]) => {
    let hit: { d: number; pos: number; r: Rect } | null = null;
    for (const e of edges)
      for (const t of targets) {
        const d = t.pos - e;
        if (Math.abs(d) <= threshold && (!hit || Math.abs(d) < Math.abs(hit.d))) hit = { d, pos: t.pos, r: t.r };
      }
    return hit;
  };
  const hx = best([box.x, box.x + box.w / 2, box.x + box.w], targetsX);
  const hy = best([box.y, box.y + box.h / 2, box.y + box.h], targetsY);
  const dx = hx?.d ?? 0;
  const dy = hy?.d ?? 0;
  const guides: Guide[] = [];
  if (hx) guides.push({ axis: "x", pos: hx.pos, from: Math.min(box.y + dy, hx.r.y), to: Math.max(box.y + dy + box.h, hx.r.y + hx.r.h) });
  if (hy) guides.push({ axis: "y", pos: hy.pos, from: Math.min(box.x + dx, hy.r.x), to: Math.max(box.x + dx + box.w, hy.r.x + hy.r.w) });
  return { dx, dy, guides };
}

/** Keep mixed styles (an italic serif word…) through a text edit: find the unchanged start and end,
 *  shift ranges after the edit, and stretch ranges that overlap the edited part over the new text. */
export function remapRanges<R extends { start: number; end: number }>(before: string, after: string, ranges: R[]): R[] {
  let pre = 0;
  while (pre < before.length && pre < after.length && before[pre] === after[pre]) pre++;
  let suf = 0;
  while (suf < before.length - pre && suf < after.length - pre
    && before[before.length - 1 - suf] === after[after.length - 1 - suf]) suf++;
  const oldEnd = before.length - suf, newEnd = after.length - suf, delta = after.length - before.length;
  const map = (i: number, isEnd: boolean) =>
    i <= pre ? i : i >= oldEnd ? i + delta : isEnd ? newEnd : pre;
  return ranges
    .map((r) => ({ ...r, start: map(r.start, false), end: map(r.end, true) }))
    .filter((r) => r.end > r.start);
}

export type AlignKind = "left" | "hcenter" | "right" | "top" | "vcenter" | "bottom";

/** Align layers to each other (2+) or to the frame (1). */
export function align(layers: Layer[], doc: Doc, kind: AlignKind): Record<string, Partial<Layer>> {
  const ref = layers.length > 1 ? union(layers.map(rectOf))! : { x: 0, y: 0, w: doc.width, h: doc.height };
  const out: Record<string, Partial<Layer>> = {};
  for (const l of layers) {
    const p: Partial<Layer> = {};
    if (kind === "left") p.x = ref.x;
    if (kind === "hcenter") p.x = Math.round(ref.x + (ref.w - l.w) / 2);
    if (kind === "right") p.x = ref.x + ref.w - l.w;
    if (kind === "top") p.y = ref.y;
    if (kind === "vcenter") p.y = Math.round(ref.y + (ref.h - l.h) / 2);
    if (kind === "bottom") p.y = ref.y + ref.h - l.h;
    out[l.id] = p;
  }
  return out;
}
