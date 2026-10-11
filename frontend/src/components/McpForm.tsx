import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, Plug, XCircle } from "lucide-react";
import { useState, type FormEvent } from "react";
import { api, type DataSource } from "../api";
import { Button, Card, Field, Input } from "./ui";

export interface McpTool {
  name: string;
  title: string;
  description: string;
  read_only: boolean;
  enabled: boolean;
}

interface TestResult {
  ok: boolean;
  message: string;
  tools: McpTool[];
}

/** Connect an MCP server (like the ERP's), or edit one (pass `existing`). */
export function McpForm({ appId, existing, onDone }: { appId: string; existing?: DataSource; onDone?: () => void }) {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(!!existing);
  const [name, setName] = useState(existing?.name ?? "");
  const [url, setUrl] = useState(String(existing?.details.url ?? ""));
  const [token, setToken] = useState("");
  const [result, setResult] = useState<TestResult | null>(null);
  const body = () => JSON.stringify({ name: name || "MCP server", url, token });

  const test = useMutation({
    mutationFn: () =>
      api<TestResult>(existing ? `/api/sources/${existing.id}/mcp/test` : `/api/apps/${appId}/sources/mcp/test`, {
        method: "POST",
        body: body(),
      }),
    onSuccess: setResult,
    onError: (err) => setResult({ ok: false, message: err.message, tools: [] }),
  });
  const save = useMutation({
    mutationFn: () =>
      existing
        ? api<DataSource>(`/api/sources/${existing.id}/mcp`, { method: "PUT", body: body() })
        : api<DataSource>(`/api/apps/${appId}/sources/mcp`, { method: "POST", body: body() }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["sources"] });
      queryClient.invalidateQueries({ queryKey: ["apps"] });
      setToken("");
      setResult(null);
      if (existing) onDone?.();
      else {
        setName("");
        setUrl("");
        setOpen(false);
      }
    },
  });

  function submit(event: FormEvent) {
    event.preventDefault();
    save.mutate();
  }

  const fields = (
    <form onSubmit={submit} className="space-y-4">
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Name" hint="How this system is shown to you and to the assistant.">
          <Input required value={name} onChange={(e) => setName(e.target.value)} placeholder="ERP" />
        </Field>
        <Field label="Server address" hint="The MCP endpoint, usually ending in /mcp.">
          <Input required value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://erp.example.com/mcp" />
        </Field>
        <Field
          label="Token (optional)"
          hint={existing ? "Leave blank to keep the saved token." : "Sent as a bearer token. Stored encrypted."}
        >
          <Input type="password" autoComplete="new-password" value={token} onChange={(e) => setToken(e.target.value)} />
        </Field>
      </div>

      <p className="rounded-md bg-amber-50 px-3 py-2 text-xs text-amber-900">
        Tools the server marks read-only are turned on; tools that might change data stay off until you turn them on.
        Give the server's token access to read-only tools only.
      </p>

      {result && (
        <div className="space-y-2">
          <p className={`flex items-center gap-2 text-sm ${result.ok ? "text-emerald-700" : "text-red-600"}`}>
            {result.ok ? <CheckCircle2 size={16} /> : <XCircle size={16} />} {result.message}
          </p>
          {result.tools.length > 0 && (
            <ul className="space-y-1 text-sm">
              {result.tools.map((t) => (
                <li key={t.name} className="flex items-center gap-2">
                  <span className="font-medium">{t.title}</span>
                  <ToolBadge readOnly={t.read_only} />
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
      {save.error && <p className="text-sm text-red-600">{save.error.message}</p>}

      <div className="flex gap-2">
        <Button type="button" variant="secondary" onClick={() => test.mutate()} disabled={test.isPending || !url}>
          {test.isPending ? "Testing..." : "Test connection"}
        </Button>
        <Button type="submit" disabled={save.isPending}>
          {save.isPending ? "Saving..." : existing ? "Save and re-read tools" : "Add server"}
        </Button>
        <Button
          type="button"
          variant="secondary"
          onClick={() => {
            setResult(null);
            if (existing) onDone?.();
            else setOpen(false);
          }}
        >
          Cancel
        </Button>
      </div>
    </form>
  );

  if (existing) return <div className="mt-3 rounded-md border border-slate-200 p-4">{fields}</div>;

  return (
    <Card
      title="Connect a system (MCP)"
      description="Let the assistant look things up in another system, like orders, tracking and returns in your ERP, through tools that system offers over MCP (Model Context Protocol)."
    >
      {open ? (
        fields
      ) : (
        <Button variant="secondary" onClick={() => setOpen(true)}>
          <Plug size={16} /> Add an MCP server
        </Button>
      )}
    </Card>
  );
}

function ToolBadge({ readOnly }: { readOnly: boolean }) {
  return readOnly ? (
    <span className="rounded-full bg-emerald-50 px-2 py-0.5 text-xs text-emerald-700">read-only</span>
  ) : (
    <span className="rounded-full bg-amber-50 px-2 py-0.5 text-xs text-amber-800">may change data</span>
  );
}

/** Which of a server's tools the app may use. */
export function McpToolsPanel({ sourceId }: { sourceId: string }) {
  const queryClient = useQueryClient();
  const { data: tools } = useQuery({
    queryKey: ["mcp-tools", sourceId],
    queryFn: () => api<McpTool[]>(`/api/sources/${sourceId}/mcp/tools`),
  });
  const save = useMutation({
    mutationFn: (enabled: string[]) =>
      api<McpTool[]>(`/api/sources/${sourceId}/mcp/tools`, { method: "PUT", body: JSON.stringify({ enabled }) }),
    onSuccess: (updated) => {
      queryClient.setQueryData(["mcp-tools", sourceId], updated);
      queryClient.invalidateQueries({ queryKey: ["sources"] });
    },
  });

  function toggle(tool: McpTool, on: boolean) {
    if (on && !tool.read_only && !confirm(`"${tool.title}" may change data in the other system. Let the assistant use it?`)) return;
    const enabled = (tools ?? []).filter((t) => (t.name === tool.name ? on : t.enabled)).map((t) => t.name);
    save.mutate(enabled);
  }

  if (!tools) return null;
  return (
    <div className="mt-3 space-y-2 rounded-md border border-slate-200 p-3">
      <p className="text-xs text-slate-500">Tools the assistant may use:</p>
      <ul className="space-y-2">
        {tools.map((t) => (
          <li key={t.name}>
            <label className="flex cursor-pointer items-start gap-3 text-sm">
              <input
                type="checkbox"
                className="mt-0.5 h-4 w-4 accent-slate-900"
                checked={t.enabled}
                disabled={save.isPending}
                onChange={(e) => toggle(t, e.target.checked)}
              />
              <span>
                <span className="flex flex-wrap items-center gap-2">
                  <span className="font-medium">{t.title}</span>
                  {t.title !== t.name && <code className="text-xs text-slate-400">{t.name}</code>}
                  <ToolBadge readOnly={t.read_only} />
                </span>
                {t.description && <span className="block text-xs text-slate-500">{t.description}</span>}
              </span>
            </label>
          </li>
        ))}
      </ul>
      {save.error && <p className="text-sm text-red-600">{save.error.message}</p>}
    </div>
  );
}
