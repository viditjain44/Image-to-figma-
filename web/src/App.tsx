import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import { api, fileUrl } from "./api";
import Uploader from "./components/Uploader";
import DesignPanel from "./editor/DesignPanel";
import { renderDesign, saveBlob, type ImageFormat } from "./editor/exportImage";
import { Icon } from "./editor/icons";
import LayersPanel from "./editor/LayersPanel";
import { canvasPickable, initialState, newId, reducer } from "./editor/store";
import Workspace, { type Camera, type Tool, type ViewOptions } from "./editor/Workspace";
import type { Job, JobSummary, Layer, Options } from "./types";

const DEFAULT_OPTIONS: Options = {
  cutout_subject: true, use_llm: true, scale: 1,
  sans_font: "Inter", serif_font: "Playfair Display", script_font: "Dancing Script",
};
const TOOLS: [Tool, () => JSX.Element, string][] = [
  ["move", Icon.move, "Move (V)"], ["hand", Icon.hand, "Hand (H, or hold Space)"],
  ["rect", Icon.rect, "Rectangle (R)"], ["text", Icon.text, "Text (T)"],
];

const typing = (t: EventTarget | null) =>
  !!(t as HTMLElement | null)?.closest?.("input, textarea, select, [contenteditable]");

export default function App() {
  const [state, dispatch] = useReducer(reducer, initialState);
  const [online, setOnline] = useState<boolean | null>(null);
  const [options, setOptions] = useState<Options>(DEFAULT_OPTIONS);
  const [jobs, setJobs] = useState<JobSummary[]>([]);
  const [jobId, setJobId] = useState<string | null>(null);
  const [job, setJob] = useState<Job | null>(null);
  const [error, setError] = useState("");
  const [tab, setTab] = useState<"layers" | "files">("files");
  const [tool, setTool] = useState<Tool>("move");
  const [cam, setCam] = useState<Camera>({ x: 0, y: 0, z: 1 });
  const [fitSignal, setFitSignal] = useState(0);
  const [view, setView] = useState<ViewOptions>({ reference: 0, boxes: false });
  const [editingId, setEditingId] = useState<string | null>(null);
  const [saving, setSaving] = useState<"idle" | "saving" | "error">("idle");
  const [zoomMenu, setZoomMenu] = useState(false);
  const [refit, setRefit] = useState<{ widths: Record<string, number>; nonce: number } | null>(null);
  const doc = state.doc;

  const refreshJobs = useCallback(() => api.jobs().then(setJobs).catch(() => {}), []);

  // Keep checking the server, so starting it after the UI just works
  useEffect(() => {
    let wasOnline = false;
    const check = () =>
      api.health()
        .then(() => {
          setOnline(true);
          if (!wasOnline) {
            wasOnline = true;
            api.defaults().then(setOptions).catch(() => {});
            refreshJobs();
          }
        })
        .catch(() => { wasOnline = false; setOnline(false); });
    check();
    const id = setInterval(check, 4000);
    return () => clearInterval(id);
  }, [refreshJobs]);

  // Poll the current job until it finishes, then open it in the editor
  useEffect(() => {
    if (!jobId) return;
    let stop = false;
    const tick = async () => {
      try {
        const j = await api.job(jobId);
        if (stop) return;
        setJob(j);
        if (j.status === "done" && j.document) {
          dispatch({ type: "load", doc: j.document });
          setTab("layers");
          refreshJobs();
          return;
        }
        if (j.status === "error") return;
        setTimeout(tick, 1000);
      } catch (e) {
        if (!stop) setError(String(e));
      }
    };
    tick();
    return () => { stop = true; };
  }, [jobId, refreshJobs]);

  // Autosave a moment after the last edit (⌘S saves right away)
  const savingDoc = useRef(false);
  const saveNow = useCallback(async () => {
    if (!state.doc || savingDoc.current) return;
    const version = state.version;
    savingDoc.current = true;
    setSaving("saving");
    try {
      await api.save(state.doc);
      dispatch({ type: "saved", version });
      setSaving("idle");
    } catch (e) {
      setSaving("error");
      setError(`Could not save: ${e}`);
    } finally {
      savingDoc.current = false;
    }
  }, [state.doc, state.version]);
  useEffect(() => {
    if (!doc || state.version === state.savedVersion) return;
    const t = setTimeout(saveNow, 1000);
    return () => clearTimeout(t);
  }, [doc, state.version, state.savedVersion, saveNow]);

  // Paste a screenshot anywhere to convert it
  const convertRef = useRef<(f: File) => void>(() => {});
  useEffect(() => {
    const onPaste = (e: ClipboardEvent) => {
      if (typing(e.target)) return;
      const blob = [...(e.clipboardData?.items ?? [])].find((i) => i.type.startsWith("image/"))?.getAsFile();
      if (blob) { e.preventDefault(); convertRef.current(new File([blob], "pasted.png", { type: blob.type })); }
    };
    window.addEventListener("paste", onPaste);
    return () => window.removeEventListener("paste", onPaste);
  }, []);

  const open = (id: string) => {
    if (id === jobId && doc) return;
    setEditingId(null);
    dispatch({ type: "close" });
    setJob(null); setError(""); setJobId(id);
  };

  const convert = async (file: File) => {
    setError("");
    try {
      const { job_id } = await api.convert(file, options);
      open(job_id);
    } catch (e) {
      setError(String(e));
    }
  };

  convertRef.current = convert;

  const baseName = doc ? doc.source_name.replace(/\.[^.]+$/, "") : "design";
  const [exporting, setExporting] = useState(false);
  const [exportMenu, setExportMenu] = useState(false);

  const downloadLayers = () => {
    if (!doc) return;
    saveBlob(new Blob([JSON.stringify(doc, null, 2)], { type: "application/json" }), `${baseName}.figma-layers.json`);
  };

  const downloadImage = async (format: ImageFormat, scale: number) => {
    if (!doc) return;
    setExportMenu(false);
    setEditingId(null);
    setExporting(true);
    try {
      const blob = await renderDesign(doc, format, scale);
      saveBlob(blob, `${baseName}${scale !== 1 ? `@${scale}x` : ""}.${format}`);
    } catch (e) {
      setError(`Export failed: ${e}`);
    } finally {
      setExporting(false);
    }
  };

  const zoomTo = useCallback((z: number) => {
    const el = document.querySelector(".viewport");
    if (!el) return;
    const cx = el.clientWidth / 2, cy = el.clientHeight / 2;
    setCam((c) => ({ z, x: cx - ((cx - c.x) / c.z) * z, y: cy - ((cy - c.y) / c.z) * z }));
  }, []);

  // ---------- keyboard shortcuts (Figma's) ----------
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (typing(e.target) || !doc) return;
      const mod = e.metaKey || e.ctrlKey;
      const key = e.key.toLowerCase();
      const selLayers = doc.layers.filter((l) => state.selection.includes(l.id));
      const editable = selLayers.filter((l) => !l.locked);
      if (mod && key === "z") { e.preventDefault(); dispatch({ type: e.shiftKey ? "redo" : "undo" }); return; }
      if (mod && key === "y") { e.preventDefault(); dispatch({ type: "redo" }); return; }
      if (mod && key === "s") { e.preventDefault(); saveNow(); return; }
      if (mod && e.shiftKey && key === "e") { e.preventDefault(); downloadImage("png", 1); return; }
      if (mod && key === "a") {
        e.preventDefault();
        dispatch({ type: "select", ids: doc.layers.filter(canvasPickable).map((l) => l.id) });
        return;
      }
      if (mod && key === "d" && editable.length) {
        e.preventDefault();
        const copies: Layer[] = editable.map((l) => ({ ...l, id: newId(l.type), x: l.x + 10, y: l.y + 10, name: `${l.name} copy` }));
        dispatch({ type: "commit", layers: [...doc.layers, ...copies] });
        dispatch({ type: "select", ids: copies.map((c) => c.id) });
        return;
      }
      if (mod && (key === "]" || key === "[") && editable.length) { // bring forward / send backward
        e.preventDefault();
        const ids = new Set(editable.map((l) => l.id));
        const layers = [...doc.layers];
        const order = key === "]" ? [...layers.keys()].reverse() : [...layers.keys()];
        for (const i of order) {
          const j = key === "]" ? i + 1 : i - 1;
          if (ids.has(layers[i].id) && j >= 0 && j < layers.length && !ids.has(layers[j].id))
            [layers[i], layers[j]] = [layers[j], layers[i]];
        }
        dispatch({ type: "commit", layers });
        return;
      }
      if (mod && (key === "=" || key === "+")) { e.preventDefault(); zoomTo(Math.min(32, cam.z * 1.25)); return; }
      if (mod && key === "-") { e.preventDefault(); zoomTo(Math.max(0.02, cam.z / 1.25)); return; }
      if (mod && key === "0") { e.preventDefault(); zoomTo(1); return; }
      if (e.shiftKey && (key === "1" || key === "!")) { setFitSignal((n) => n + 1); return; }
      if (mod) return;
      if (key === "v") setTool("move");
      else if (key === "h") setTool("hand");
      else if (key === "r") setTool("rect");
      else if (key === "t") setTool("text");
      else if (key === "escape") { setTool("move"); dispatch({ type: "select", ids: [] }); }
      else if ((key === "backspace" || key === "delete") && editable.length) {
        e.preventDefault();
        const ids = new Set(editable.map((l) => l.id));
        dispatch({ type: "commit", layers: doc.layers.filter((l) => !ids.has(l.id)) });
      } else if (key === "enter" && selLayers.length === 1 && ["text", "button", "pill"].includes(selLayers[0].type)) {
        e.preventDefault();
        setEditingId(selLayers[0].id);
      } else if (key.startsWith("arrow") && editable.length) {
        e.preventDefault();
        const step = e.shiftKey ? 10 : 1;
        const dx = key === "arrowleft" ? -step : key === "arrowright" ? step : 0;
        const dy = key === "arrowup" ? -step : key === "arrowdown" ? step : 0;
        dispatch({ type: "commit", patch: Object.fromEntries(editable.map((l) => [l.id, { x: l.x + dx, y: l.y + dy }])) });
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [doc, state.selection, cam.z, zoomTo, saveNow, downloadImage]);

  const busy = !!job && (job.status === "queued" || job.status === "running");
  const saveLabel = !doc ? "" : saving === "saving" ? "Saving…" : saving === "error" ? "Not saved"
    : state.version === state.savedVersion ? "Saved" : "Editing…";

  return (
    <div className="app">
      <header className="topbar">
        <div className="tb-left">
          <span className="brand">Image → Figma</span>
          <div className="tools">
            {TOOLS.map(([t, I, title]) => (
              <button key={t} className={`tool${tool === t ? " on" : ""}`} title={title} disabled={!doc}
                onClick={() => setTool(t)}><I /></button>
            ))}
          </div>
        </div>
        <div className="tb-center">
          {doc ? <><b>{doc.source_name}</b><span className={`save ${saving}`}>{saveLabel}</span></> : <span className="muted">No file open</span>}
        </div>
        <div className="tb-right">
          {doc && (
            <>
              <label className="ref" title="Show the original image on top to check alignment">
                Original
                <input type="range" min={0} max={1} step={0.05} value={view.reference}
                  onChange={(e) => setView({ ...view, reference: +e.target.value })} aria-label="Original overlay opacity" />
              </label>
              <button className={`chip${view.boxes ? " on" : ""}`} title="Show what was detected"
                onClick={() => setView({ ...view, boxes: !view.boxes })}>Detected</button>
              <button className="tool" title="Undo (⌘Z)" disabled={!state.past.length} onClick={() => dispatch({ type: "undo" })}><Icon.undo /></button>
              <button className="tool" title="Redo (⇧⌘Z)" disabled={!state.future.length} onClick={() => dispatch({ type: "redo" })}><Icon.redo /></button>
              <div className="zoom">
                <button className="chip" onClick={() => setZoomMenu(!zoomMenu)}>{Math.round(cam.z * 100)}%</button>
                {zoomMenu && (
                  <div className="menu" onMouseLeave={() => setZoomMenu(false)}>
                    <button onClick={() => { zoomTo(cam.z * 1.25); setZoomMenu(false); }}>Zoom in <kbd>⌘+</kbd></button>
                    <button onClick={() => { zoomTo(cam.z / 1.25); setZoomMenu(false); }}>Zoom out <kbd>⌘−</kbd></button>
                    <button onClick={() => { setFitSignal((n) => n + 1); setZoomMenu(false); }}>Zoom to fit <kbd>⇧1</kbd></button>
                    <button onClick={() => { zoomTo(0.5); setZoomMenu(false); }}>50%</button>
                    <button onClick={() => { zoomTo(1); setZoomMenu(false); }}>100% <kbd>⌘0</kbd></button>
                    <button onClick={() => { zoomTo(2); setZoomMenu(false); }}>200%</button>
                  </div>
                )}
              </div>
              <div className="zoom">
                <button className="primary" disabled={exporting} onClick={() => setExportMenu(!exportMenu)}>
                  {exporting ? "Exporting…" : "Export"}
                </button>
                {exportMenu && (
                  <div className="menu" onMouseLeave={() => setExportMenu(false)}>
                    <button onClick={() => downloadImage("png", 1)}>PNG <kbd>{doc.width}×{doc.height}</kbd></button>
                    <button onClick={() => downloadImage("png", 2)}>PNG 2× <kbd>{doc.width * 2}×{doc.height * 2}</kbd></button>
                    <button onClick={() => downloadImage("jpg", 1)}>JPG <kbd>{doc.width}×{doc.height}</kbd></button>
                    <hr />
                    <button onClick={() => { downloadLayers(); setExportMenu(false); }}>Layers file (.json)</button>
                  </div>
                )}
              </div>
            </>
          )}
          <span className={`dot ${online ? "ok" : online === false ? "bad" : ""}`}
            title={online ? "Server running" : "Server offline: uv run img-to-figma serve"} />
        </div>
      </header>

      <aside className="left">
        <div className="tabs">
          <button className={tab === "layers" ? "on" : ""} disabled={!doc} onClick={() => setTab("layers")}>Layers</button>
          <button className={tab === "files" ? "on" : ""} onClick={() => setTab("files")}>Files</button>
        </div>
        <div className="left-body">
          {tab === "layers" && doc && (
            <LayersPanel state={state} dispatch={dispatch}
              onEdit={(id) => { dispatch({ type: "select", ids: [id] }); setEditingId(id); }} />
          )}
          {tab === "files" && (
            <>
              <Uploader options={options} setOptions={setOptions} busy={busy} onConvert={convert} />
              <section className="panel">
                <h3>Recent</h3>
                {online === false && <p className="muted small">Server offline. Run <code>uv run img-to-figma serve</code></p>}
                {jobs.length === 0 && online && <p className="muted small">No conversions yet.</p>}
                <ul className="jobs">
                  {jobs.map((j) => (
                    <li key={j.job_id} className={j.job_id === jobId ? "active" : ""} onClick={() => open(j.job_id)}>
                      <img src={fileUrl(j.thumb)} alt="" />
                      <span><b>{j.name}</b><br /><span className="muted">{j.width}×{j.height} · {j.layers} layers</span></span>
                    </li>
                  ))}
                </ul>
              </section>
            </>
          )}
        </div>
      </aside>

      <main className="canvas-area">
        {error && <div className="toast error" onClick={() => setError("")}>{error}</div>}
        {doc ? (
          <Workspace state={state} dispatch={dispatch} tool={tool} setTool={setTool} cam={cam} setCam={setCam}
            fitSignal={fitSignal} view={view} editingId={editingId} setEditingId={setEditingId} onDropFile={convert}
            refit={refit} />
        ) : jobId ? (
          <div className="center-card">
            <h2>{job?.name ?? "…"}</h2>
            <p className="muted">{job?.status === "error" ? "Failed" : busy ? "Converting…" : "Loading…"}</p>
            <pre>{(job?.log ?? []).join("\n") || "Waiting…"}{job?.error ? `\n\nError: ${job.error}` : ""}</pre>
          </div>
        ) : (
          <EmptyCanvas onFile={convert} />
        )}
      </main>

      <aside className="right">
        {doc ? <DesignPanel state={state} dispatch={dispatch} onRefit={(widths) => setRefit({ widths, nonce: Date.now() })} /> : <p className="muted small pad">Open or convert an image to start.</p>}
      </aside>
    </div>
  );
}

function EmptyCanvas({ onFile }: { onFile: (f: File) => void }) {
  const [over, setOver] = useState(false);
  return (
    <div className={`empty-canvas${over ? " over" : ""}`}
      onDragOver={(e) => { e.preventDefault(); setOver(true); }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => {
        e.preventDefault(); setOver(false);
        const f = [...e.dataTransfer.files].find((x) => x.type.startsWith("image/"));
        if (f) onFile(f);
      }}>
      <div>
        <Icon.upload />
        <h2>Drop an image or screenshot here</h2>
        <p className="muted">Or paste with ⌘V, or pick one under <b>Files</b>. Text becomes editable layers, buttons become
          auto-layout frames, the background is cleaned up.</p>
      </div>
    </div>
  );
}
