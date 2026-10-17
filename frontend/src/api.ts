import type { Bootstrap, Reply, SessionInfo } from "./types";

const BASE = "";

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`);
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json();
}

async function post<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json();
}

export const api = {
  health: () => get<{ status: string }>("/api/health"),
  bootstrap: (userId?: string) =>
    get<Bootstrap>(`/api/bootstrap${userId ? `?user_id=${encodeURIComponent(userId)}` : ""}`),
  chat: (user_id: string, session_id: string, message: string) =>
    post<Reply>("/api/chat", { user_id, session_id, message }),
  sessions: (userId: string) =>
    get<{ sessions: SessionInfo[] }>(`/api/sessions?user_id=${encodeURIComponent(userId)}`),
  newSession: (userId: string, title?: string) =>
    post<{ session_id: string }>(
      `/api/sessions?user_id=${encodeURIComponent(userId)}${title ? `&title=${encodeURIComponent(title)}` : ""}`,
      {}
    ),
  messages: (sessionId: string) =>
    get<{ messages: { message_id: string; role: string; content: string; meta?: string }[] }>(
      `/api/sessions/${encodeURIComponent(sessionId)}/messages`
    ),
};
