// Small inputs that commit on Enter / blur (one undo step per edit), like Figma's property fields.
import { useEffect, useState, type ReactNode } from "react";

export function NumField({ label, value, onCommit, disabled, min }: {
  label: ReactNode; value: number; onCommit: (v: number) => void; disabled?: boolean; min?: number;
}) {
  const [v, setV] = useState(String(value));
  useEffect(() => setV(String(value)), [value]);
  const commit = (raw: string) => {
    let n = Number(raw);
    if (!Number.isFinite(n)) return setV(String(value));
    if (min !== undefined) n = Math.max(min, n);
    n = Math.round(n);
    if (n !== value) onCommit(n);
    setV(String(n));
  };
  return (
    <label className="field">
      <span className="field-label">{label}</span>
      <input
        value={v}
        disabled={disabled}
        inputMode="numeric"
        onChange={(e) => setV(e.target.value)}
        onBlur={(e) => commit(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") (e.target as HTMLInputElement).blur();
          if (e.key === "ArrowUp" || e.key === "ArrowDown") {
            e.preventDefault();
            commit(String(Number(v) + (e.key === "ArrowUp" ? 1 : -1) * (e.shiftKey ? 10 : 1)));
          }
          if (e.key === "Escape") { setV(String(value)); (e.target as HTMLInputElement).blur(); }
        }}
      />
    </label>
  );
}

export function TextField({ value, onCommit, placeholder, list }: {
  value: string; onCommit: (v: string) => void; placeholder?: string; list?: string;
}) {
  const [v, setV] = useState(value);
  useEffect(() => setV(value), [value]);
  return (
    <input
      className="text-field"
      value={v}
      list={list}
      placeholder={placeholder}
      onChange={(e) => setV(e.target.value)}
      onBlur={() => { if (v.trim() && v !== value) onCommit(v.trim()); else setV(value); }}
      onKeyDown={(e) => {
        if (e.key === "Enter") (e.target as HTMLInputElement).blur();
        if (e.key === "Escape") { setV(value); (e.target as HTMLInputElement).blur(); }
      }}
    />
  );
}

export function ColorField({ value, onCommit }: { value: string; onCommit: (v: string) => void }) {
  const [hex, setHex] = useState(value.replace("#", ""));
  useEffect(() => setHex(value.replace("#", "")), [value]);
  const commitHex = (raw: string) => {
    const h = raw.replace("#", "").trim();
    const full = h.length === 3 ? h.split("").map((c) => c + c).join("") : h;
    if (/^[0-9a-f]{6}$/i.test(full)) { if (`#${full.toUpperCase()}` !== value) onCommit(`#${full.toUpperCase()}`); }
    else setHex(value.replace("#", ""));
  };
  return (
    <div className="color-field">
      <input type="color" value={/^#[0-9a-f]{6}$/i.test(value) ? value : "#000000"}
        onChange={(e) => onCommit(e.target.value.toUpperCase())} aria-label="Colour" />
      <input className="hex" value={hex} onChange={(e) => setHex(e.target.value)}
        onBlur={(e) => commitHex(e.target.value)}
        onKeyDown={(e) => { if (e.key === "Enter") (e.target as HTMLInputElement).blur(); }} aria-label="Hex colour" />
    </div>
  );
}
