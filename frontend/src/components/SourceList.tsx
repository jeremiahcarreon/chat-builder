import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Database, FileSpreadsheet, FileText, Loader2, RefreshCw, Trash2 } from "lucide-react";
import { Link } from "react-router";
import { api, type DataSource } from "../api";

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

function summary(source: DataSource): string {
  const d = source.details;
  const parts: string[] = [KIND_LABELS[source.kind] ?? source.kind];
  if (d.host) parts.push(`${d.host}${d.port ? `:${d.port}` : ""}${d.database ? ` / ${d.database}` : ""}`);
  if (typeof d.size === "number") parts.push(`${(d.size / 1024).toFixed(d.size < 10240 ? 1 : 0)} KB`);
  if (d.page_count) parts.push(`${d.page_count} pages`);
  if (d.chunk_count) parts.push(`${d.chunk_count} passages`);
  if (d.table_count) parts.push(`${d.table_count} table${d.table_count === 1 ? "" : "s"}`);
  if (d.row_count) parts.push(`${Number(d.row_count).toLocaleString()} rows`);
  return parts.join(" · ");
}

export function SourceList({ sources, showApp = false }: { sources: DataSource[]; showApp?: boolean }) {
  const queryClient = useQueryClient();
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
          </div>
          <div className="flex gap-1">
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
