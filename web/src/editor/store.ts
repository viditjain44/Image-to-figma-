// Editor state: the open document, the selection, and undo/redo history.
//
// Every user change goes through `commit` (one undo step). Drags call `begin` once, then send
// `preview` patches while the pointer moves, then `end`, so a whole drag is a single undo step.
// `amend` folds a follow-up into the last undo step (e.g. re-fitting text after a font swap).
// `sync` is for values the browser measures (rendered text size): it never creates history and
// never marks the document as changed.
import type { Doc, Layer } from "../types";

export interface EditorState {
  doc: Doc | null;
  past: Doc[];
  future: Doc[];
  selection: string[];
  checkpoint: Doc | null; // document at the start of the current drag
  version: number;        // bumps on every user change (drives autosave)
  savedVersion: number;
}

export const initialState: EditorState = {
  doc: null, past: [], future: [], selection: [], checkpoint: null, version: 0, savedVersion: 0,
};

export type LayerPatch = Record<string, Partial<Layer>>;

export type Action =
  | { type: "load"; doc: Doc }
  | { type: "close" }
  | { type: "commit"; patch?: LayerPatch; layers?: Layer[] }
  | { type: "begin" }
  | { type: "preview"; patch?: LayerPatch; layers?: Layer[] }
  | { type: "end" }
  | { type: "sync"; patch: LayerPatch }
  | { type: "amend"; patch: LayerPatch }
  | { type: "select"; ids: string[] }
  | { type: "undo" }
  | { type: "redo" }
  | { type: "saved"; version: number };

const HISTORY = 200;

function apply(doc: Doc, patch: LayerPatch): Doc {
  return { ...doc, layers: doc.layers.map((l) => (patch[l.id] ? { ...l, ...patch[l.id] } : l)) };
}

function existing(ids: string[], doc: Doc | null): string[] {
  if (!doc) return [];
  const all = new Set(doc.layers.map((l) => l.id));
  return ids.filter((id) => all.has(id));
}

export function reducer(s: EditorState, a: Action): EditorState {
  switch (a.type) {
    case "load":
      return { ...initialState, doc: a.doc };
    case "close":
      return initialState;
    case "commit": {
      if (!s.doc) return s;
      const doc = a.layers ? { ...s.doc, layers: a.layers } : apply(s.doc, a.patch ?? {});
      return { ...s, doc, past: [...s.past, s.doc].slice(-HISTORY), future: [], version: s.version + 1,
        selection: existing(s.selection, doc) };
    }
    case "begin":
      return s.doc ? { ...s, checkpoint: s.doc } : s;
    case "preview":
      if (!s.doc) return s;
      return { ...s, doc: a.layers ? { ...s.doc, layers: a.layers } : apply(s.doc, a.patch ?? {}) };
    case "end": {
      if (!s.doc || !s.checkpoint) return s;
      if (s.checkpoint === s.doc) return { ...s, checkpoint: null }; // nothing moved: no undo step
      return { ...s, past: [...s.past, s.checkpoint].slice(-HISTORY), future: [], checkpoint: null,
        version: s.version + 1 };
    }
    case "sync":
      return s.doc ? { ...s, doc: apply(s.doc, a.patch) } : s;
    case "amend": // part of the last change (same undo step), but saved
      return s.doc ? { ...s, doc: apply(s.doc, a.patch), version: s.version + 1 } : s;
    case "select":
      return { ...s, selection: existing(a.ids, s.doc) };
    case "undo": {
      if (!s.doc || !s.past.length) return s;
      const doc = s.past[s.past.length - 1];
      return { ...s, doc, past: s.past.slice(0, -1), future: [s.doc, ...s.future], version: s.version + 1,
        selection: existing(s.selection, doc) };
    }
    case "redo": {
      if (!s.doc || !s.future.length) return s;
      const doc = s.future[0];
      return { ...s, doc, past: [...s.past, s.doc], future: s.future.slice(1), version: s.version + 1,
        selection: existing(s.selection, doc) };
    }
    case "saved":
      return { ...s, savedVersion: a.version };
  }
}

/** Layers the user can click on the canvas (the full-size background and locked layers are picked in the panel). */
export const canvasPickable = (l: Layer) => l.visible && !l.locked && l.role !== "background";

export const newId = (prefix: string) => `${prefix}-${Date.now().toString(36)}${Math.random().toString(36).slice(2, 5)}`;
