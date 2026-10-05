import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronDown, ChevronRight, EyeOff } from "lucide-react";
import { useMemo, useState } from "react";
import { api } from "../api";
import { Button, Input, Textarea, Toggle } from "./ui";

interface TableInfo {
  name: string;
  columns: [string, string][];
  primary_key: string[];
  foreign_keys: [string, string, string][];
  rows: number | null;
  note: string;
  hidden: boolean;
  column_notes: Record<string, string>;
}

function rowsLabel(rows: number | null): string {
  if (rows === null) return "";
  if (rows >= 1_000_000) return `${(rows / 1_000_000).toFixed(1)}M rows`;
  if (rows >= 1_000) return `${Math.round(rows / 1_000)}k rows`;
  return `${rows} rows`;
}

/** Notes on a connected database's tables and columns, and which tables to hide. */
export function TablesPanel({ sourceId }: { sourceId: string }) {
  const { data } = useQuery({ queryKey: ["tables", sourceId], queryFn: () => api<TableInfo[]>(`/api/sources/${sourceId}/tables`) });
  const [search, setSearch] = useState("");
  const [open, setOpen] = useState<string | null>(null);
  const shown = useMemo(() => {
    const term = search.trim().toLowerCase();
    if (!data) return [];
    return term
      ? data.filter((t) => t.name.toLowerCase().includes(term) || t.note.toLowerCase().includes(term) || t.columns.some(([c]) => c.toLowerCase().includes(term)))
      : data;
  }, [data, search]);

  if (!data) return <p className="py-2 text-sm text-slate-500">Loading tables...</p>;
  if (!data.length) return <p className="py-2 text-sm text-slate-500">No tables yet. Re-read the schema after connecting.</p>;
  return (
    <div className="mt-3 space-y-3 rounded-lg border border-slate-200 bg-slate-50 p-4">
      <p className="text-sm text-slate-600">
        Explain what tables and columns mean (especially cryptic names) and hide tables the assistant should never use. The assistant sees these
        notes when choosing tables and writing queries.
      </p>
      <Input placeholder={`Search ${data.length} tables`} value={search} onChange={(e) => setSearch(e.target.value)} />
      <ul className="divide-y divide-slate-200 rounded-md border border-slate-200 bg-white">
        {shown.slice(0, 200).map((table) => (
          <li key={table.name}>
            <button onClick={() => setOpen(open === table.name ? null : table.name)} className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm">
              {open === table.name ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
              <span className={`font-mono ${table.hidden ? "text-slate-400 line-through" : ""}`}>{table.name}</span>
              {table.hidden && <EyeOff size={13} className="text-slate-400" aria-label="Hidden" />}
              <span className="min-w-0 flex-1 truncate text-xs text-slate-500">{table.note}</span>
              <span className="shrink-0 text-xs text-slate-400">{rowsLabel(table.rows)}</span>
            </button>
            {open === table.name && <TableEditor sourceId={sourceId} table={table} />}
          </li>
        ))}
      </ul>
      {shown.length > 200 && <p className="text-xs text-slate-500">Showing 200 of {shown.length}; search to narrow it down.</p>}
    </div>
  );
}

function TableEditor({ sourceId, table }: { sourceId: string; table: TableInfo }) {
  const queryClient = useQueryClient();
  const [note, setNote] = useState(table.note);
  const [hidden, setHidden] = useState(table.hidden);
  const [columns, setColumns] = useState<Record<string, string>>(table.column_notes);
  const save = useMutation({
    mutationFn: () =>
      api<TableInfo>(`/api/sources/${sourceId}/tables/${encodeURIComponent(table.name)}`, {
        method: "PUT",
        body: JSON.stringify({ note, hidden, column_notes: columns }),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["tables", sourceId] });
      queryClient.invalidateQueries({ queryKey: ["sources"] });
    },
  });
  return (
    <div className="space-y-3 border-t border-slate-100 bg-slate-50/60 px-4 py-3 text-sm">
      <Toggle checked={hidden} onChange={setHidden} label="Hide this table" hint="The assistant won't see or query it." />
      <label className="block space-y-1">
        <span className="font-medium">What this table holds</span>
        <Textarea rows={2} value={note} onChange={(e) => setNote(e.target.value)} placeholder="e.g. Order headers, one row per sales order. ORDDATE is the order date as YYYYMMDD." />
      </label>
      {(table.primary_key.length > 0 || table.foreign_keys.length > 0) && (
        <div className="text-xs text-slate-500">
          {table.primary_key.length > 0 && <div>Primary key: {table.primary_key.join(", ")}</div>}
          {table.foreign_keys.length > 0 && <div>Joins to: {table.foreign_keys.map(([c, t, r]) => `${c} → ${t}.${r}`).join("; ")}</div>}
        </div>
      )}
      <div className="space-y-1">
        <div className="font-medium">Column notes</div>
        <div className="max-h-72 space-y-1 overflow-y-auto pr-1">
          {table.columns.map(([column, type]) => (
            <div key={column} className="flex items-center gap-2">
              <span className="w-48 shrink-0 truncate font-mono text-xs" title={`${column} (${type})`}>
                {column} <span className="text-slate-400">{type}</span>
              </span>
              <Input
                className="py-1 text-xs"
                value={columns[column] ?? ""}
                placeholder="What it means (optional)"
                onChange={(e) => setColumns({ ...columns, [column]: e.target.value })}
              />
            </div>
          ))}
        </div>
      </div>
      <div className="flex items-center gap-3">
        <Button onClick={() => save.mutate()} disabled={save.isPending}>
          {save.isPending ? "Saving..." : "Save"}
        </Button>
        {save.isSuccess && <span className="text-emerald-700">Saved</span>}
        {save.error && <span className="text-red-600">{save.error.message}</span>}
      </div>
    </div>
  );
}
