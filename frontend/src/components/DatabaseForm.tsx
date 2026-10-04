import { useMutation, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, Database, XCircle } from "lucide-react";
import { useState, type FormEvent } from "react";
import { api, type DataSource } from "../api";
import { Button, Card, Field, Input, Select, Toggle } from "./ui";

const KINDS = [
  { id: "mssql", label: "SQL Server", port: 1433 },
  { id: "mysql", label: "MySQL", port: 3306 },
  { id: "mariadb", label: "MariaDB", port: 3306 },
  { id: "postgres", label: "PostgreSQL", port: 5432 },
];
export const SERVER_KINDS = new Set(KINDS.map((k) => k.id));

interface FormState {
  name: string;
  kind: string;
  host: string;
  port: string;
  database: string;
  username: string;
  password: string;
  encryption: string;
  require_tls: boolean;
  schemas: string;
}

function initialState(existing?: DataSource): FormState {
  const d = existing?.details ?? {};
  return {
    name: existing?.name ?? "",
    kind: existing?.kind ?? "mssql",
    host: String(d.host ?? ""),
    port: d.port ? String(d.port) : "",
    database: String(d.database ?? ""),
    username: String(d.username ?? ""),
    password: "",
    encryption: String(d.encryption ?? "request"),
    require_tls: Boolean(d.require_tls),
    schemas: Array.isArray(d.schemas) ? (d.schemas as string[]).join(", ") : "",
  };
}

interface TestResult {
  ok: boolean;
  message: string;
}

/** Add a new database connection, or edit an existing one (pass `existing`). */
export function DatabaseForm({
  appId,
  existing,
  onDone,
}: {
  appId: string;
  existing?: DataSource;
  onDone?: () => void;
}) {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(!!existing);
  const [form, setForm] = useState<FormState>(() => initialState(existing));
  const [result, setResult] = useState<TestResult | null>(null);
  const set = <K extends keyof FormState>(key: K, value: FormState[K]) => {
    setResult(null);
    setForm((f) => ({ ...f, [key]: value }));
  };
  const kind = KINDS.find((k) => k.id === form.kind)!;

  const body = () =>
    JSON.stringify({
      name: form.name,
      kind: form.kind,
      host: form.host,
      port: form.port ? Number(form.port) : null,
      database: form.database,
      username: form.username,
      password: form.password,
      encryption: form.encryption,
      require_tls: form.require_tls,
      schemas: form.schemas
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean),
    });

  const test = useMutation({
    mutationFn: () =>
      api<TestResult>(existing ? `/api/sources/${existing.id}/database/test` : `/api/apps/${appId}/sources/database/test`, {
        method: "POST",
        body: body(),
      }),
    onSuccess: setResult,
    onError: (err) => setResult({ ok: false, message: err.message }),
  });
  const save = useMutation({
    mutationFn: () =>
      existing
        ? api<DataSource>(`/api/sources/${existing.id}/database`, { method: "PUT", body: body() })
        : api<DataSource>(`/api/apps/${appId}/sources/database`, { method: "POST", body: body() }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["sources"] });
      queryClient.invalidateQueries({ queryKey: ["apps"] });
      setForm(initialState(existing));
      setResult(null);
      if (existing) onDone?.();
      else setOpen(false);
    },
  });

  function submit(event: FormEvent) {
    event.preventDefault();
    save.mutate();
  }

  const fields = (
    <form onSubmit={submit} className="space-y-4">
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Name" hint="How this source is shown to you and to the assistant.">
          <Input required value={form.name} onChange={(e) => set("name", e.target.value)} placeholder="Service records" />
        </Field>
        <Field label="Database type">
          <Select value={form.kind} disabled={!!existing} onChange={(e) => set("kind", e.target.value)}>
            {KINDS.map((k) => (
              <option key={k.id} value={k.id}>
                {k.label}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="Host or IP address" hint="From inside Docker, use host.docker.internal for a database on this computer.">
          <Input required value={form.host} onChange={(e) => set("host", e.target.value)} placeholder="192.168.1.20" />
        </Field>
        <Field label="Port">
          <Input
            type="number"
            min={1}
            max={65535}
            value={form.port}
            onChange={(e) => set("port", e.target.value)}
            placeholder={String(kind.port)}
          />
        </Field>
        <Field label="Database name">
          <Input value={form.database} onChange={(e) => set("database", e.target.value)} />
        </Field>
        <Field label="Username">
          <Input autoComplete="off" value={form.username} onChange={(e) => set("username", e.target.value)} />
        </Field>
        <Field label="Password" hint={existing ? "Leave blank to keep the saved password." : "Stored encrypted."}>
          <Input
            type="password"
            autoComplete="new-password"
            value={form.password}
            onChange={(e) => set("password", e.target.value)}
          />
        </Field>
        {form.kind === "mssql" ? (
          <Field label="Encryption">
            <Select value={form.encryption} onChange={(e) => set("encryption", e.target.value)}>
              <option value="request">Use if the server supports it</option>
              <option value="require">Required</option>
              <option value="off">Off</option>
            </Select>
          </Field>
        ) : (
          <div className="flex items-end pb-2">
            <Toggle checked={form.require_tls} onChange={(v) => set("require_tls", v)} label="Require an encrypted (TLS) connection" />
          </div>
        )}
        {form.kind !== "mysql" && form.kind !== "mariadb" && (
          <Field label="Schemas to include (optional)" hint="Comma-separated, e.g. dbo, sales. Leave blank for all.">
            <Input value={form.schemas} onChange={(e) => set("schemas", e.target.value)} />
          </Field>
        )}
      </div>

      <p className="rounded-md bg-amber-50 px-3 py-2 text-xs text-amber-900">
        Use a database login that can only read data. The app also blocks anything except single SELECT queries,
        but a read-only login is the strongest protection.
      </p>

      {result && (
        <p className={`flex items-center gap-2 text-sm ${result.ok ? "text-emerald-700" : "text-red-600"}`}>
          {result.ok ? <CheckCircle2 size={16} /> : <XCircle size={16} />} {result.message}
        </p>
      )}
      {save.error && <p className="text-sm text-red-600">{save.error.message}</p>}

      <div className="flex gap-2">
        <Button type="button" variant="secondary" onClick={() => test.mutate()} disabled={test.isPending || !form.host}>
          {test.isPending ? "Testing..." : "Test connection"}
        </Button>
        <Button type="submit" disabled={save.isPending}>
          {save.isPending ? "Saving..." : existing ? "Save and re-read schema" : "Add database"}
        </Button>
        <Button
          type="button"
          variant="secondary"
          onClick={() => {
            setForm(initialState(existing));
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
      title="Connect a database"
      description="SQL Server, MySQL, MariaDB or PostgreSQL. The assistant queries it live, read-only. For SQLite, upload the .db file above."
    >
      {open ? (
        fields
      ) : (
        <Button variant="secondary" onClick={() => setOpen(true)}>
          <Database size={16} /> Add a database connection
        </Button>
      )}
    </Card>
  );
}
