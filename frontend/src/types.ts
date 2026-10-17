export interface User {
  user_id: string;
  name: string;
  role: string;
  team_id: string;
}

export interface Area {
  area_id: string;
  name: string;
}

export interface SessionInfo {
  session_id: string;
  title: string | null;
  created_at: string;
}

export interface Evidence {
  kind: "record" | "document" | "note" | string;
  id: string;
  label: string;
  detail?: string;
  path?: string;
  snippet?: string;
}

export interface ConflictSide {
  source: string;
  value: string;
}

export interface Conflict {
  kind: string;
  asset_id?: string;
  message: string;
  left: ConflictSide;
  right: ConflictSide;
  ask?: string;
}

export interface Visual {
  type: "card" | "table" | "line" | "bar" | "map" | "heatmap";
  spec: any;
}

export interface Reply {
  text: string;
  visual: Visual | null;
  evidence: Evidence[];
  conflicts: Conflict[];
  intent: Record<string, any>;
  tool: string;
  notes: string[];
  focus: string[];
  data: Record<string, any>;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  text: string;
  reply?: Reply;
  pending?: boolean;
}

export interface Bootstrap {
  today: string;
  llm_enabled: boolean;
  model: string | null;
  stats: Record<string, number>;
  users: User[];
  areas: Area[];
  sessions: SessionInfo[];
}
