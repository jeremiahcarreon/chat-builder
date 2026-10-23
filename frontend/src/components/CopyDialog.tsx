import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Copy } from "lucide-react";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router";
import { ApiError, api } from "../api";
import { Button, Field, Input, Toggle } from "./ui";

interface CopyOptions {
  name: string;
  settings: { id: string; label: string; hint: string }[];
  sources: { id: string; kind: string; name: string; status: string }[];
  test_count: number;
  widget: boolean;
}

/** Makes a new app from this one, taking only the parts the person ticks. */
export function CopyButton({ appId }: { appId: string }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <Button variant="secondary" onClick={() => setOpen(true)}>
        <Copy size={14} /> Copy
      </Button>
      {open && <CopyDialog appId={appId} onClose={() => setOpen(false)} />}
    </>
  );
}

function CopyDialog({ appId, onClose }: { appId: string; onClose: () => void }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { data: options } = useQuery({
    queryKey: ["copy-options", appId],
    queryFn: () => api<CopyOptions>(`/api/apps/${appId}/copy-options`),
  });
  const [name, setName] = useState("");
  const [settings, setSettings] = useState<Set<string>>(new Set());
  const [sources, setSources] = useState<Set<string>>(new Set());
  const [tests, setTests] = useState(true);
  const [widget, setWidget] = useState(false);

  // Everything ticked to start with; untick what the new app shouldn't have.
  useEffect(() => {
    if (!options) return;
    setName(options.name);
    setSettings(new Set(options.settings.map((s) => s.id)));
    setSources(new Set(options.sources.map((s) => s.id)));
  }, [options]);

  const copy = useMutation({
    mutationFn: () =>
      api<{ app_id: string; name: string }>(`/api/apps/${appId}/copy`, {
        method: "POST",
        body: JSON.stringify({ name, settings: [...settings], sources: [...sources], tests, widget }),
      }),
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: ["apps"] });
      onClose();
      navigate(`/apps/${result.app_id}`);
    },
  });

  function toggle(set: Set<string>, update: (next: Set<string>) => void, id: string, on: boolean) {
    const next = new Set(set);
    if (on) next.add(id);
    else next.delete(id);
    update(next);
  }

  return (
    <div className="fixed inset-0 z-30 flex items-center justify-center bg-slate-900/40 p-4" onClick={onClose}>
      <div
        className="flex max-h-full w-full max-w-lg flex-col rounded-lg border border-slate-200 bg-white shadow-xl"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-label="Copy app"
      >
        <div className="border-b border-slate-200 px-5 py-4">
          <h2 className="text-lg font-semibold">Copy this app</h2>
          <p className="mt-1 text-sm text-slate-500">
            Choose what the new app starts with. Conversations, API keys and who has access aren't copied.
          </p>
        </div>
        {!options ? (
          <p className="p-5 text-sm text-slate-500">Loading...</p>
        ) : (
          <div className="min-h-0 flex-1 space-y-5 overflow-y-auto px-5 py-4 text-sm">
            <Field label="Name of the new app">
              <Input value={name} onChange={(e) => setName(e.target.value)} maxLength={200} required />
            </Field>

            <Section title="Settings" hint="The provider and model always come along.">
              {options.settings.map((s) => (
                <Toggle
                  key={s.id}
                  checked={settings.has(s.id)}
                  onChange={(on) => toggle(settings, setSettings, s.id, on)}
                  label={s.label}
                  hint={s.hint}
                />
              ))}
              {options.widget && (
                <Toggle
                  checked={widget}
                  onChange={setWidget}
                  label="Widget settings"
                  hint="Allowed sites, sign-in mode and colour. The copy gets its own signing secret."
                />
              )}
            </Section>

            <Section
              title={`Data sources (${options.sources.length})`}
              hint="Copied with their search index and connection details, so nothing is reprocessed."
            >
              {options.sources.length === 0 && <p className="text-slate-500">This app has no data sources.</p>}
              {options.sources.map((s) => (
                <Toggle
                  key={s.id}
                  checked={sources.has(s.id)}
                  onChange={(on) => toggle(sources, setSources, s.id, on)}
                  label={s.name}
                  hint={`${s.kind}${s.status !== "ready" ? ` · ${s.status}` : ""}`}
                />
              ))}
            </Section>

            <Section title="Quality">
              <Toggle
                checked={tests && options.test_count > 0}
                onChange={setTests}
                label={`Test set (${options.test_count} question${options.test_count === 1 ? "" : "s"})`}
                hint="Test runs and their results stay with the original."
              />
            </Section>
          </div>
        )}
        <div className="flex items-center gap-2 border-t border-slate-200 px-5 py-4">
          <Button onClick={() => copy.mutate()} disabled={!options || !name.trim() || copy.isPending}>
            {copy.isPending ? "Copying..." : "Create copy"}
          </Button>
          <Button variant="secondary" onClick={onClose} disabled={copy.isPending}>
            Cancel
          </Button>
          {copy.error && (
            <span className="text-sm text-red-600">{copy.error instanceof ApiError ? copy.error.message : "Couldn't copy the app."}</span>
          )}
        </div>
      </div>
    </div>
  );
}

function Section({ title, hint, children }: { title: string; hint?: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="mb-2">
        <div className="font-medium">{title}</div>
        {hint && <div className="text-xs text-slate-500">{hint}</div>}
      </div>
      <div className="space-y-2.5">{children}</div>
    </div>
  );
}
