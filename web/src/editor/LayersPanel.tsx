// Left panel: the layer list, top layer first, like Figma.
import { useState, type Dispatch, type MouseEvent } from "react";
import { Icon } from "./icons";
import type { Layer } from "../types";
import type { Action, EditorState } from "./store";

const ICON: Record<Layer["type"], () => JSX.Element> = { image: Icon.image, text: Icon.text, button: Icon.button, pill: Icon.button, rect: Icon.rect };

export default function LayersPanel({ state, dispatch, onEdit }: {
  state: EditorState; dispatch: Dispatch<Action>; onEdit: (id: string) => void;
}) {
  const doc = state.doc!;
  const [renaming, setRenaming] = useState<string | null>(null);
  const [dragId, setDragId] = useState<string | null>(null);
  const [dropAt, setDropAt] = useState<{ id: string; above: boolean } | null>(null);
  const rows = [...doc.layers].reverse();

  const click = (e: MouseEvent, id: string) => {
    const sel = state.selection;
    if (e.shiftKey && sel.length) { // range, in list order
      const ids = rows.map((l) => l.id);
      const a = ids.indexOf(sel[sel.length - 1]), b = ids.indexOf(id);
      const range = ids.slice(Math.min(a, b), Math.max(a, b) + 1);
      dispatch({ type: "select", ids: [...new Set([...sel, ...range])] });
    } else if (e.metaKey || e.ctrlKey) {
      dispatch({ type: "select", ids: sel.includes(id) ? sel.filter((s) => s !== id) : [...sel, id] });
    } else {
      dispatch({ type: "select", ids: [id] });
    }
  };

  const drop = () => {
    if (!dragId || !dropAt || dragId === dropAt.id) return;
    const moving = doc.layers.find((l) => l.id === dragId)!;
    const rest = doc.layers.filter((l) => l.id !== dragId);
    const i = rest.findIndex((l) => l.id === dropAt.id);
    rest.splice(dropAt.above ? i + 1 : i, 0, moving); // the list is reversed: "above" = later in the array
    dispatch({ type: "commit", layers: rest });
  };

  return (
    <ul className="layers" onDragEnd={() => { setDragId(null); setDropAt(null); }}>
      {rows.map((l) => {
        const sel = state.selection.includes(l.id);
        const marker = dropAt?.id === l.id ? (dropAt.above ? " drop-above" : " drop-below") : "";
        return (
          <li
            key={l.id}
            className={`${sel ? "active" : ""}${l.visible ? "" : " hidden"}${marker}`}
            draggable={renaming !== l.id}
            onDragStart={(e) => { setDragId(l.id); e.dataTransfer.effectAllowed = "move"; }}
            onDragOver={(e) => {
              e.preventDefault();
              const b = e.currentTarget.getBoundingClientRect();
              setDropAt({ id: l.id, above: e.clientY < b.top + b.height / 2 });
            }}
            onDrop={(e) => { e.preventDefault(); drop(); setDragId(null); setDropAt(null); }}
            onClick={(e) => click(e, l.id)}
            onDoubleClick={() => (l.type === "image" ? setRenaming(l.id) : onEdit(l.id))}
          >
            <span className="icon">{ICON[l.type]()}</span>
            {renaming === l.id ? (
              <input
                className="rename"
                autoFocus
                defaultValue={l.name}
                onClick={(e) => e.stopPropagation()}
                onBlur={(e) => {
                  setRenaming(null);
                  const name = e.target.value.trim();
                  if (name && name !== l.name) dispatch({ type: "commit", patch: { [l.id]: { name } } });
                }}
                onKeyDown={(e) => {
                  if (e.key === "Enter") (e.target as HTMLInputElement).blur();
                  if (e.key === "Escape") setRenaming(null);
                  e.stopPropagation();
                }}
              />
            ) : (
              <span className="lname" title={l.text || l.name}
                onDoubleClick={(e) => { e.stopPropagation(); setRenaming(l.id); }}>
                {l.name}
              </span>
            )}
            <button className={`row-btn${l.locked ? " on" : ""}`} title={l.locked ? "Unlock" : "Lock"}
              onClick={(e) => { e.stopPropagation(); dispatch({ type: "commit", patch: { [l.id]: { locked: !l.locked } } }); }}>
              {l.locked ? <Icon.lock /> : <Icon.unlock />}
            </button>
            <button className={`row-btn${l.visible ? "" : " on"}`} title={l.visible ? "Hide" : "Show"}
              onClick={(e) => { e.stopPropagation(); dispatch({ type: "commit", patch: { [l.id]: { visible: !l.visible } } }); }}>
              {l.visible ? <Icon.eye /> : <Icon.eyeOff />}
            </button>
          </li>
        );
      })}
    </ul>
  );
}
