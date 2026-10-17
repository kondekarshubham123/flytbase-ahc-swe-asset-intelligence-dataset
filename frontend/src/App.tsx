import React, { useEffect, useRef, useState } from "react";
import { api } from "./api";
import { Markdown } from "./markdown";
import { VisualView } from "./components/Visual";
import { ConflictCard, EvidenceList } from "./components/Feedback";
import type { Bootstrap, ChatMessage, Reply, SessionInfo, User } from "./types";

const SUGGESTIONS: string[] = [
  "Is WELL-0001 producing oil right now? Show me its status card.",
  "Which wells in Northeast Block 8 are not producing oil right now, and why?",
  "How many oil spills has WELL-0001 had in the last 4 years, and how much oil in total?",
  "Show a heatmap of 2026 spills by geographic area and month.",
  "Which rules has WELL-0001 broken in the last 12 months, and what is still open?",
];

function uid() {
  return Math.random().toString(36).slice(2);
}

export default function App() {
  const [boot, setBoot] = useState<Bootstrap | null>(null);
  const [user, setUser] = useState<User | null>(null);
  const [sessions, setSessions] = useState<SessionInfo[]>([]);
  const [sessionId, setSessionId] = useState<string>("");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    api.bootstrap().then((b) => {
      setBoot(b);
      const first = b.users.find((u) => u.user_id === "U-201") || b.users[0];
      if (first) selectUser(first, b);
    }).catch((e) => setError(String(e)));
  }, []);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  async function selectUser(u: User, b?: Bootstrap) {
    setUser(u);
    setMessages([]);
    try {
      const s = await api.sessions(u.user_id);
      setSessions(s.sessions);
      if (s.sessions.length > 0) {
        setSessionId(s.sessions[0].session_id);
        const m = await api.messages(s.sessions[0].session_id);
        setMessages(
          m.messages.map((x) => ({
            id: x.message_id,
            role: x.role === "user" ? "user" : "assistant",
            text: x.content,
          }))
        );
      } else {
        await startNewSession(u.user_id);
      }
    } catch (e) {
      setError(String(e));
    }
  }

  async function loadSession(s: SessionInfo) {
    setSessionId(s.session_id);
    try {
      const m = await api.messages(s.session_id);
      setMessages(
        m.messages.map((x) => ({
          id: x.message_id,
          role: x.role === "user" ? "user" : "assistant",
          text: x.content,
        }))
      );
    } catch (e) {
      setError(String(e));
    }
  }

  async function startNewSession(userId?: string) {
    const uidv = userId || user?.user_id;
    if (!uidv) return;
    const s = await api.newSession(uidv, "New chat");
    setSessionId(s.session_id);
    setMessages([]);
    setSessions((prev) => [
      { session_id: s.session_id, title: "New chat", created_at: new Date().toISOString() },
      ...prev,
    ]);
  }

  async function send(text?: string) {
    const msg = (text ?? input).trim();
    if (!msg || !user || busy) return;
    setInput("");
    setError(null);
    const userMsg: ChatMessage = { id: uid(), role: "user", text: msg };
    const pending: ChatMessage = { id: uid(), role: "assistant", text: "", pending: true };
    setMessages((m) => [...m, userMsg, pending]);
    setBusy(true);
    try {
      const reply: Reply = await api.chat(user.user_id, sessionId, msg);
      setMessages((m) =>
        m.map((x) => (x.id === pending.id ? { ...x, pending: false, text: reply.text, reply } : x))
      );
    } catch (e) {
      setMessages((m) =>
        m.map((x) => (x.id === pending.id ? { ...x, pending: false, text: `Error: ${e}` } : x))
      );
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand">
          <div className="logo">◆</div>
          <div>
            <div className="brand-title">Asset Intelligence</div>
            <div className="brand-sub">Copilot · FlytBase</div>
          </div>
        </div>

        <label className="field">
          <span>Signed in as</span>
          <select
            value={user?.user_id || ""}
            onChange={(e) => {
              const u = boot?.users.find((x) => x.user_id === e.target.value);
              if (u) selectUser(u);
            }}
          >
            {boot?.users.map((u) => (
              <option key={u.user_id} value={u.user_id}>
                {u.name} · {u.role.replace(/_/g, " ")}
              </option>
            ))}
          </select>
        </label>

        <button className="btn-new" onClick={() => startNewSession()}>
          + New chat
        </button>

        <div className="sessions">
          <div className="side-label">Recent chats</div>
          {sessions.slice(0, 8).map((s) => (
            <button
              key={s.session_id}
              className={`session ${s.session_id === sessionId ? "active" : ""}`}
              onClick={() => loadSession(s)}
            >
              {s.title || "New chat"}
            </button>
          ))}
        </div>

        <div className="suggest">
          <div className="side-label">Try asking</div>
          {SUGGESTIONS.map((s) => (
            <button key={s} className="sugg" onClick={() => send(s)}>
              {s}
            </button>
          ))}
        </div>

        <div className="foot">
          <div>Data as of {boot?.today}</div>
          <div className="muted">
            {boot?.llm_enabled ? `LLM: ${boot.model}` : "Deterministic mode (no LLM)"}
          </div>
          {boot?.stats && (
            <div className="muted">
              {boot.stats.wells} wells · {boot.stats.inspections.toLocaleString()} inspections ·{" "}
              {boot.stats.valid_spills} valid spills
            </div>
          )}
        </div>
      </aside>

      <main className="chat">
        <header className="chat-head">
          <div>
            <div className="chat-title">Ask about any well</div>
            <div className="chat-sub">
              Every answer is computed from the records and cites them.
            </div>
          </div>
          {user && <div className="persona">{user.name}</div>}
        </header>

        <div className="messages">
          {messages.length === 0 && (
            <div className="welcome">
              <h2>Good day{user ? `, ${user.name.split(" ")[0]}` : ""}.</h2>
              <p>
                Ask a plain-English question about wells, spills, downtime, compliance or cost.
                I compute the numbers from the data and show you the records I used.
              </p>
            </div>
          )}
          {messages.map((m) => (
            <div key={m.id} className={`msg ${m.role}`}>
              {m.role === "user" ? (
                <div className="bubble user-bubble">{m.text}</div>
              ) : m.pending ? (
                <div className="bubble asst-bubble thinking">Computing from the records…</div>
              ) : (
                <div className="bubble asst-bubble">
                  <Markdown text={m.text} />
                  {m.reply?.visual && (
                    <div className="visual">
                      <VisualView visual={m.reply.visual} data={m.reply.data} />
                    </div>
                  )}
                  {m.reply && <ConflictCard conflicts={m.reply.conflicts} />}
                  {m.reply && <EvidenceList items={m.reply.evidence} />}
                </div>
              )}
            </div>
          ))}
          <div ref={endRef} />
        </div>

        {error && <div className="error">{error}</div>}

        <div className="composer">
          <input
            value={input}
            placeholder="e.g. Which wells in Southwest Block 24 were down this summer?"
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                send();
              }
            }}
            disabled={busy}
          />
          <button onClick={() => send()} disabled={busy || !input.trim()}>
            {busy ? "…" : "Send"}
          </button>
        </div>
      </main>
    </div>
  );
}
