// Right panel: properties of the selection, or of the whole design when nothing is selected.
import { useState, type Dispatch } from "react";
import { STYLE_OPTIONS } from "../fonts";
import type { Doc, Layer } from "../types";
import { align, union, rectOf, type AlignKind } from "./geometry";
import { ColorField, NumField, TextField } from "./fields";
import { Icon } from "./icons";
import type { Action, EditorState, LayerPatch } from "./store";

const FONT_SUGGESTIONS = ["Inter", "Poppins", "Roboto", "Montserrat", "Open Sans", "Lato", "DM Sans", "Manrope",
  "Plus Jakarta Sans", "Work Sans", "Nunito", "Raleway", "Playfair Display", "Lora", "Merriweather",
  "DM Serif Display", "Cormorant Garamond", "Libre Baskerville", "Dancing Script", "Great Vibes"];

const ALIGNS: [AlignKind, () => JSX.Element, string][] = [
  ["left", Icon.alignLeft, "Align left"], ["hcenter", Icon.alignHCenter, "Align horizontal centres"],
  ["right", Icon.alignRight, "Align right"], ["top", Icon.alignTop, "Align top"],
  ["vcenter", Icon.alignVCenter, "Align vertical centres"], ["bottom", Icon.alignBottom, "Align bottom"],
];

export default function DesignPanel({ state, dispatch, onRefit }: {
  state: EditorState; dispatch: Dispatch<Action>; onRefit: (widths: Record<string, number>) => void;
}) {
  const doc = state.doc!;
  const sel = doc.layers.filter((l) => state.selection.includes(l.id));
  const commit = (patch: LayerPatch) => dispatch({ type: "commit", patch });
  const all = (p: Partial<Layer>): LayerPatch => Object.fromEntries(sel.map((l) => [l.id, p]));

  if (!sel.length) return <DocumentPanel doc={doc} dispatch={dispatch} onRefit={onRefit} />;

  const one = sel.length === 1 ? sel[0] : null;
  const box = union(sel.map(rectOf))!;
  const texts = sel.filter((l) => l.type === "text" || l.type === "button" || l.type === "pill");
  const filled = sel.filter((l) => l.type === "button" || l.type === "pill" || l.type === "rect");
  const t = texts[0];
  const f = filled[0];
  const textPatch = (p: Partial<Layer>): LayerPatch => Object.fromEntries(texts.map((l) => [l.id, p]));
  const fillPatch = (p: Partial<Layer>): LayerPatch => Object.fromEntries(filled.map((l) => [l.id, p]));

  return (
    <>
      <section className="panel">
        <div className="align-row">
          {ALIGNS.map(([k, I, title]) => (
            <button key={k} className="icon-btn" title={title} onClick={() => commit(align(sel, doc, k))}><I /></button>
          ))}
        </div>
      </section>

      <section className="panel">
        <h3>{one ? one.name : `${sel.length} layers`}</h3>
        <div className="grid2">
          <NumField label="X" value={box.x} onCommit={(v) => commit(Object.fromEntries(sel.map((l) => [l.id, { x: l.x + v - box.x }])))} />
          <NumField label="Y" value={box.y} onCommit={(v) => commit(Object.fromEntries(sel.map((l) => [l.id, { y: l.y + v - box.y }])))} />
          <NumField label="W" value={box.w} disabled={!one || one.type === "text"} min={1} onCommit={(v) => one && commit({ [one.id]: { w: v } })} />
          <NumField label="H" value={box.h} disabled={!one || one.type === "text"} min={1} onCommit={(v) => one && commit({ [one.id]: { h: v } })} />
        </div>
        {one?.type === "text" && <p className="muted small">Text boxes size to their content. Drag a corner to scale.</p>}
      </section>

      {t && (
        <section className="panel">
          <h3>Text</h3>
          <TextField value={t.font_family} list="font-list" placeholder="Font family"
            onCommit={(v) => commit(textPatch({ font_family: v }))} />
          <datalist id="font-list">{FONT_SUGGESTIONS.map((n) => <option key={n} value={n} />)}</datalist>
          <div className="grid2">
            <select value={t.font_style} onChange={(e) => commit(textPatch({ font_style: e.target.value }))} aria-label="Font style">
              {[t.font_style, ...STYLE_OPTIONS.filter((s) => s !== t.font_style)].map((s) => <option key={s}>{s}</option>)}
            </select>
            <NumField label="Size" value={t.font_size} min={1} onCommit={(v) => commit(textPatch({ font_size: v }))} />
            <NumField label="Line" value={t.line_height} min={0} onCommit={(v) => commit(textPatch({ line_height: v }))} />
            <NumField label="Letter %" value={Math.round(t.letter_spacing)} onCommit={(v) => commit(textPatch({ letter_spacing: v }))} />
          </div>
          <div className="seg">
            {(["LEFT", "CENTER", "RIGHT"] as const).map((a) => {
              const I = a === "LEFT" ? Icon.textLeft : a === "CENTER" ? Icon.textCenter : Icon.textRight;
              return <button key={a} className={`icon-btn${t.align === a ? " on" : ""}`} title={`Align ${a.toLowerCase()}`}
                onClick={() => commit(textPatch({ align: a }))}><I /></button>;
            })}
          </div>
          <ColorField value={t.color} onCommit={(v) => commit(textPatch({ color: v }))} />
          {texts.some((l) => l.ranges.length > 0) && (
            <div className="note">
              Mixed styles (e.g. an italic serif word) are kept, also when you edit the text.{" "}
              <button className="link" onClick={() => commit(textPatch({ ranges: [] }))}>Clear them</button>
            </div>
          )}
        </section>
      )}

      {f && (
        <section className="panel">
          <h3>Fill</h3>
          <ColorField value={f.fill} onCommit={(v) => commit(fillPatch({ fill: v }))} />
          <div className="grid2">
            <NumField label="Radius" value={f.corner_radius} min={0} onCommit={(v) => commit(fillPatch({ corner_radius: v }))} />
          </div>
        </section>
      )}

      <section className="panel">
        <h3>Layer</h3>
        <div className="grid2">
          <label className="check"><input type="checkbox" checked={sel.every((l) => l.visible)}
            onChange={(e) => commit(all({ visible: e.target.checked }))} /> Visible</label>
          <label className="check"><input type="checkbox" checked={sel.every((l) => l.locked)}
            onChange={(e) => commit(all({ locked: e.target.checked }))} /> Locked</label>
        </div>
      </section>
    </>
  );
}

function DocumentPanel({ doc, dispatch, onRefit }: {
  doc: Doc; dispatch: Dispatch<Action>; onRefit: (widths: Record<string, number>) => void;
}) {
  const [keepWidths, setKeepWidths] = useState(true);
  const families = new Map<string, number>();
  for (const l of doc.layers) {
    if (l.font_family) families.set(l.font_family, (families.get(l.font_family) ?? 0) + 1);
    for (const r of l.ranges) if (r.font_family) families.set(r.font_family, (families.get(r.font_family) ?? 0) + 1);
  }
  const replace = (from: string, to: string) => {
    // Text layers set in that font keep their current width (the size adapts to the new font)
    const widths = Object.fromEntries(doc.layers
      .filter((l) => l.type === "text" && l.font_family === from && (l.fit_w || l.w) > 0)
      .map((l) => [l.id, l.fit_w || l.w]));
    dispatch({
      type: "commit",
      layers: doc.layers.map((l) => ({
        ...l,
        font_family: l.font_family === from ? to : l.font_family,
        ranges: l.ranges.map((r) => (r.font_family === from ? { ...r, font_family: to } : r)),
      })),
    });
    if (keepWidths) onRefit(widths);
  };
  const total = Object.values(doc.timings).reduce((a, b) => a + b, 0);
  return (
    <>
      <section className="panel">
        <h3>Frame</h3>
        <p className="kv"><span>Source</span><b title={doc.source_name}>{doc.source_name}</b></p>
        <p className="kv"><span>Size</span><b>{doc.width} × {doc.height}</b></p>
        <p className="kv"><span>Layers</span><b>{doc.layers.length}</b></p>
        <p className="kv"><span>Converted in</span><b>{total.toFixed(0)} s</b></p>
      </section>
      <section className="panel">
        <h3>Fonts in this design</h3>
        <p className="muted small">Replace a font everywhere, e.g. with your brand font.</p>
        <label className="check small"><input type="checkbox" checked={keepWidths} onChange={(e) => setKeepWidths(e.target.checked)} />
          Keep text widths (resize to match the original)</label>
        <datalist id="font-list">{FONT_SUGGESTIONS.map((n) => <option key={n} value={n} />)}</datalist>
        {[...families].map(([fam, n]) => (
          <div key={fam} className="font-row">
            <TextField value={fam} list="font-list" onCommit={(v) => replace(fam, v)} />
            <span className="muted small">{n}</span>
          </div>
        ))}
      </section>
      {doc.warnings.length > 0 && (
        <section className="panel">
          <h3>Notes</h3>
          {doc.warnings.map((w) => <p key={w} className="muted small">{w}</p>)}
        </section>
      )}
      <section className="panel">
        <h3>Send to Figma</h3>
        <p className="muted small">
          Edits save automatically. In Figma desktop run the <b>Image to Figma</b> plugin → <b>Recent conversions</b> →
          <b> Import</b>.
        </p>
      </section>
    </>
  );
}
