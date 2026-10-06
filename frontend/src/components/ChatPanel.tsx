import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, MessageSquarePlus, Send, Sparkles, ThumbsDown, ThumbsUp, Trash2, X } from "lucide-react";
import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api, streamChat, type AppInfo, type ChatMessage, type ConversationSummary } from "../api";

export function ChatPanel({
  appId,
  className = "",
  compact = false,
  newChatSignal = 0,
  showModel = false,
}: {
  appId: string;
  className?: string;
  // Under each answer, which model automatic model choice picked (for app admins testing).
  showModel?: boolean;
  // The widget: no conversation list; "new chat" comes from its header.
  compact?: boolean;
  newChatSignal?: number;
}) {
  const queryClient = useQueryClient();
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  const { data: info } = useQuery({
    queryKey: ["chat-info", appId],
    queryFn: () => api<AppInfo>(`/api/chat/${appId}`),
  });
  const { data: conversations } = useQuery({
    queryKey: ["conversations", appId],
    queryFn: () => api<ConversationSummary[]>(`/api/chat/${appId}/conversations`),
  });

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, status]);

  useEffect(() => () => abortRef.current?.abort(), []);

  useEffect(() => {
    if (newChatSignal) newConversation();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [newChatSignal]);

  async function openConversation(id: string) {
    if (busy) return;
    setConversationId(id);
    setMessages(await api<ChatMessage[]>(`/api/chat/${appId}/conversations/${id}`));
  }

  function newConversation() {
    if (busy) return;
    setConversationId(null);
    setMessages([]);
  }

  async function removeConversation(id: string) {
    if (!confirm("Delete this conversation?")) return;
    await api(`/api/chat/${appId}/conversations/${id}`, { method: "DELETE" });
    if (id === conversationId) newConversation();
    queryClient.invalidateQueries({ queryKey: ["conversations", appId] });
  }

  function updateLast(update: (msg: ChatMessage) => ChatMessage) {
    setMessages((prev) => [...prev.slice(0, -1), update(prev[prev.length - 1])]);
  }

  function updateMessage(id: ChatMessage["id"], update: (msg: ChatMessage) => ChatMessage) {
    setMessages((prev) => prev.map((m) => (m.id === id ? update(m) : m)));
  }

  /** Answers the question behind `message` again, one step up the model ladder, in its place. */
  async function improve(message: ChatMessage) {
    if (busy || typeof message.id !== "number") return;
    const oldId = message.id;
    const placeholder = `b-${Date.now()}`;
    setBusy(true);
    setStatus("Getting a better answer...");
    updateMessage(oldId, (m) => ({ ...m, id: placeholder, content: "", can_improve: false, notice: undefined }));
    const controller = new AbortController();
    abortRef.current = controller;
    let failed: string | null = null;
    let newId: number | null = null;
    try {
      await streamChat(
        appId,
        { better_than: oldId },
        (ev) => {
          switch (ev.type) {
            case "text":
              setStatus(null);
              updateMessage(placeholder, (m) => ({ ...m, content: m.content + ev.text }));
              break;
            case "reset":
              updateMessage(placeholder, (m) => ({ ...m, content: "" }));
              break;
            case "tool":
              setStatus(ev.label);
              break;
            case "route":
              updateMessage(placeholder, (m) => ({
                ...m,
                can_improve: ev.can_improve,
                model: `${ev.model}${ev.effort ? `, ${ev.effort} effort` : ""} (better answer)`,
              }));
              break;
            case "error":
              failed = ev.message;
              break;
            case "done":
              newId = ev.message_id;
              break;
          }
        },
        controller.signal,
      );
    } catch (err) {
      if (!controller.signal.aborted) failed = err instanceof Error ? err.message : "Request failed";
    } finally {
      if (failed || newId === null) {
        // The original answer stays.
        updateMessage(placeholder, () => ({ ...message, notice: failed ?? undefined, can_improve: false }));
      } else {
        const id = newId;
        updateMessage(placeholder, (m) => ({ ...m, id, replaced: oldId, rating: null }));
      }
      setBusy(false);
      setStatus(null);
    }
  }

  async function send(event?: FormEvent) {
    event?.preventDefault();
    const text = input.trim();
    if (!text || busy) return;
    setInput("");
    setBusy(true);
    setStatus("Thinking...");
    setMessages((prev) => [
      ...prev,
      { id: `u-${Date.now()}`, role: "user", content: text },
      { id: `a-${Date.now()}`, role: "assistant", content: "" },
    ]);
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      await streamChat(
        appId,
        { message: text, conversation_id: conversationId },
        (ev) => {
          switch (ev.type) {
            case "conversation":
              setConversationId(ev.id);
              break;
            case "text":
              setStatus(null);
              updateLast((m) => ({ ...m, content: m.content + ev.text }));
              break;
            case "reset":
              updateLast((m) => ({ ...m, content: "" }));
              break;
            case "tool":
              setStatus(ev.label);
              break;
            case "route":
              updateLast((m) => ({
                ...m,
                can_improve: ev.can_improve,
                model: `${ev.model}${ev.effort ? `, ${ev.effort} effort` : ""}${ev.escalated ? " (moved up)" : ""}`,
              }));
              break;
            case "blocked":
              updateLast((m) => ({ ...m, content: ev.message, blocked: true }));
              break;
            case "error":
              updateLast((m) => ({ ...m, content: m.content || ev.message, error: !m.content }));
              break;
            case "done":
              // The saved message's id, so the answer can be rated.
              updateLast((m) => ({ ...m, id: ev.message_id }));
              break;
          }
        },
        controller.signal,
      );
    } catch (err) {
      if (!controller.signal.aborted) {
        updateLast((m) => ({ ...m, content: err instanceof Error ? err.message : "Request failed", error: true }));
      }
    } finally {
      setBusy(false);
      setStatus(null);
      queryClient.invalidateQueries({ queryKey: ["conversations", appId] });
    }
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      send();
    }
  }

  return (
    <div className={`flex min-h-0 overflow-hidden bg-white ${compact ? "" : "rounded-lg border border-slate-200"} ${className}`}>
      <aside className={`${compact ? "hidden" : "hidden md:flex"} w-56 shrink-0 flex-col border-r border-slate-200 bg-slate-50`}>
        <button
          onClick={newConversation}
          className="m-3 flex items-center justify-center gap-2 rounded-md border border-slate-300 bg-white px-3 py-2 text-sm hover:bg-slate-100"
        >
          <MessageSquarePlus size={16} /> New chat
        </button>
        <ul className="flex-1 space-y-0.5 overflow-y-auto px-2 pb-2">
          {conversations?.map((c) => (
            <li key={c.id} className="group flex items-center">
              <button
                onClick={() => openConversation(c.id)}
                className={`flex-1 truncate rounded-md px-2 py-1.5 text-left text-sm ${
                  c.id === conversationId ? "bg-slate-200 font-medium" : "hover:bg-slate-100"
                }`}
                title={c.title}
              >
                {c.title || "Untitled"}
              </button>
              <button
                onClick={() => removeConversation(c.id)}
                className="invisible p-1 text-slate-400 hover:text-red-600 group-hover:visible"
                aria-label="Delete conversation"
              >
                <Trash2 size={14} />
              </button>
            </li>
          ))}
        </ul>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <div className="flex-1 space-y-4 overflow-y-auto p-5">
          {messages.length === 0 && (
            <div className="mx-auto mt-10 max-w-md text-center">
              <h2 className="text-lg font-semibold">{info?.name}</h2>
              <p className="mt-2 text-sm text-slate-500">
                {info?.welcome_message || info?.description || "Ask a question to get started."}
              </p>
            </div>
          )}
          {messages.map((m, i) => (
            <MessageBubble
              key={m.id}
              message={m}
              appId={appId}
              showModel={showModel}
              onImprove={() => improve(m)}
              // Rating is offered once an answer has finished streaming.
              rateable={!(busy && i === messages.length - 1)}
              onRated={(rated) => setMessages((prev) => prev.map((x) => (x.id === rated.id ? { ...x, ...rated } : x)))}
            />
          ))}
          {status && (
            <div className="flex items-center gap-2 text-sm text-slate-500">
              <Loader2 size={14} className="animate-spin" /> {status}
            </div>
          )}
          <div ref={bottomRef} />
        </div>
        <form onSubmit={send} className="border-t border-slate-200 p-3">
          <div className="flex items-end gap-2">
            <textarea
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={onKeyDown}
              rows={1}
              maxLength={8000}
              placeholder="Type a message..."
              className="max-h-40 min-h-10 flex-1 resize-none rounded-md border border-slate-300 px-3 py-2 text-sm outline-none focus:border-slate-500 focus:ring-2 focus:ring-slate-200"
            />
            <button
              type="submit"
              disabled={busy || !input.trim()}
              className="flex h-10 w-10 items-center justify-center rounded-md bg-slate-900 text-white disabled:opacity-40"
              aria-label="Send"
            >
              {busy ? <Loader2 size={16} className="animate-spin" /> : <Send size={16} />}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

function MessageBubble({
  message,
  appId,
  showModel,
  onImprove,
  rateable,
  onRated,
}: {
  message: ChatMessage;
  appId: string;
  showModel: boolean;
  onImprove: () => void;
  rateable: boolean;
  onRated: (message: ChatMessage) => void;
}) {
  if (message.role === "user") {
    return (
      <div className="flex justify-end">
        <div className="max-w-[80%] whitespace-pre-wrap rounded-2xl rounded-br-sm bg-slate-900 px-4 py-2 text-sm text-white">
          {message.content}
        </div>
      </div>
    );
  }
  const tone = message.error
    ? "border-red-200 bg-red-50 text-red-800"
    : message.blocked
      ? "border-amber-200 bg-amber-50 text-amber-900"
      : "border-slate-200 bg-slate-50";
  if (!message.content) return null;
  return (
    <div className="flex">
      <div className="max-w-[85%]">
        <div className={`prose-chat rounded-2xl rounded-bl-sm border px-4 py-2 text-sm ${tone}`}>
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{message.content}</ReactMarkdown>
        </div>
        {showModel && message.model && <p className="mt-1 text-xs text-slate-400">Answered by {message.model}</p>}
        {message.notice && <p className="mt-1 px-1 text-xs text-slate-500">{message.notice}</p>}
        {rateable && typeof message.id === "number" && message.replaced !== undefined && (
          <ReasonChips appId={appId} answerId={message.replaced} />
        )}
        {rateable && typeof message.id === "number" && !message.error && (
          <div className="flex flex-wrap items-center gap-x-3">
            <FeedbackBar message={message} appId={appId} onRated={onRated} />
            {message.can_improve && !message.blocked && (
              <button
                onClick={onImprove}
                className="mt-1 flex items-center gap-1 rounded px-1 py-0.5 text-xs font-medium text-slate-600 hover:bg-slate-100 hover:text-slate-900"
              >
                <Sparkles size={13} /> Get a better answer
              </button>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

const REASONS = ["Wrong", "Incomplete", "Didn't answer my question"];

/** After a better answer: one optional tap saying what was wrong with the one it replaced. */
function ReasonChips({ appId, answerId }: { appId: string; answerId: number }) {
  const [state, setState] = useState<"asking" | "thanks" | "closed">("asking");

  async function choose(reason: string) {
    setState("thanks");
    try {
      await api(`/api/chat/${appId}/messages/${answerId}/feedback`, {
        method: "POST",
        body: JSON.stringify({ rating: -1, feedback: reason }),
      });
    } catch {
      // Optional feedback; the better answer is what matters.
    }
  }

  if (state === "closed") return null;
  if (state === "thanks") return <p className="mt-1 px-1 text-xs text-slate-500">Thanks, that helps us improve.</p>;
  return (
    <div className="mt-1.5 flex flex-wrap items-center gap-1.5 px-1 text-xs text-slate-500">
      <span>What was wrong with the first answer?</span>
      {REASONS.map((reason) => (
        <button
          key={reason}
          onClick={() => choose(reason)}
          className="rounded-full border border-slate-300 bg-white px-2.5 py-0.5 text-slate-700 hover:border-slate-500"
        >
          {reason}
        </button>
      ))}
      <button onClick={() => setState("closed")} className="rounded p-0.5 hover:bg-slate-100" aria-label="Skip">
        <X size={13} />
      </button>
    </div>
  );
}

/** "Was this helpful?" with thumbs up/down, and a comment box after thumbs down. */
function FeedbackBar({
  message,
  appId,
  onRated,
}: {
  message: ChatMessage;
  appId: string;
  onRated: (message: ChatMessage) => void;
}) {
  const [asking, setAsking] = useState(false);
  const [comment, setComment] = useState("");
  const [saving, setSaving] = useState(false);

  async function rate(rating: 1 | -1, feedback?: string) {
    setSaving(true);
    try {
      const saved = await api<ChatMessage>(`/api/chat/${appId}/messages/${message.id}/feedback`, {
        method: "POST",
        body: JSON.stringify({ rating, feedback }),
      });
      onRated({ ...message, rating: saved.rating, feedback: saved.feedback });
      setAsking(false);
    } finally {
      setSaving(false);
    }
  }

  if (message.rating === 1) {
    return <p className="mt-1 px-1 text-xs text-slate-500">Thanks, glad it helped.</p>;
  }
  if (message.rating === -1 && !asking) {
    return (
      <p className="mt-1 px-1 text-xs text-slate-500">
        Thanks for the feedback{message.feedback ? "" : "."}
        {!message.feedback && (
          <button onClick={() => setAsking(true)} className="ml-1 underline">
            Tell us what was wrong
          </button>
        )}
      </p>
    );
  }
  if (asking) {
    return (
      <form
        className="mt-2 space-y-2"
        onSubmit={(e) => {
          e.preventDefault();
          rate(-1, comment);
        }}
      >
        <textarea
          autoFocus
          rows={2}
          maxLength={4000}
          value={comment}
          onChange={(e) => setComment(e.target.value)}
          placeholder="What was wrong or missing? (optional)"
          className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm outline-none focus:border-slate-500"
        />
        <div className="flex gap-2">
          <button type="submit" disabled={saving} className="rounded-md bg-slate-900 px-3 py-1 text-xs font-medium text-white">
            Send feedback
          </button>
          <button type="button" onClick={() => rate(-1)} disabled={saving} className="px-2 text-xs text-slate-500 hover:underline">
            Skip
          </button>
        </div>
      </form>
    );
  }
  return (
    <div className="mt-1 flex items-center gap-1 px-1 text-xs text-slate-500">
      <span>Was this helpful?</span>
      <button onClick={() => rate(1)} disabled={saving} className="rounded p-1 hover:bg-slate-100 hover:text-emerald-700" aria-label="Helpful">
        <ThumbsUp size={14} />
      </button>
      <button onClick={() => setAsking(true)} disabled={saving} className="rounded p-1 hover:bg-slate-100 hover:text-red-700" aria-label="Not helpful">
        <ThumbsDown size={14} />
      </button>
    </div>
  );
}
