import React, { useState } from "react";
import type { Conflict, Evidence } from "../types";

const KIND_LABEL: Record<string, string> = {
  record: "record",
  document: "doc",
  note: "note",
};

export function EvidenceList({ items }: { items: Evidence[] }) {
  const [open, setOpen] = useState(false);
  if (!items || items.length === 0) return null;
  return (
    <div className="evidence">
      <button className="evidence-toggle" onClick={() => setOpen(!open)}>
        {open ? "▾" : "▸"} {items.length} source{items.length === 1 ? "" : "s"}
      </button>
      {open && (
        <ul className="evidence-list">
          {items.map((e, i) => (
            <li key={i}>
              <span className={`chip chip-${e.kind}`}>{KIND_LABEL[e.kind] || e.kind}</span>
              <span className="ev-label">{e.label}</span>
              {e.detail && <span className="ev-detail">{e.detail}</span>}
              {e.path && <span className="ev-path">{e.path}</span>}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function ConflictCard({ conflicts }: { conflicts: Conflict[] }) {
  if (!conflicts || conflicts.length === 0) return null;
  return (
    <div className="conflicts">
      {conflicts.map((c, i) => (
        <div className="conflict" key={i}>
          <div className="conflict-head">⚠️ Conflict needs a decision{c.asset_id ? ` · ${c.asset_id}` : ""}</div>
          <div className="conflict-msg">{c.message}</div>
          <div className="conflict-grid">
            <div className="side">
              <div className="side-src">{c.left.source}</div>
              <div className="side-val">{c.left.value}</div>
            </div>
            <div className="vs">vs</div>
            <div className="side">
              <div className="side-src">{c.right.source}</div>
              <div className="side-val">{c.right.value}</div>
            </div>
          </div>
          {c.ask && <div className="conflict-ask">{c.ask}</div>}
        </div>
      ))}
    </div>
  );
}
