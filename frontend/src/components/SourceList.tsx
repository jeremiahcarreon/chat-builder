import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Database, FileSpreadsheet, FileText, Loader2, Pencil, RefreshCw, Table2, Trash2 } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router";
import { api, type DataSource } from "../api";
import { DatabaseForm, SERVER_KINDS } from "./DatabaseForm";
import { TablesPanel } from "./TablesPanel";

const KIND_LABELS: Record<string, string> = {
  file: "Document",
  spreadsheet: "Spreadsheet",
  sqlite: "SQLite",
  mssql: "SQL Server",
  mysql: "MySQL",
  mariadb: "MariaDB",
  postgres: "PostgreSQL",
};

function KindIcon({ kind }: { kind: string }) {
  if (kind === "file") return <FileText size={18} className="text-slate-500" />;
  if (kind === "spreadsheet") return <FileSpreadsheet size={18} className="text-emerald-600" />;
  return <Database size={18} className="text-sky-600" />;
}

function StatusBadge({ source }: { source: DataSource }) {
  const styles: Record<DataSource["status"], string> = {
    pending: "bg-slate-100 text-slate-600",
    processing: "bg-sky-50 text-sky-700",
    ready: "bg-emerald-50 text-emerald-700",
    failed: "bg-red-50 text-red-700",
  };
  return (
    <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium ${styles[source.status]}`}>
      {(source.status === "pending" || source.status === "processing") && <Loader2 size={12} className="animate-spin" />}
      {source.status}
    </span>
  );
}

function fileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} bytes`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(bytes < 10240 ? 1 : 0)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function summary(source: DataSource): string {
  const d = source.details;
  const parts: string[] = [KIND_LABELS[source.kind] ?? source.kind];
  if (d.host) parts.push(`${d.host}${d.port ? `:${d.port}` : ""}${d.database ? ` / ${d.database}` : ""}`);
  if (typeof d.size === "number") parts.push(fileSize(d.size));
  if (d.page_count) parts.push(`${d.page_count} page${d.page_count === 1 ? "" : "s"}`);
  if (d.chunk_count) parts.push(`${d.chunk_count} passage${d.chunk_count === 1 ? "" : "s"}`);
  if (d.table_count) parts.push(`${d.table_count} table${d.table_count === 1 ? "" : "s"}`);
  if (d.hidden_count) parts.push(`${d.hidden_count} hidden`);
  if (d.row_count) parts.push(`${Number(d.row_count).toLocaleString()} row${d.row_count === 1 ? "" : "s"}`);
  return parts.join(" · ");
}

export function SourceList({ sources, showApp = false }: { sources: DataSource[]; showApp?: boolean }) {
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState<string | null>(null);
  const [tables, setTables] = useState<string | null>(null);
  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ["sources"] });
    queryClient.invalidateQueries({ queryKey: ["apps"] });
  };
  const reprocess = useMutation({
    mutationFn: (id: string) => api(`/api/sources/${id}/reprocess`, { method: "POST" }),
    onSuccess: refresh,
  });
  const remove = useMutation({
    mutationFn: (id: string) => api(`/api/sources/${id}`, { method: "DELETE" }),
    onSuccess: refresh,
  });

  if (!sources.length) {
    return <p className="text-sm text-slate-500">No data sources yet.</p>;
  }
  return (
    <ul className="divide-y divide-slate-200">
      {sources.map((source) => (
        <li key={source.id} className="flex items-start gap-3 py-3">
          <div className="mt-0.5">
            <KindIcon kind={source.kind} />
          </div>
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2">
              <span className="truncate font-medium">{source.name}</span>
              <StatusBadge source={source} />
            </div>
            <div className="text-xs text-slate-500">
              {showApp && source.app_name && (
                <>
                  <Link to={`/apps/${source.app_id}`} className="underline">
                    {source.app_name}
                  </Link>
                  {" · "}
                </>
              )}
              {summary(source)}
            </div>
            {source.error && <div className="mt-1 text-xs text-red-600">{source.error}</div>}
            {source.kind === "file" && <SkuTags source={source} onSaved={refresh} />}
            {editing === source.id && (
              <DatabaseForm appId={source.app_id} existing={source} onDone={() => setEditing(null)} />
            )}
            {tables === source.id && <TablesPanel sourceId={source.id} />}
          </div>
          <div className="flex gap-1">
            {(SERVER_KINDS.has(source.kind) || source.kind === "sqlite") && source.status === "ready" && (
              <button
                onClick={() => setTables(tables === source.id ? null : source.id)}
                className="rounded p-1.5 text-slate-500 hover:bg-slate-100"
                title="Tables: notes and hiding"
              >
                <Table2 size={15} />
              </button>
            )}
            {SERVER_KINDS.has(source.kind) && (
              <button
                onClick={() => setEditing(editing === source.id ? null : source.id)}
                className="rounded p-1.5 text-slate-500 hover:bg-slate-100"
                title="Edit connection"
              >
                <Pencil size={15} />
              </button>
            )}
            <button
              onClick={() => reprocess.mutate(source.id)}
              className="rounded p-1.5 text-slate-500 hover:bg-slate-100"
              title={source.kind === "file" || source.kind === "spreadsheet" ? "Process again" : "Re-read schema"}
            >
              <RefreshCw size={15} />
            </button>
            <button
              onClick={() => confirm(`Remove ${source.name}?`) && remove.mutate(source.id)}
              className="rounded p-1.5 text-slate-500 hover:bg-red-50 hover:text-red-600"
              title="Remove"
            >
              <Trash2 size={15} />
            </button>
          </div>
        </li>
      ))}
    </ul>
  );
}

export function isBusy(sources: DataSource[] | undefined): boolean {
  return !!sources?.some((s) => s.status === "pending" || s.status === "processing");
}

/** SKU tags on a document: questions naming one of these SKUs search this document first. */
function SkuTags({ source, onSaved }: { source: DataSource; onSaved: () => void }) {
  const tags = (source.details.skus as unknown as string[] | undefined) ?? [];
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(tags.join(", "));
  const save = useMutation({
    mutationFn: () =>
      api(`/api/sources/${source.id}`, {
        method: "PATCH",
        body: JSON.stringify({ skus: value.split(/[,\s]+/).filter(Boolean) }),
      }),
    onSuccess: () => {
      setEditing(false);
      onSaved();
    },
  });
  if (editing) {
    return (
      <form
        className="mt-1 flex items-center gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          save.mutate();
        }}
      >
        <input
          autoFocus
          value={value}
          onChange={(e) => setValue(e.target.value)}
          placeholder="SKUs, e.g. RK-2, RK-2HD"
          className="w-64 rounded border border-slate-300 px-2 py-1 text-xs"
        />
        <button type="submit" className="text-xs font-medium text-slate-900 underline" disabled={save.isPending}>
          Save
        </button>
        <button type="button" className="text-xs text-slate-500" onClick={() => setEditing(false)}>
          Cancel
        </button>
      </form>
    );
  }
  return (
    <div className="mt-1 flex flex-wrap items-center gap-1 text-xs">
      {tags.map((tag) => (
        <span key={tag} className="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-slate-700">
          {tag}
        </span>
      ))}
      <button onClick={() => { setValue(tags.join(", ")); setEditing(true); }} className="text-slate-500 underline" title="Questions naming one of these SKUs search this document first">
        {tags.length ? "edit SKUs" : "tag SKUs"}
      </button>
    </div>
  );
}
