import { useState } from "react";
import type { Options } from "../types";

interface Props {
  options: Options;
  setOptions: (o: Options) => void;
  busy: boolean;
  onConvert: (file: File) => void;
}

export default function Uploader({ options, setOptions, busy, onConvert }: Props) {
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState("");
  const [over, setOver] = useState(false);

  const pick = (f?: File | null) => {
    if (!f || !f.type.startsWith("image/")) return;
    setFile(f);
    setPreview((old) => { if (old) URL.revokeObjectURL(old); return URL.createObjectURL(f); });
  };

  const set = <K extends keyof Options>(k: K, v: Options[K]) => setOptions({ ...options, [k]: v });

  return (
    <section className="panel">
      <label
        className={`drop${over ? " over" : ""}`}
        onDragOver={(e) => { e.preventDefault(); setOver(true); }}
        onDragLeave={() => setOver(false)}
        onDrop={(e) => { e.preventDefault(); setOver(false); pick(e.dataTransfer.files[0]); }}
      >
        <input type="file" accept="image/*" hidden onChange={(e) => pick(e.target.files?.[0])} />
        {preview ? (
          <>
            <img src={preview} alt="" />
            <span className="muted">{file?.name}</span>
          </>
        ) : (
          <span>Drop an image or click to choose<br /><span className="muted">or paste a screenshot anywhere (⌘V)</span></span>
        )}
      </label>

      <div className="opts">
        <label className="check">
          <input type="checkbox" checked={options.cutout_subject} onChange={(e) => set("cutout_subject", e.target.checked)} />
          Cut out main subject
        </label>
        <label className="check">
          <input type="checkbox" checked={options.use_llm} onChange={(e) => set("use_llm", e.target.checked)} />
          AI labels (Qwen3-VL)
        </label>
        <label>
          Scale
          <select value={options.scale} onChange={(e) => set("scale", +e.target.value)}>
            <option value={1}>1× (ad / export)</option>
            <option value={2}>2× (retina screenshot)</option>
          </select>
        </label>
        <label>Sans font<input value={options.sans_font} onChange={(e) => set("sans_font", e.target.value)} /></label>
        <label>Serif font<input value={options.serif_font} onChange={(e) => set("serif_font", e.target.value)} /></label>
      </div>

      <button className="primary" disabled={!file || busy} onClick={() => file && onConvert(file)}>
        {busy ? "Converting…" : "Convert"}
      </button>
    </section>
  );
}
