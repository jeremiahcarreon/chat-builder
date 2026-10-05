import { MessageSquarePlus, X } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";
import { useParams } from "react-router";
import { ApiError, api, setWidgetAuth, type WidgetConfig } from "../api";
import { ChatPanel } from "../components/ChatPanel";
import { Button, Input } from "../components/ui";

type Stage = { kind: "loading" } | { kind: "login" } | { kind: "waiting" } | { kind: "ready"; name: string } | { kind: "error"; message: string };

// Partitioned per embedding site by the browser, and cleared when the tab closes.
const storageKey = (appId: string) => `cb-widget:${appId}`;

function originAllowed(origin: string, allowed: string[]): boolean {
  if (origin === window.location.origin) return true; // Chat Builder's own preview page
  return allowed.some((entry) => {
    if (!entry.includes("*.")) return entry === origin;
    const [scheme, host] = entry.split("://*.");
    const url = new URL(origin);
    return url.protocol === `${scheme}:` && url.host.endsWith(`.${host}`);
  });
}

/** The chat inside the widget's iframe. */
export function EmbedChat() {
  const { appId = "" } = useParams();
  const [config, setConfig] = useState<WidgetConfig | null>(null);
  const [stage, setStage] = useState<Stage>({ kind: "loading" });
  const [newChat, setNewChat] = useState(0);

  function close() {
    window.parent.postMessage({ type: "cb:close" }, "*");
  }

  function signedIn(token: string, name: string) {
    sessionStorage.setItem(storageKey(appId), JSON.stringify({ token, name }));
    setWidgetAuth({ token });
    setStage({ kind: "ready", name });
  }

  useEffect(() => {
    let cancelled = false;
    let waitTimer: number | undefined;

    async function start() {
      let cfg: WidgetConfig;
      try {
        cfg = await api<WidgetConfig>(`/api/embed/${appId}/config`);
      } catch {
        setStage({ kind: "error", message: "This chat isn't available." });
        return;
      }
      if (cancelled) return;
      setConfig(cfg);

      if (cfg.auth === "public") {
        let visitorId = localStorage.getItem(`${storageKey(appId)}:visitor`);
        if (!visitorId) {
          visitorId = (await api<{ visitor_id: string }>(`/api/embed/${appId}/visitor`, { method: "POST" })).visitor_id;
          localStorage.setItem(`${storageKey(appId)}:visitor`, visitorId);
        }
        setWidgetAuth({ visitorId });
        setStage({ kind: "ready", name: "" });
        return;
      }

      // A session from earlier in this tab, if it's still valid.
      const saved = sessionStorage.getItem(storageKey(appId));
      if (saved) {
        const { token, name } = JSON.parse(saved);
        setWidgetAuth({ token });
        try {
          await api(`/api/chat/${appId}`);
          if (!cancelled) setStage({ kind: "ready", name });
          if (cfg.auth === "login") return;
        } catch {
          sessionStorage.removeItem(storageKey(appId));
          setWidgetAuth(null);
        }
      }

      if (cfg.auth === "login") {
        setStage((s) => (s.kind === "ready" ? s : { kind: "login" }));
        return;
      }

      // Signed: the embedding page passes its token through postMessage.
      setStage((s) => (s.kind === "ready" ? s : { kind: "waiting" }));
      waitTimer = window.setTimeout(() => {
        setStage((s) =>
          s.kind === "waiting" ? { kind: "error", message: "This site didn't sign you in to the chat. Try reloading the page." } : s,
        );
      }, 8000);

      const onMessage = async (event: MessageEvent) => {
        if (event.source !== window.parent || !originAllowed(event.origin, cfg.allowed_origins)) return;
        const data = event.data || {};
        if (data.type !== "cb:identity") return;
        if (!data.token) {
          setStage({ kind: "error", message: "This site didn't sign you in to the chat." });
          return;
        }
        try {
          const session = await api<{ token: string; name: string; email: string }>(`/api/embed/${appId}/identify`, {
            method: "POST",
            body: JSON.stringify({ token: data.token }),
          });
          window.clearTimeout(waitTimer);
          signedIn(session.token, session.name || session.email);
        } catch (err) {
          window.clearTimeout(waitTimer);
          setStage({ kind: "error", message: err instanceof ApiError ? err.message : "Couldn't sign you in." });
        }
      };
      window.addEventListener("message", onMessage);
      window.parent.postMessage({ type: "cb:ready" }, "*");
    }

    start();
    return () => {
      cancelled = true;
      window.clearTimeout(waitTimer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [appId]);

  return (
    <div className="flex h-screen flex-col bg-white">
      <header className="flex items-center gap-2 px-4 py-3 text-white" style={{ background: config?.color ?? "#0f172a" }}>
        <div className="min-w-0 flex-1">
          <div className="truncate font-semibold">{config?.name ?? "Chat"}</div>
          {stage.kind === "ready" && stage.name && <div className="truncate text-xs opacity-80">Signed in as {stage.name}</div>}
        </div>
        {stage.kind === "ready" && (
          <button onClick={() => setNewChat((n) => n + 1)} className="rounded p-1.5 hover:bg-white/15" aria-label="New chat" title="New chat">
            <MessageSquarePlus size={18} />
          </button>
        )}
        <button onClick={close} className="rounded p-1.5 hover:bg-white/15" aria-label="Close chat">
          <X size={18} />
        </button>
      </header>
      <main className="flex min-h-0 flex-1">
        {stage.kind === "ready" && <ChatPanel appId={appId} compact newChatSignal={newChat} className="flex-1" />}
        {stage.kind === "login" && <WidgetLogin appId={appId} onSignedIn={signedIn} />}
        {(stage.kind === "loading" || stage.kind === "waiting") && (
          <p className="m-auto text-sm text-slate-500">{stage.kind === "waiting" ? "Signing you in..." : "Loading..."}</p>
        )}
        {stage.kind === "error" && <p className="m-auto max-w-xs px-6 text-center text-sm text-slate-600">{stage.message}</p>}
      </main>
    </div>
  );
}

function WidgetLogin({ appId, onSignedIn }: { appId: string; onSignedIn: (token: string, name: string) => void }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const session = await api<{ token: string; name: string; email: string }>(`/api/embed/${appId}/login`, {
        method: "POST",
        body: JSON.stringify({ email, password }),
      });
      onSignedIn(session.token, session.name || session.email);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't sign in.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="m-auto w-full max-w-xs space-y-3 px-6">
      <p className="text-sm text-slate-600">Sign in with your Chat Builder account.</p>
      <Input type="email" autoComplete="username" placeholder="Email" required value={email} onChange={(e) => setEmail(e.target.value)} />
      <Input type="password" autoComplete="current-password" placeholder="Password" required value={password} onChange={(e) => setPassword(e.target.value)} />
      {error && <p className="text-sm text-red-600">{error}</p>}
      <Button type="submit" disabled={busy} className="w-full">
        {busy ? "Signing in..." : "Sign in"}
      </Button>
    </form>
  );
}
