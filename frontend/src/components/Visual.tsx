import React from "react";
import type { Visual } from "../types";

const COLUMNS: { key: string; label: string }[] = [
  { key: "asset_id", label: "Well" },
  { key: "well_name", label: "Name" },
  { key: "area_name", label: "Area" },
  { key: "well_type", label: "Type" },
  { key: "inspected_at", label: "Date" },
  { key: "inspection_id", label: "Inspection" },
  { key: "wellhead_temp_c", label: "Wellhead °C" },
  { key: "ambient_temp_c", label: "Air °C" },
  { key: "delta_c", label: "Δ °C" },
  { key: "threshold_c", label: "Limit °C" },
  { key: "producing", label: "Producing" },
  { key: "downtime_days", label: "Days down" },
  { key: "volume_bbl", label: "Spill bbl" },
  { key: "spill_class", label: "Class" },
  { key: "reportable", label: "Reportable" },
  { key: "containment_hours", label: "Contained h" },
  { key: "limit_hours", label: "Limit h" },
  { key: "records", label: "Records" },
  { key: "report_file", label: "Report" },
  { key: "protocol", label: "Protocol" },
  { key: "detail", label: "Detail" },
  { key: "status", label: "Status" },
  { key: "cost_usd", label: "Cost USD" },
  { key: "jobs", label: "Jobs" },
  { key: "repairs", label: "Repairs" },
  { key: "threshold", label: "Threshold" },
  { key: "needs_rca", label: "Needs RCA" },
];

function fmt(v: any): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "boolean") return v ? "yes" : "no";
  if (typeof v === "number") return Number.isInteger(v) ? String(v) : v.toFixed(2);
  if (typeof v === "string" && v.includes("T") && v.length >= 10) return v.slice(0, 10);
  return String(v);
}

function DataTable({ rows }: { rows: any[] }) {
  if (!rows || rows.length === 0) return <div className="empty">No rows.</div>;
  const present = COLUMNS.filter((c) => rows.some((r) => r[c.key] !== undefined && r[c.key] !== null));
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>{present.map((c) => <th key={c.key}>{c.label}</th>)}</tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i}>
              {present.map((c) => (
                <td key={c.key} className={c.key === "producing" ? (r[c.key] ? "ok" : "bad") : ""}>
                  {fmt(r[c.key])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function StatusCard({ data }: { data: any }) {
  const well = data?.well || {};
  const latest = data?.latest || {};
  const producing = data?.producing;
  const reasons = data?.reasons || [];
  return (
    <div className="status-card">
      <div className="sc-head">
        <div>
          <div className="sc-id">{well.asset_id}</div>
          <div className="sc-sub">
            {well.name} · {well.well_type} · {well.area_id}
          </div>
        </div>
        <div className={`badge ${producing ? "badge-ok" : "badge-bad"}`}>
          {producing ? "PRODUCING" : "NOT PRODUCING"}
        </div>
      </div>
      <div className="sc-grid">
        <div>
          <span className="k">Wellhead</span>
          <span className="v">{fmt(latest.wellhead_temp_c)} °C</span>
        </div>
        <div>
          <span className="k">Air</span>
          <span className="v">{fmt(latest.ambient_temp_c)} °C</span>
        </div>
        <div>
          <span className="k">Δ vs limit</span>
          <span className="v">{fmt(data?.delta_c)} / {fmt(latest.threshold_c)} °C</span>
        </div>
        <div>
          <span className="k">Last inspection</span>
          <span className="v">{latest.inspection_id} · {fmt(latest.inspected_at)}</span>
        </div>
        <div>
          <span className="k">Recorded status</span>
          <span className={`v ${data?.recorded_status_stale ? "stale" : ""}`}>
            {well.recorded_status}{data?.recorded_status_stale ? " (out of date)" : ""}
          </span>
        </div>
      </div>
      {!producing && (
        <div className="sc-reason">
          <span className="k">Reason</span>
          {reasons.length ? (
            <ul>{reasons.map((r: any, i: number) => <li key={i}>{r.text} <em>({r.id})</em></li>)}</ul>
          ) : (
            <div>
              No recorded reason.
              {data?.inspector_note ? <em> Inspector noted: “{data.inspector_note}”</em> : null}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function LineChart({ rows }: { rows: any[] }) {
  if (!rows || rows.length === 0) return <div className="empty">No readings.</div>;
  const W = 640, H = 240, P = 34;
  const series = [
    { key: "wellhead_temp_c", color: "#ff7a59", label: "Wellhead °C" },
    { key: "ambient_temp_c", color: "#4ea8de", label: "Air °C" },
  ];
  const vals = rows.flatMap((r) => series.map((s) => Number(r[s.key]))).filter((v) => !isNaN(v));
  const min = Math.min(...vals), max = Math.max(...vals);
  const y = (v: number) => H - P - ((v - min) / (max - min || 1)) * (H - 2 * P);
  const x = (i: number) => P + (i / Math.max(rows.length - 1, 1)) * (W - 2 * P);
  return (
    <div className="chart">
      <svg viewBox={`0 0 ${W} ${H}`} className="svg-chart">
        {[0, 0.25, 0.5, 0.75, 1].map((t, i) => {
          const v = min + t * (max - min);
          return (
            <g key={i}>
              <line x1={P} x2={W - P} y1={y(v)} y2={y(v)} stroke="#2a3345" strokeWidth={1} />
              <text x={4} y={y(v) + 4} fill="#8794a8" fontSize={10}>{v.toFixed(1)}</text>
            </g>
          );
        })}
        {series.map((s) => (
          <polyline
            key={s.key}
            fill="none"
            stroke={s.color}
            strokeWidth={2}
            points={rows.map((r, i) => `${x(i)},${y(Number(r[s.key]))}`).join(" ")}
          />
        ))}
        {rows.map((r, i) =>
          !r.producing ? (
            <circle key={i} cx={x(i)} cy={y(Number(r.wellhead_temp_c))} r={4} fill="#ef4444">
              <title>{`${fmt(r.inspected_at)}: NOT producing (Δ ${r.delta_c} °C)`}</title>
            </circle>
          ) : null
        )}
      </svg>
      <div className="legend">
        {series.map((s) => (
          <span key={s.key}><i style={{ background: s.color }} />{s.label}</span>
        ))}
        <span><i style={{ background: "#ef4444", borderRadius: "50%" }} />not producing</span>
      </div>
    </div>
  );
}

function BarChart({ rows }: { rows: any[] }) {
  if (!rows || rows.length === 0) return <div className="empty">No data.</div>;
  const max = Math.max(...rows.map((r) => Number(r.downtime_days) || 0), 1);
  return (
    <div className="bars">
      {rows.map((r, i) => (
        <div className="bar-row" key={i}>
          <span className="bar-label">{r.asset_id}</span>
          <div className="bar-track">
            <div className="bar-fill" style={{ width: `${((r.downtime_days || 0) / max) * 100}%` }} />
          </div>
          <span className="bar-val">{r.downtime_days} d</span>
        </div>
      ))}
    </div>
  );
}

function MapView({ points }: { points: any[] }) {
  if (!points || points.length === 0) return <div className="empty">No points.</div>;
  const W = 640, H = 360, P = 40;
  const lats = points.map((p) => p.lat), lons = points.map((p) => p.lon);
  const minLat = Math.min(...lats), maxLat = Math.max(...lats);
  const minLon = Math.min(...lons), maxLon = Math.max(...lons);
  const x = (v: number) => P + ((v - minLon) / (maxLon - minLon || 1)) * (W - 2 * P);
  const y = (v: number) => H - P - ((v - minLat) / (maxLat - minLat || 1)) * (H - 2 * P);
  const maxBbl = Math.max(...points.map((p) => p.bbl || 0), 1);
  return (
    <div className="chart">
      <svg viewBox={`0 0 ${W} ${H}`} className="svg-chart map">
        <rect x={P} y={P} width={W - 2 * P} height={H - 2 * P} fill="#0f1723" stroke="#2a3345" />
        {points.map((p, i) => (
          <g key={i}>
            <circle cx={x(p.lon)} cy={y(p.lat)} r={6 + (p.bbl / maxBbl) * 8} fill="#ff7a59" fillOpacity={0.55} stroke="#ff7a59">
              <title>{`${p.asset_id}: ${p.bbl} bbl on ${p.date}`}</title>
            </circle>
            <text x={x(p.lon)} y={y(p.lat) - 12} fill="#dbe4f0" fontSize={10} textAnchor="middle">{p.asset_id}</text>
          </g>
        ))}
      </svg>
      <div className="legend"><span>Pin size = spill volume (bbl)</span></div>
    </div>
  );
}

function Heatmap({ cells, totals }: { cells: any[]; totals: any[] }) {
  if (!cells || cells.length === 0) return <div className="empty">No spills.</div>;
  const areas = Array.from(new Set(cells.map((c) => c.area_name)));
  const max = Math.max(...cells.map((c) => c.spills), 1);
  const get = (a: string, m: number) => cells.find((c) => c.area_name === a && c.month === m)?.spills || 0;
  const color = (v: number) => (v === 0 ? "#141b28" : `rgba(255,122,89,${0.2 + 0.8 * (v / max)})`);
  return (
    <div className="heatmap">
      <div className="hm-row">
        <span className="hm-label" />
        {Array.from({ length: 12 }, (_, i) => (
          <span key={i} className="hm-month">{["J", "F", "M", "A", "M", "J", "J", "A", "S", "O", "N", "D"][i]}</span>
        ))}
        <span className="hm-month">Σ</span>
      </div>
      {areas.map((a) => (
        <div className="hm-row" key={a}>
          <span className="hm-label">{a}</span>
          {Array.from({ length: 12 }, (_, i) => {
            const v = get(a, i + 1);
            return (
              <span key={i} className="hm-cell" style={{ background: color(v) }} title={`${a} · month ${i + 1}: ${v}`}>
                {v || ""}
              </span>
            );
          })}
          <span className="hm-total">{totals.find((t) => t.area_name === a)?.total ?? ""}</span>
        </div>
      ))}
    </div>
  );
}

export function VisualView({ visual, data }: { visual: Visual; data: any }) {
  const spec = visual.spec || {};
  switch (visual.type) {
    case "card":
      return <StatusCard data={data} />;
    case "table":
      return <DataTable rows={data?.rows || spec.rows || []} />;
    case "line":
      return <LineChart rows={data?.rows || []} />;
    case "bar":
      return <BarChart rows={data?.rows || []} />;
    case "map":
      return <MapView points={spec.points || []} />;
    case "heatmap":
      return <Heatmap cells={data?.cells || []} totals={data?.area_totals || []} />;
    default:
      return null;
  }
}
