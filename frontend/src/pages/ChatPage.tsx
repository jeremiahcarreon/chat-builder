import { useQuery } from "@tanstack/react-query";
import { useParams } from "react-router";
import { api, type AppInfo } from "../api";
import { ChatPanel } from "../components/ChatPanel";

/** Standalone chat page: works for the owner and, if the app is public, for anyone. */
export function ChatPage() {
  const { appId = "" } = useParams();
  const { data: info, error, isLoading } = useQuery({
    queryKey: ["chat-info", appId],
    queryFn: () => api<AppInfo>(`/api/chat/${appId}`),
  });

  if (isLoading) return null;
  if (error || !info) {
    return (
      <div className="flex min-h-screen items-center justify-center text-slate-500">
        This chat isn't available.
      </div>
    );
  }
  return (
    <div className="flex h-screen flex-col">
      <header className="border-b border-slate-200 bg-white px-5 py-3">
        <h1 className="font-semibold">{info.name}</h1>
        {info.description && <p className="text-sm text-slate-500">{info.description}</p>}
      </header>
      <main className="flex min-h-0 flex-1 p-4">
        <ChatPanel appId={appId} className="flex-1" />
      </main>
    </div>
  );
}
