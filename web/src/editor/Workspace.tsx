// The canvas: an infinite, zoomable workspace with the design frame on it.
// Pointer handling lives here: pan, select, marquee, move (with snapping), resize, draw, edit text.
import { useCallback, useEffect, useLayoutEffect, useRef, useState, type Dispatch, type MouseEvent, type PointerEvent } from "react";
import { fileUrl } from "../api";
import { loadFontsFor } from "../fonts";
import type { Layer } from "../types";
import { intersects, rectOf, remapRanges, snap, union, type Guide, type Rect } from "./geometry";
import LayerView from "./LayerView";
import { canvasPickable, newId, type Action, type EditorState, type LayerPatch } from "./store";

export type Tool = "move" | "hand" | "text" | "rect";
export interface Camera { x: number; y: number; z: number }
export interface ViewOptions { reference: number; boxes: boolean } // reference: opacity of the original on top, 0 = off

type Handle = "nw" | "n" | "ne" | "e" | "se" | "s" | "sw" | "w";
const HANDLES: Handle[] = ["nw", "n", "ne", "e", "se", "s", "sw", "w"];
const CORNERS: Handle[] = ["nw", "ne", "se", "sw"];

type Drag =
  | { kind: "pan"; sx: number; sy: number; cam: Camera }
  | { kind: "move"; wx: number; wy: number; orig: Record<string, Layer>; moved: boolean }
  | { kind: "resize"; handle: Handle; wx: number; wy: number; orig: Layer }
  | { kind: "marquee"; wx: number; wy: number; base: string[] }
  | { kind: "draw"; wx: number; wy: number; id: string };

interface Props {
  state: EditorState;
  dispatch: Dispatch<Action>;
  tool: Tool;
  setTool: (t: Tool) => void;
  cam: Camera;
  setCam: (c: Camera | ((c: Camera) => Camera)) => void;
  fitSignal: number;
  view: ViewOptions;
  editingId: string | null;
  setEditingId: (id: string | null) => void;
  onDropFile: (f: File) => void;
  refit: { widths: Record<string, number>; nonce: number } | null; // keep these text layers at these widths
}

const r = Math.round;

export default function Workspace(p: Props) {
  const { state, dispatch, tool, cam, setCam, view, editingId, setEditingId } = p;
  const doc = state.doc!;
  const vp = useRef<HTMLDivElement>(null);
  const drag = useRef<Drag | null>(null);
  const space = useRef(false);
  const docRef = useRef(doc);
  docRef.current = doc;
  const camRef = useRef(cam);
  camRef.current = cam;
  const [guides, setGuides] = useState<Guide[]>([]);
  const [marquee, setMarquee] = useState<Rect | null>(null);
  const [hover, setHover] = useState<string | null>(null);
  const [panning, setPanning] = useState(false);

  // ---------- camera ----------
  const fit = useCallback(() => {
    const el = vp.current;
    if (!el) return;
    const z = Math.min((el.clientWidth - 120) / doc.width, (el.clientHeight - 120) / doc.height, 4);
    setCam({ z, x: (el.clientWidth - doc.width * z) / 2, y: (el.clientHeight - doc.height * z) / 2 });
  }, [doc.width, doc.height, setCam]);
  useLayoutEffect(fit, [doc.job_id, p.fitSignal]); // eslint-disable-line react-hooks/exhaustive-deps

  const toWorld = (e: { clientX: number; clientY: number }) => {
    const b = vp.current!.getBoundingClientRect();
    const c = camRef.current;
    return { x: (e.clientX - b.left - c.x) / c.z, y: (e.clientY - b.top - c.y) / c.z };
  };

  useEffect(() => {
    const el = vp.current!;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const b = el.getBoundingClientRect();
      if (e.ctrlKey || e.metaKey) { // pinch / cmd+scroll: zoom around the cursor
        setCam((c) => {
          const z = Math.min(32, Math.max(0.02, c.z * Math.exp(-e.deltaY * 0.01)));
          const mx = e.clientX - b.left, my = e.clientY - b.top;
          return { z, x: mx - ((mx - c.x) / c.z) * z, y: my - ((my - c.y) / c.z) * z };
        });
      } else {
        setCam((c) => ({ ...c, x: c.x - e.deltaX, y: c.y - e.deltaY }));
      }
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, [setCam]);

  useEffect(() => { // hold space to pan, like Figma
    const typing = (e: KeyboardEvent) => (e.target as HTMLElement).closest("input, textarea, select, [contenteditable]");
    const down = (e: KeyboardEvent) => { if (e.code === "Space" && !typing(e)) { space.current = true; setPanning(true); e.preventDefault(); } };
    const up = (e: KeyboardEvent) => { if (e.code === "Space") { space.current = false; setPanning(false); } };
    window.addEventListener("keydown", down);
    window.addEventListener("keyup", up);
    return () => { window.removeEventListener("keydown", down); window.removeEventListener("keyup", up); };
  }, []);

  // ---------- text layers: measure the rendered size (text is auto-width, like Figma) ----------
  const textEls = useRef(new Map<string, HTMLElement>());
  const fitted = useRef(new Set<string>());
  const observer = useRef<ResizeObserver | null>(null);

  const syncSizes = useCallback((ids: Iterable<string>) => {
    const d = docRef.current;
    const patch: LayerPatch = {};
    for (const id of ids) {
      const el = textEls.current.get(id);
      const l = d.layers.find((x) => x.id === id);
      if (!el || !l || l.type !== "text" || !fitted.current.has(id)) continue;
      const w = r(el.offsetWidth), h = r(el.offsetHeight);
      if (w === l.w && h === l.h) continue;
      // keep the measured anchor: left edge, centre or right edge
      const x = l.align === "CENTER" ? r(l.x + (l.w - w) / 2) : l.align === "RIGHT" ? r(l.x + l.w - w) : l.x;
      patch[id] = { w, h, x };
    }
    if (Object.keys(patch).length) dispatch({ type: "sync", patch });
  }, [dispatch]);

  useEffect(() => {
    observer.current = new ResizeObserver((entries) =>
      syncSizes(entries.map((e) => (e.target as HTMLElement).dataset.layerId!)));
    return () => observer.current?.disconnect();
  }, [syncSizes]);

  const register = useCallback((id: string, el: HTMLElement | null) => {
    const old = textEls.current.get(id);
    if (old && old !== el) observer.current?.unobserve(old);
    if (el) { textEls.current.set(id, el); observer.current?.observe(el); } else textEls.current.delete(id);
  }, []);

  // Once fonts are in: scale each converted text so it is as wide as in the original image
  // (sizes are estimated from glyph height, which varies between fonts; widths were measured exactly).
  useEffect(() => {
    let cancelled = false;
    fitted.current = new Set();
    const texts = doc.layers.filter((l) => l.type === "text" || l.type === "button" || l.type === "pill");
    const specs = texts.flatMap((l) => [{ family: l.font_family, style: l.font_style },
      ...l.ranges.filter((g) => g.font_family).map((g) => ({ family: g.font_family, style: g.font_style || l.font_style }))]);
    loadFontsFor(specs).then(() => {
      if (cancelled) return;
      const d = docRef.current;
      const patch: LayerPatch = {};
      for (const l of d.layers) {
        const el = textEls.current.get(l.id);
        if (l.type !== "text" || !el) continue;
        const target = l.fit_w || l.w;
        const ratio = target / Math.max(1, el.offsetWidth);
        if (target > 0 && (ratio < 0.96 || ratio > 1.04)) {
          const k = Math.min(1.25, Math.max(0.75, ratio));
          patch[l.id] = { font_size: Math.max(1, r(l.font_size * k)), line_height: l.line_height ? r(l.line_height * k) : 0 };
        }
      }
      if (Object.keys(patch).length) dispatch({ type: "sync", patch });
      requestAnimationFrame(() => {
        if (cancelled) return;
        for (const l of docRef.current.layers) if (l.type === "text") fitted.current.add(l.id);
        syncSizes(fitted.current);
      });
    });
    return () => { cancelled = true; };
  }, [doc.job_id]); // eslint-disable-line react-hooks/exhaustive-deps

  // Fonts changed in the design panel: measure again once they load
  const fontKey = doc.layers.map((l) => `${l.font_family}|${l.font_style}`).join(",");
  useEffect(() => {
    const specs = doc.layers.filter((l) => l.font_family).map((l) => ({ family: l.font_family, style: l.font_style }));
    loadFontsFor(specs).then(() => syncSizes(textEls.current.keys()));
  }, [fontKey]); // eslint-disable-line react-hooks/exhaustive-deps

  // After a font swap: scale each text so it keeps the width it had (the converted layout stays put)
  useEffect(() => {
    if (!p.refit) return;
    const { widths } = p.refit;
    const ids = Object.keys(widths);
    const layers = docRef.current.layers.filter((l) => ids.includes(l.id));
    let cancelled = false;
    loadFontsFor(layers.map((l) => ({ family: l.font_family, style: l.font_style }))).then(() =>
      requestAnimationFrame(() => {
        if (cancelled) return;
        const patch: LayerPatch = {};
        for (const l of docRef.current.layers) {
          const el = textEls.current.get(l.id);
          if (!el || !(l.id in widths)) continue;
          const k = Math.min(1.5, Math.max(0.5, widths[l.id] / Math.max(1, el.offsetWidth)));
          if (Math.abs(k - 1) > 0.01)
            patch[l.id] = { font_size: Math.max(1, r(l.font_size * k)), line_height: l.line_height ? r(l.line_height * k) : 0 };
        }
        if (Object.keys(patch).length) dispatch({ type: "amend", patch });
      }));
    return () => { cancelled = true; };
  }, [p.refit?.nonce]); // eslint-disable-line react-hooks/exhaustive-deps

  // ---------- pointer ----------
  const layerAt = (target: EventTarget | null): Layer | null => {
    const id = (target as HTMLElement | null)?.closest?.("[data-layer-id]")?.getAttribute("data-layer-id");
    const l = id ? doc.layers.find((x) => x.id === id) : null;
    return l && canvasPickable(l) ? l : null;
  };

  const onPointerDown = (e: PointerEvent) => {
    if (e.button === 2) return;
    const w = toWorld(e);
    // Capture on the viewport itself, so the release always comes back here even off-canvas
    try { vp.current?.setPointerCapture(e.pointerId); } catch { /* pointer already gone */ }
    if (e.button === 1 || space.current || tool === "hand") {
      drag.current = { kind: "pan", sx: e.clientX, sy: e.clientY, cam: camRef.current };
      setPanning(true);
      return;
    }
    const handle = (e.target as HTMLElement).dataset?.handle as Handle | undefined;
    if (handle && state.selection.length === 1) {
      const l = doc.layers.find((x) => x.id === state.selection[0])!;
      dispatch({ type: "begin" });
      drag.current = { kind: "resize", handle, wx: w.x, wy: w.y, orig: l };
      return;
    }
    if (tool === "text") {
      const id = newId("text");
      const layer: Layer = {
        id, name: "Text", role: "body", type: "text", x: r(w.x), y: r(w.y), w: 0, h: 0, text: "Text",
        font_family: doc.fonts.sans || "Inter", font_style: "Regular", font_size: r(Math.min(200, Math.max(12, 16 / cam.z))),
        line_height: 0, color: "#111111", fill: "", corner_radius: 0, padding_x: 0, align: "LEFT", letter_spacing: 0,
        ranges: [], src: "", visible: true, locked: false,
      };
      fitted.current.add(id);
      dispatch({ type: "commit", layers: [...doc.layers, layer] });
      dispatch({ type: "select", ids: [id] });
      setEditingId(id);
      p.setTool("move");
      return;
    }
    if (tool === "rect") {
      const id = newId("rect");
      const layer: Layer = {
        id, name: "Rectangle", role: "shape", type: "rect", x: r(w.x), y: r(w.y), w: 1, h: 1, text: "",
        font_family: "", font_style: "", font_size: 0, line_height: 0, color: "", fill: "#D9D9D9", corner_radius: 0,
        padding_x: 0, align: "LEFT", letter_spacing: 0, ranges: [], src: "", visible: true, locked: false,
      };
      dispatch({ type: "begin" });
      dispatch({ type: "preview", layers: [...doc.layers, layer] });
      dispatch({ type: "select", ids: [id] });
      drag.current = { kind: "draw", wx: w.x, wy: w.y, id };
      return;
    }
    const hit = layerAt(e.target);
    if (hit) {
      let sel = state.selection;
      if (e.shiftKey) sel = sel.includes(hit.id) ? sel.filter((s) => s !== hit.id) : [...sel, hit.id];
      else if (!sel.includes(hit.id)) sel = [hit.id];
      dispatch({ type: "select", ids: sel });
      const orig: Record<string, Layer> = {};
      for (const id of sel) {
        const l = doc.layers.find((x) => x.id === id);
        if (l && !l.locked) orig[id] = l;
      }
      dispatch({ type: "begin" });
      drag.current = { kind: "move", wx: w.x, wy: w.y, orig, moved: false };
      return;
    }
    if (!e.shiftKey) dispatch({ type: "select", ids: [] });
    drag.current = { kind: "marquee", wx: w.x, wy: w.y, base: e.shiftKey ? state.selection : [] };
  };

  const onPointerMove = (e: PointerEvent) => {
    const d = drag.current;
    if (!d) {
      setHover(layerAt(e.target)?.id ?? null);
      return;
    }
    if (e.buttons === 0) { // the release was missed (off-window, interrupted click): never keep dragging
      onPointerUp();
      return;
    }
    const w = toWorld(e);
    if (d.kind === "pan") {
      setCam({ ...d.cam, x: d.cam.x + e.clientX - d.sx, y: d.cam.y + e.clientY - d.sy });
    } else if (d.kind === "move") {
      let dx = w.x - d.wx, dy = w.y - d.wy;
      if (!d.moved && Math.hypot(dx, dy) * camRef.current.z < 3) return; // a click is not a drag
      d.moved = true;
      if (e.shiftKey) { if (Math.abs(dx) > Math.abs(dy)) dy = 0; else dx = 0; } // lock to an axis
      const ids = Object.keys(d.orig);
      const box = union(ids.map((id) => rectOf(d.orig[id])));
      let g: Guide[] = [];
      if (box && !(e.ctrlKey || e.metaKey)) { // hold cmd/ctrl to move without snapping
        const s = snap({ ...box, x: box.x + dx, y: box.y + dy }, docRef.current, new Set(ids), 6 / camRef.current.z);
        dx += s.dx; dy += s.dy; g = s.guides;
      }
      setGuides(g);
      const patch: LayerPatch = {};
      for (const id of ids) patch[id] = { x: r(d.orig[id].x + dx), y: r(d.orig[id].y + dy) };
      dispatch({ type: "preview", patch });
    } else if (d.kind === "resize") {
      dispatch({ type: "preview", patch: { [d.orig.id]: resize(d.orig, d.handle, w.x - d.wx, w.y - d.wy, e.shiftKey) } });
    } else if (d.kind === "marquee") {
      const m = { x: Math.min(d.wx, w.x), y: Math.min(d.wy, w.y), w: Math.abs(w.x - d.wx), h: Math.abs(w.y - d.wy) };
      setMarquee(m);
      const inside = docRef.current.layers.filter((l) => canvasPickable(l) && intersects(m, rectOf(l))).map((l) => l.id);
      dispatch({ type: "select", ids: [...new Set([...d.base, ...inside])] });
    } else if (d.kind === "draw") {
      let wd = w.x - d.wx, ht = w.y - d.wy;
      if (e.shiftKey) { const s = Math.max(Math.abs(wd), Math.abs(ht)); wd = Math.sign(wd || 1) * s; ht = Math.sign(ht || 1) * s; }
      dispatch({ type: "preview", patch: { [d.id]: {
        x: r(Math.min(d.wx, d.wx + wd)), y: r(Math.min(d.wy, d.wy + ht)), w: Math.max(1, r(Math.abs(wd))), h: Math.max(1, r(Math.abs(ht))) } } });
    }
  };

  const onPointerUp = () => {
    const d = drag.current;
    drag.current = null;
    setGuides([]);
    setMarquee(null);
    setPanning(space.current);
    if (!d) return;
    if (d.kind === "draw") {
      const l = docRef.current.layers.find((x) => x.id === d.id);
      if (l && l.w < 3 && l.h < 3) dispatch({ type: "preview", patch: { [d.id]: { w: 100, h: 100 } } }); // a click makes a 100×100
      dispatch({ type: "end" });
      p.setTool("move");
    } else if (d.kind === "move" || d.kind === "resize") {
      dispatch({ type: "end" });
    }
  };

  const upRef = useRef(onPointerUp);
  upRef.current = onPointerUp;
  useEffect(() => {
    const up = () => { if (drag.current) upRef.current(); };
    window.addEventListener("pointerup", up);
    window.addEventListener("pointercancel", up);
    window.addEventListener("blur", up);
    return () => {
      window.removeEventListener("pointerup", up);
      window.removeEventListener("pointercancel", up);
      window.removeEventListener("blur", up);
    };
  }, []);

  const onDoubleClick = (e: MouseEvent) => {
    const hit = layerAt(e.target);
    if (hit && (hit.type === "text" || hit.type === "button" || hit.type === "pill")) {
      dispatch({ type: "select", ids: [hit.id] });
      setEditingId(hit.id);
    }
  };

  const finishEdit = (id: string) => (text: string | null) => {
    setEditingId(null);
    const l = docRef.current.layers.find((x) => x.id === id);
    if (!l || text === null || text === l.text) return;
    if (!text.trim() && l.type === "text") { // emptied text is deleted, like Figma
      dispatch({ type: "commit", layers: docRef.current.layers.filter((x) => x.id !== id) });
      return;
    }
    dispatch({ type: "commit", patch: { [id]: { text, ranges: remapRanges(l.text, text, l.ranges) } } });
  };

  // ---------- screen-space overlay ----------
  const toScreen = (rc: Rect) => ({ x: cam.x + rc.x * cam.z, y: cam.y + rc.y * cam.z, w: rc.w * cam.z, h: rc.h * cam.z });
  const selected = doc.layers.filter((l) => state.selection.includes(l.id));
  const selBox = union(selected.map(rectOf));
  const single = selected.length === 1 ? selected[0] : null;
  const handles = single && !single.locked && editingId !== single.id
    ? (single.type === "text" ? CORNERS : HANDLES) : [];
  const hovered = hover && !state.selection.includes(hover) ? doc.layers.find((l) => l.id === hover) : null;
  const cursor = panning || tool === "hand" ? (drag.current?.kind === "pan" ? "grabbing" : "grab")
    : tool === "text" ? "text" : tool === "rect" ? "crosshair" : "default";

  return (
    <div
      className="viewport"
      ref={vp}
      style={{ cursor }}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onPointerCancel={onPointerUp}
      onPointerLeave={() => setHover(null)}
      onDoubleClick={onDoubleClick}
      onContextMenu={(e) => e.preventDefault()}
      onDragOver={(e) => e.preventDefault()}
      onDrop={(e) => {
        e.preventDefault();
        const f = [...e.dataTransfer.files].find((x) => x.type.startsWith("image/"));
        if (f) p.onDropFile(f);
      }}
    >
      <div className="world" style={{ transform: `translate(${cam.x}px, ${cam.y}px) scale(${cam.z})` }}>
        <div className="frame-label" style={{ top: -22 / cam.z, fontSize: 12 / cam.z }}>
          {doc.source_name.replace(/\.[^.]+$/, "")} <span>{doc.width} × {doc.height}</span>
        </div>
        <div className="frame" style={{ width: doc.width, height: doc.height }}>
          {doc.layers.map((l) => (
            <LayerView key={l.id} l={l} editing={editingId === l.id} register={register} onEditDone={finishEdit(l.id)} />
          ))}
          {view.reference > 0 && (
            <img className="reference" src={fileUrl(doc.files.original)} style={{ opacity: view.reference }} alt="" draggable={false} />
          )}
          {view.boxes && <img className="reference" src={fileUrl(doc.files.overlay)} style={{ opacity: 0.85 }} alt="" draggable={false} />}
        </div>
      </div>

      <svg className="overlay">
        {hovered && (() => { const s = toScreen(rectOf(hovered)); return <rect className="hover" x={s.x} y={s.y} width={s.w} height={s.h} />; })()}
        {selected.length > 1 && selected.map((l) => {
          const s = toScreen(rectOf(l));
          return <rect key={l.id} className="sel-item" x={s.x} y={s.y} width={s.w} height={s.h} />;
        })}
        {selBox && (() => {
          const s = toScreen(selBox);
          return (
            <g>
              <rect className="sel" x={s.x} y={s.y} width={s.w} height={s.h} />
              {handles.map((h) => {
                const hx = s.x + (h.includes("w") ? 0 : h.includes("e") ? s.w : s.w / 2);
                const hy = s.y + (h.includes("n") ? 0 : h.includes("s") ? s.h : s.h / 2);
                return <rect key={h} className={`handle h-${h}`} data-handle={h} x={hx - 4} y={hy - 4} width={8} height={8} />;
              })}
              {drag.current?.kind !== "pan" && (
                <text className="size-tag" x={s.x + s.w / 2} y={s.y + s.h + 18}>{r(selBox.w)} × {r(selBox.h)}</text>
              )}
            </g>
          );
        })()}
        {guides.map((g, i) => {
          if (g.axis === "x") {
            const x = cam.x + g.pos * cam.z;
            return <line key={i} className="guide" x1={x} x2={x} y1={cam.y + g.from * cam.z} y2={cam.y + g.to * cam.z} />;
          }
          const y = cam.y + g.pos * cam.z;
          return <line key={i} className="guide" y1={y} y2={y} x1={cam.x + g.from * cam.z} x2={cam.x + g.to * cam.z} />;
        })}
        {marquee && (() => { const s = toScreen(marquee); return <rect className="marquee" x={s.x} y={s.y} width={s.w} height={s.h} />; })()}
      </svg>
    </div>
  );
}

/** New geometry for a layer dragged by one of its handles. Text scales its font (corner handles only). */
function resize(o: Layer, h: Handle, dx: number, dy: number, keepRatio: boolean): Partial<Layer> {
  let x = o.x, y = o.y, w = o.w, ht = o.h;
  if (h.includes("e")) w = o.w + dx;
  if (h.includes("w")) { w = o.w - dx; x = o.x + dx; }
  if (h.includes("s")) ht = o.h + dy;
  if (h.includes("n")) { ht = o.h - dy; y = o.y + dy; }
  if (o.type === "text" || (keepRatio && h.length === 2)) { // proportional
    const k = Math.max(0.05, Math.abs(w) / Math.max(1, o.w));
    w = o.w * k; ht = o.h * k;
    if (h.includes("w")) x = o.x + o.w - w;
    if (h.includes("n")) y = o.y + o.h - ht;
    if (o.type === "text")
      return { x: Math.round(x), y: Math.round(y), font_size: Math.max(1, Math.round(o.font_size * k)),
        line_height: o.line_height ? Math.round(o.line_height * k) : 0, w: Math.round(w), h: Math.round(ht) };
  }
  if (w < 1) { x += w - 1; w = 1; }
  if (ht < 1) { y += ht - 1; ht = 1; }
  return { x: Math.round(x), y: Math.round(y), w: Math.round(w), h: Math.round(ht) };
}
