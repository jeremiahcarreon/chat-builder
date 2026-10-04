import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ExternalLink } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";
import { Link, useNavigate, useParams } from "react-router";
import { api, type ChatApp, type ChatAppInput, type ProviderKey } from "../api";
import { ChatPanel } from "../components/ChatPanel";
import { Button, Card, Field, Input, Select, Tabs, Textarea, Toggle } from "../components/ui";
import { DataSourcesTab } from "./DataSourcesTab";

const blank: ChatAppInput = {
  name: "",
  description: "",
  welcome_message: "",
  provider: "anthropic",
  model: "claude-opus-5-5",
  personality: "",
  in_scope: "",
  out_of_scope: "",
  refusal_message: "",
  strict_mode: true,
  max_answer_tokens: 4096,
  messages_per_minute: 10,
  daily_token_limit: 500000,
  is_public: false,
};

type Tab = "settings" | "sources" | "chat";

export function AppEditor() {
  const { appId } = useParams();
  const isNew = !appId || appId === "new";
  const [tab, setTab] = useState<Tab>("settings");
  const { data: app } = useQuery({
    queryKey: ["app", appId],
    queryFn: () => api<ChatApp>(`/api/apps/${appId}`),
    enabled: !isNew,
  });

  if (isNew) {
    return (
      <div className="space-y-6">
        <h1 className="text-2xl font-semibold">New chat app</h1>
        <AppSettingsForm initial={blank} />
      </div>
    );
  }
  if (!app) return <p className="text-sm text-slate-500">Loading...</p>;

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between gap-4">
        <div>
          <Link to="/apps" className="text-sm text-slate-500 hover:underline">
            ← Chat Apps
          </Link>
          <h1 className="text-2xl font-semibold">{app.name}</h1>
        </div>
        <a
          href={`/chat/${app.id}`}
          target="_blank"
          rel="noreferrer"
          className="inline-flex items-center gap-2 rounded-md border border-slate-300 bg-white px-3 py-2 text-sm hover:bg-slate-100"
        >
          Open chat page <ExternalLink size={14} />
        </a>
      </div>
      <Tabs
        tabs={[
          { id: "settings", label: "Settings" },
          { id: "sources", label: "Data sources" },
          { id: "chat", label: "Try it" },
        ]}
        active={tab}
        onChange={setTab}
      />
      {tab === "settings" && <AppSettingsForm initial={app} appId={app.id} />}
      {tab === "sources" && <DataSourcesTab appId={app.id} />}
      {tab === "chat" && <ChatPanel appId={app.id} className="h-[70vh]" />}
    </div>
  );
}

function AppSettingsForm({ initial, appId }: { initial: ChatAppInput; appId?: string }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [form, setForm] = useState<ChatAppInput>(initial);
  const [saved, setSaved] = useState(false);
  const set = <K extends keyof ChatAppInput>(key: K, value: ChatAppInput[K]) => {
    setSaved(false);
    setForm((f) => ({ ...f, [key]: value }));
  };

  const { data: providers } = useQuery({
    queryKey: ["provider-keys"],
    queryFn: () => api<ProviderKey[]>("/api/settings/providers"),
  });
  const provider = providers?.find((p) => p.provider === form.provider);
  const { data: models, error: modelsError } = useQuery({
    queryKey: ["models", form.provider],
    queryFn: () => api<string[]>(`/api/settings/providers/${form.provider}/models`),
    enabled: !!provider?.configured,
    staleTime: 5 * 60_000,
  });

  useEffect(() => setForm(initial), [initial]);

  const save = useMutation({
    mutationFn: () =>
      appId
        ? api<ChatApp>(`/api/apps/${appId}`, { method: "PUT", body: JSON.stringify(form) })
        : api<ChatApp>("/api/apps", { method: "POST", body: JSON.stringify(form) }),
    onSuccess: (app) => {
      queryClient.invalidateQueries({ queryKey: ["apps"] });
      queryClient.setQueryData(["app", app.id], app);
      setSaved(true);
      if (!appId) navigate(`/apps/${app.id}`, { replace: true });
    },
  });
  const remove = useMutation({
    mutationFn: () => api(`/api/apps/${appId}`, { method: "DELETE" }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["apps"] });
      navigate("/apps");
    },
  });

  function submit(event: FormEvent) {
    event.preventDefault();
    save.mutate();
  }

  return (
    <form onSubmit={submit} className="space-y-6">
      <Card title="Basics">
        <div className="space-y-4">
          <Field label="Name">
            <Input required maxLength={200} value={form.name} onChange={(e) => set("name", e.target.value)} />
          </Field>
          <Field label="Description" hint="What this assistant is for. Also shown on the chat page.">
            <Textarea rows={2} value={form.description} onChange={(e) => set("description", e.target.value)} />
          </Field>
          <Field label="Welcome message" hint="Shown before the first message of a conversation.">
            <Textarea rows={2} value={form.welcome_message} onChange={(e) => set("welcome_message", e.target.value)} />
          </Field>
        </div>
      </Card>

      <Card title="Model">
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Provider">
            <Select
              value={form.provider}
              onChange={(e) => {
                const next = providers?.find((p) => p.provider === e.target.value);
                set("provider", e.target.value);
                if (next?.default_model) set("model", next.default_model);
              }}
            >
              {providers?.map((p) => (
                <option key={p.provider} value={p.provider}>
                  {p.label}
                  {p.configured ? "" : " (no key yet)"}
                </option>
              ))}
            </Select>
          </Field>
          <Field
            label="Model"
            hint={
              !provider?.configured
                ? "Add an API key for this provider in Settings to see its models."
                : modelsError
                  ? `Couldn't load models: ${modelsError.message}`
                  : "Pick from the list or type a model ID."
            }
          >
            <Input
              list="model-options"
              required
              value={form.model}
              onChange={(e) => set("model", e.target.value)}
            />
            <datalist id="model-options">
              {models?.map((m) => <option key={m} value={m} />)}
            </datalist>
          </Field>
        </div>
      </Card>

      <Card
        title="Personality and scope"
        description="These settings become the assistant's instructions. Keep scope specific so it can't be used for unrelated work."
      >
        <div className="space-y-4">
          <Field label="Personality" hint="Tone, style, and how it should talk to people.">
            <Textarea
              rows={3}
              placeholder="Friendly and professional. Short answers with clear steps."
              value={form.personality}
              onChange={(e) => set("personality", e.target.value)}
            />
          </Field>
          <Field label="In scope" hint="Topics it should help with, one per line or comma-separated.">
            <Textarea
              rows={3}
              placeholder="HVAC maintenance, service scheduling, warranty questions"
              value={form.in_scope}
              onChange={(e) => set("in_scope", e.target.value)}
            />
          </Field>
          <Field label="Out of scope" hint="Topics or behaviors it must refuse.">
            <Textarea
              rows={3}
              placeholder="Writing code, homework, legal or medical advice, pricing negotiations"
              value={form.out_of_scope}
              onChange={(e) => set("out_of_scope", e.target.value)}
            />
          </Field>
          <Field label="Refusal message" hint="What it says when a question is out of scope.">
            <Input
              placeholder="Sorry, I can only help with questions about our HVAC services."
              value={form.refusal_message}
              onChange={(e) => set("refusal_message", e.target.value)}
            />
          </Field>
          <Toggle
            checked={form.strict_mode}
            onChange={(v) => set("strict_mode", v)}
            label="Strict mode"
            hint="Answer only from this app's documents and data. Say 'I don't know' instead of guessing."
          />
        </div>
      </Card>

      <Card title="Limits and sharing">
        <div className="grid gap-4 sm:grid-cols-3">
          <Field label="Max answer length (tokens)">
            <Input
              type="number"
              min={64}
              max={64000}
              value={form.max_answer_tokens}
              onChange={(e) => set("max_answer_tokens", Number(e.target.value))}
            />
          </Field>
          <Field label="Messages per minute">
            <Input
              type="number"
              min={1}
              max={600}
              value={form.messages_per_minute}
              onChange={(e) => set("messages_per_minute", Number(e.target.value))}
            />
          </Field>
          <Field label="Daily token limit">
            <Input
              type="number"
              min={1000}
              value={form.daily_token_limit}
              onChange={(e) => set("daily_token_limit", Number(e.target.value))}
            />
          </Field>
        </div>
        <div className="mt-4">
          <Toggle
            checked={form.is_public}
            onChange={(v) => set("is_public", v)}
            label="Public chat page"
            hint="Anyone with the link can chat without logging in. Guardrails and limits still apply."
          />
        </div>
      </Card>

      <div className="flex items-center gap-3">
        <Button type="submit" disabled={save.isPending}>
          {save.isPending ? "Saving..." : appId ? "Save changes" : "Create app"}
        </Button>
        {saved && <span className="text-sm text-emerald-700">Saved</span>}
        {save.error && <span className="text-sm text-red-600">{save.error.message}</span>}
        {appId && (
          <Button
            type="button"
            variant="danger"
            className="ml-auto"
            onClick={() => confirm("Delete this app, its data sources and conversations?") && remove.mutate()}
          >
            Delete app
          </Button>
        )}
      </div>
    </form>
  );
}
