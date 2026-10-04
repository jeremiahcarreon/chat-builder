import { useQuery } from "@tanstack/react-query";
import { Globe, Lock, Plus } from "lucide-react";
import { Link } from "react-router";
import { api, type ChatApp } from "../api";

export function Apps() {
  const { data: apps, isLoading } = useQuery({ queryKey: ["apps"], queryFn: () => api<ChatApp[]>("/api/apps") });

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">Chat Apps</h1>
        <Link
          to="/apps/new"
          className="inline-flex items-center gap-2 rounded-md bg-slate-900 px-3 py-2 text-sm font-medium text-white hover:bg-slate-700"
        >
          <Plus size={16} /> New app
        </Link>
      </div>
      {isLoading ? (
        <p className="text-sm text-slate-500">Loading...</p>
      ) : !apps?.length ? (
        <div className="rounded-lg border border-dashed border-slate-300 bg-white p-10 text-center">
          <p className="font-medium">No chat apps yet</p>
          <p className="mt-1 text-sm text-slate-500">Create one, give it a personality and scope, then add data.</p>
        </div>
      ) : (
        <div className="grid gap-4 sm:grid-cols-2">
          {apps.map((app) => (
            <Link
              key={app.id}
              to={`/apps/${app.id}`}
              className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm transition hover:border-slate-400"
            >
              <div className="flex items-start justify-between gap-2">
                <h2 className="font-semibold">{app.name}</h2>
                {app.is_public ? (
                  <Globe size={16} className="text-emerald-600" aria-label="Public" />
                ) : (
                  <Lock size={16} className="text-slate-400" aria-label="Private" />
                )}
              </div>
              <p className="mt-1 line-clamp-2 text-sm text-slate-500">{app.description || "No description"}</p>
              <p className="mt-3 text-xs text-slate-500">
                {app.provider} · {app.model} · {app.source_count} data source{app.source_count === 1 ? "" : "s"} ·{" "}
                {app.conversation_count} conversation{app.conversation_count === 1 ? "" : "s"}
              </p>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
