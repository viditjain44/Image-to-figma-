// Draws one layer inside the frame, the way the Figma plugin will build it.
import { useEffect, useRef, type CSSProperties, type ReactNode } from "react";
import { fileUrl } from "../api";
import { fontCss } from "../fonts";
import type { Layer } from "../types";

/** Splits the text into spans so per-range colours and fonts show, like in Figma. */
function richText(l: Layer): ReactNode {
  if (!l.ranges.length) return l.text;
  const styles: CSSProperties[] = Array.from({ length: l.text.length }, () => ({}));
  for (const r of l.ranges) {
    for (let i = r.start; i < Math.min(r.end, l.text.length); i++) {
      if (r.color) styles[i] = { ...styles[i], color: r.color };
      if (r.font_family || r.font_style)
        styles[i] = { ...styles[i], ...fontCss(r.font_family || l.font_family, r.font_style || l.font_style) };
    }
  }
  const out: ReactNode[] = [];
  let start = 0;
  for (let i = 1; i <= l.text.length; i++) {
    if (i === l.text.length || JSON.stringify(styles[i]) !== JSON.stringify(styles[start])) {
      out.push(<span key={start} style={styles[start]}>{l.text.slice(start, i)}</span>);
      start = i;
    }
  }
  return out;
}

export function textStyle(l: Layer): CSSProperties {
  return {
    ...fontCss(l.font_family, l.font_style),
    fontSize: l.font_size,
    lineHeight: l.line_height ? `${l.line_height}px` : "normal",
    color: l.color,
    letterSpacing: l.letter_spacing ? `${l.letter_spacing / 100}em` : undefined,
    whiteSpace: "pre",
    textAlign: l.align.toLowerCase() as CSSProperties["textAlign"],
  };
}

interface Props {
  l: Layer;
  editing: boolean;
  register: (id: string, el: HTMLElement | null) => void; // text layers report their rendered size
  onEditDone: (text: string | null) => void;            // null = no change
}

export default function LayerView({ l, editing, register, onEditDone }: Props) {
  if (!l.visible) return null;
  const box: CSSProperties = { left: l.x, top: l.y, width: l.w, height: l.h };
  if (l.type === "image")
    return <img className="layer" data-layer-id={l.id} src={fileUrl(l.src)} draggable={false} style={box} alt="" />;
  // Borders are drawn inside the box, like a Figma "inside" stroke
  const border: CSSProperties = l.stroke && l.stroke_width ? { boxShadow: `inset 0 0 0 ${l.stroke_width}px ${l.stroke}` } : {};
  if (l.type === "rect")
    return <div className="layer" data-layer-id={l.id} style={{ ...box, background: l.fill, borderRadius: l.corner_radius, ...border }} />;
  if (l.type === "button" || l.type === "pill") {
    const icon = l.icon_src
      ? <img src={fileUrl(l.icon_src)} alt="" draggable={false} style={{ width: l.icon_w, height: l.icon_h, flex: "none" }} />
      : null;
    const label = editing ? <TextEditor l={l} onDone={onEditDone} style={textStyle({ ...l, line_height: 0 })} />
      : <span style={textStyle({ ...l, line_height: 0 })}>{richText(l)}</span>;
    return (
      <div className="layer button" data-layer-id={l.id}
        style={{ ...box, background: l.fill, borderRadius: l.corner_radius, gap: l.gap || 0, ...border }}>
        {l.icon_side !== "right" && icon}{label}{l.icon_side === "right" && icon}
      </div>
    );
  }
  // Text is auto-width: its box follows the rendered text, reported through `register`
  return (
    <div className="layer text" data-layer-id={l.id} ref={(el) => register(l.id, el)}
      style={{ left: l.x, top: l.y, ...textStyle(l) }}>
      {editing ? <TextEditor l={l} onDone={onEditDone} /> : richText(l)}
    </div>
  );
}

function TextEditor({ l, onDone, style }: { l: Layer; onDone: (t: string | null) => void; style?: CSSProperties }) {
  const ref = useRef<HTMLSpanElement>(null);
  const done = useRef(false);
  useEffect(() => {
    const el = ref.current!;
    el.textContent = l.text;
    el.focus();
    const range = document.createRange();
    range.selectNodeContents(el);
    getSelection()?.removeAllRanges();
    getSelection()?.addRange(range);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  const finish = (_cancel: boolean) => {
    if (done.current) return;
    done.current = true;
    onDone((ref.current?.innerText ?? l.text).replace(/\n$/, ""));
  };
  return (
    <span
      ref={ref}
      className="text-editor"
      style={style}
      // plaintext-only keeps pasted styling out
      contentEditable={"plaintext-only" as unknown as boolean}
      suppressContentEditableWarning
      onBlur={() => finish(false)}
      onPointerDown={(e) => e.stopPropagation()}
      onKeyDown={(e) => {
        e.stopPropagation();
        // Like Figma: Escape (or ⌘Enter) keeps what you typed and leaves editing; ⌘Z inside undoes typing
        if (e.key === "Escape" || (e.key === "Enter" && (e.metaKey || e.ctrlKey))) { e.preventDefault(); finish(false); }
      }}
    />
  );
}
