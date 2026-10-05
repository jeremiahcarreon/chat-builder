import { useQuery } from "@tanstack/react-query";
import { useQueryClient } from "@tanstack/react-query";
import { Globe, Lock, Plus, Upload } from "lucide-react";
import { useRef, useState } from "react";
import { Link, useNavigate } from "react-router";
import { api, type ChatApp, type CloudSwitch } from "../api";

export function Apps() {
  const { data: apps, isLoading } = useQuery({ queryKey: ["apps"], queryFn: () => api<ChatApp[]>("/api/apps") });
  const { data: cloudSwitch } = useQuery({
    queryKey: ["cloud-switch"],
    queryFn: () => api<CloudSwitch>("/api/settings/cloud-switch"),
  });

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">Chat Apps</h1>
        <div className="flex gap-2">
        <ImportButton />
        <Link
          to="/apps/new"
          className="inline-flex items-center gap-2 rounded-md bg-slate-900 px-3 py-2 text-sm font-medium text-white hover:bg-slate-700"
        >
          <Plus size={16} /> New app
        </Link>
        </div>
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
                {cloudSwitch?.enabled && app.provider === "ollama" ? (
                  <span title={`Local model ${app.model}, moved to the cloud by the switch in Settings`}>
                    {cloudSwitch.provider} · {cloudSwitch.chat_model} (cloud switch)
                  </span>
                ) : (
                  <>
                    {app.provider} · {app.model}
                  </>
                )}{" "}
                · {app.source_count} data source{app.source_count === 1 ? "" : "s"} ·{" "}
                {app.conversation_count} conversation{app.conversation_count === 1 ? "" : "s"}
              </p>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}

function ImportButton() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const input = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);

  async function upload(file: File | undefined) {
    if (!file) return;
    setBusy(true);
    try {
      const form = new FormData();
      form.append("file", file);
      const response = await fetch("/api/apps/import", { method: "POST", body: form, credentials: "same-origin" });
      const result = await response.json();
      if (!response.ok) throw new Error(typeof result.detail === "string" ? result.detail : "Import failed");
      const notes = [];
      if (result.needs_password?.length) notes.push(`Enter the password for: ${result.needs_password.join(", ")} (Data sources tab).`);
      if (result.new_signing_secret) notes.push("The widget has a new signing secret; update the sites that embed it (Deploy tab).");
      notes.push("Add provider API keys in Settings if this install doesn't have them yet.");
      alert(`Imported "${result.name}".\n\n${notes.join("\n")}`);
      queryClient.invalidateQueries({ queryKey: ["apps"] });
      navigate(`/apps/${result.app_id}`);
    } catch (err) {
      alert(err instanceof Error ? err.message : "Import failed");
    } finally {
      setBusy(false);
      if (input.current) input.current.value = "";
    }
  }

  return (
    <>
      <button
        onClick={() => input.current?.click()}
        disabled={busy}
        className="inline-flex items-center gap-2 rounded-md border border-slate-300 bg-white px-3 py-2 text-sm font-medium hover:bg-slate-100"
      >
        <Upload size={16} /> {busy ? "Importing..." : "Import app"}
      </button>
      <input ref={input} type="file" accept=".zip" className="hidden" onChange={(e) => upload(e.target.files?.[0])} />
    </>
  );
}
