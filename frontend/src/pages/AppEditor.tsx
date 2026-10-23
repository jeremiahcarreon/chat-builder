import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ExternalLink } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";
import { Link, useNavigate, useParams } from "react-router";
import { api, type ChatApp, type ChatAppInput, type CloudSwitch, type ProviderKey } from "../api";
import { RoutingCard } from "../components/RoutingCard";
import { ChatPanel } from "../components/ChatPanel";
import { Button, Card, Field, Input, Select, Tabs, Textarea, Toggle } from "../components/ui";
import { CopyButton } from "../components/CopyDialog";
import { ExportButton } from "../components/ExportDialog";
import { DataSourcesTab } from "./DataSourcesTab";
import { DeployTab } from "./DeployTab";
import { QualityTab } from "./QualityTab";

const blank: ChatAppInput = {
  name: "",
  description: "",
  welcome_message: "",
  provider: "anthropic",
  model: "claude-opus-5-5",
  personality: "",
  knowledge_notes: "",
  in_scope: "",
  out_of_scope: "",
  refusal_message: "",
  strict_mode: true,
  max_answer_tokens: 4096,
  messages_per_minute: 10,
  daily_token_limit: 500000,
  is_public: false,
  classifier_enabled: true,
  classifier_provider: null,
  classifier_model: null,
  classifier_strictness: "balanced",
  classifier_fail_open: true,
  classifier_purpose: "scope",
  temperature: null,
  seed: null,
  auto_route: false,
  route_tiers: [],
  router: "jev",
};

type Tab = "settings" | "sources" | "chat" | "quality" | "deploy";

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
      {/* Stays at the top of the scrolling area; the negative margin and matching padding
          cover the page's top padding so content doesn't show above the header. */}
      <div className="sticky top-0 z-10 -mt-8 space-y-6 bg-slate-50 pt-8">
        <div className="flex items-center justify-between gap-4">
          <div>
            <Link to="/apps" className="text-sm text-slate-500 hover:underline">
              ← Chat Apps
            </Link>
            <h1 className="text-2xl font-semibold">{app.name}</h1>
          </div>
          <div className="flex gap-2">
            <CopyButton appId={app.id} />
            <ExportButton appId={app.id} />
            <a
              href={`/chat/${app.id}`}
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-2 rounded-md border border-slate-300 bg-white px-3 py-2 text-sm hover:bg-slate-100"
            >
              Open chat page <ExternalLink size={14} />
            </a>
          </div>
        </div>
        <Tabs
          tabs={[
            { id: "settings", label: "Settings" },
            { id: "sources", label: "Data sources" },
            { id: "chat", label: "Try it" },
            { id: "quality", label: "Quality" },
            { id: "deploy", label: "Deploy" },
          ]}
          active={tab}
          onChange={setTab}
        />
      </div>
      {tab === "settings" && <AppSettingsForm initial={app} appId={app.id} />}
      {tab === "sources" && <DataSourcesTab appId={app.id} />}
      {tab === "chat" && <ChatPanel appId={app.id} showModel className="h-[70vh]" />}
      {tab === "quality" && <QualityTab appId={app.id} />}
      {tab === "deploy" && <DeployTab appId={app.id} />}
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
  const { data: cloudSwitch } = useQuery({
    queryKey: ["cloud-switch"],
    queryFn: () => api<CloudSwitch>("/api/settings/cloud-switch"),
  });
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
        {cloudSwitch?.enabled && form.provider === "ollama" && (
          <p className="mb-4 rounded-md border border-sky-200 bg-sky-50 px-3 py-2 text-sm text-sky-800">
            The cloud switch in Settings is on, so this local-model app currently answers with{" "}
            <strong>
              {cloudSwitch.provider} {cloudSwitch.chat_model}
            </strong>
            . Turn the switch off to return to {form.model}.
          </p>
        )}
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
              {providers
                ?.filter((p) => p.chat)
                .map((p) => (
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
                  : form.auto_route
                    ? "Used when automatic model choice is unavailable."
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

      <RoutingCard form={form} set={set} providers={providers} />

      <ResponseStyleCard form={form} set={set} />

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

      <GuardrailsCard form={form} set={set} providers={providers} />

      <Card
        title="Product knowledge"
        description="Rules the assistant always needs, such as how part numbers relate, what fields in your data mean, and known exceptions. Included with every question and tracked in prompt versions, so the Quality tab shows whether a change helped."
      >
        <Textarea
          rows={10}
          placeholder={"Part numbers share a base number: CC161, EC161, MC161 and AE161 are all based on 161 and have the same fitment and specs.\n- CC = 3-row, EC = 2-row, MC = 4-row, AE = 2-row with 1\" tubes.\n- FS161 is the fan shroud that fits all of the 161 radiators."}
          value={form.knowledge_notes}
          onChange={(e) => set("knowledge_notes", e.target.value)}
        />
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

function GuardrailsCard({
  form,
  set,
  providers,
}: {
  form: ChatAppInput;
  set: <K extends keyof ChatAppInput>(key: K, value: ChatAppInput[K]) => void;
  providers: ProviderKey[] | undefined;
}) {
  const ownProvider = providers?.find((p) => p.provider === form.provider);
  // With a TypeSafe key saved, apps without their own classifier choice use Jev.
  const jevReady = !!providers?.find((p) => p.provider === "typesafe")?.configured;
  const classifierProvider = providers?.find(
    (p) => p.provider === (form.classifier_provider ?? (jevReady ? "typesafe" : form.provider)),
  );
  const { data: models } = useQuery({
    queryKey: ["models", classifierProvider?.provider],
    queryFn: () => api<string[]>(`/api/settings/providers/${classifierProvider?.provider}/models`),
    enabled: !!classifierProvider?.configured,
    staleTime: 5 * 60_000,
  });

  return (
    <Card
      title="Guardrails"
      description="A fast System 1 classifier screens every message before the main model sees it. Off-topic and abusive messages get the refusal message and never reach the main model."
    >
      <div className="space-y-4">
        <Toggle
          checked={form.classifier_enabled}
          onChange={(v) => set("classifier_enabled", v)}
          label="Screen messages with the classifier"
          hint="Recommended for public apps. Adds a small, fast model call before each answer."
        />
        {form.classifier_enabled && (
          <>
            <Field
              label="What the classifier checks"
              hint={
                form.classifier_purpose === "reply"
                  ? "For apps that draft replies to messages people send (eBay buyers, tickets). Messages that don't need a reply, like a bare thanks, get the refusal message with \"no_reply\" as the reason, and nothing is drafted. The scope above still guides the main model."
                  : "Off-topic and abusive messages get the refusal message and never reach the main model."
              }
            >
              <Select
                value={form.classifier_purpose}
                onChange={(e) => set("classifier_purpose", e.target.value as ChatAppInput["classifier_purpose"])}
              >
                <option value="scope">The message is in scope</option>
                <option value="reply">The sender is expecting a reply</option>
              </Select>
            </Field>
            <div className="grid gap-4 sm:grid-cols-3">
              <Field label="Strictness">
                <Select
                  value={form.classifier_strictness}
                  onChange={(e) => set("classifier_strictness", e.target.value as ChatAppInput["classifier_strictness"])}
                >
                  {form.classifier_purpose === "reply" ? (
                    <>
                      <option value="lenient">Lenient: draft unless a reply is clearly not wanted</option>
                      <option value="balanced">Balanced</option>
                      <option value="strict">Strict: draft only when a reply is clearly expected</option>
                    </>
                  ) : (
                    <>
                      <option value="lenient">Lenient: block only clear misuse</option>
                      <option value="balanced">Balanced</option>
                      <option value="strict">Strict: only clearly in-scope questions</option>
                    </>
                  )}
                </Select>
              </Field>
              <Field
                label="Classifier provider"
                hint={
                  jevReady
                    ? "Jev, TypeSafe's System One model, is fast and costs a fraction of a cent per thousand checks."
                    : "Add a TypeSafe key in Settings to use Jev, a fast System One classifier."
                }
              >
                <Select
                  value={form.classifier_provider ?? ""}
                  onChange={(e) => {
                    set("classifier_provider", e.target.value || null);
                    set("classifier_model", null);
                  }}
                >
                  <option value="">
                    {jevReady ? "Default: TypeSafe Jev" : `Same as the app (${ownProvider?.label ?? form.provider})`}
                  </option>
                  {providers?.map((p) => (
                    <option key={p.provider} value={p.provider}>
                      {p.label}
                      {p.configured ? "" : " (no key yet)"}
                    </option>
                  ))}
                </Select>
              </Field>
              <Field
                label="Classifier model"
                hint={
                  classifierProvider?.default_classifier_model
                    ? `Blank uses ${classifierProvider.default_classifier_model}.`
                    : classifierProvider?.provider === form.provider
                      ? "Blank uses the app's own model."
                      : "Required for this provider."
                }
              >
                <Input
                  list="classifier-model-options"
                  placeholder={classifierProvider?.default_classifier_model}
                  value={form.classifier_model ?? ""}
                  onChange={(e) => set("classifier_model", e.target.value || null)}
                />
                <datalist id="classifier-model-options">
                  {models?.map((m) => <option key={m} value={m} />)}
                </datalist>
              </Field>
            </div>
            <Field label="If the classifier is unavailable">
              <Select
                value={form.classifier_fail_open ? "open" : "closed"}
                onChange={(e) => set("classifier_fail_open", e.target.value === "open")}
              >
                <option value="open">Let the message through (keeps the app working)</option>
                <option value="closed">Block the message (safer for public apps)</option>
              </Select>
            </Field>
          </>
        )}
      </div>
    </Card>
  );
}

function ResponseStyleCard({
  form,
  set,
}: {
  form: ChatAppInput;
  set: <K extends keyof ChatAppInput>(key: K, value: ChatAppInput[K]) => void;
}) {
  const { data: support } = useQuery({
    queryKey: ["sampling", form.provider, form.model],
    queryFn: () =>
      api<{ temperature: boolean; seed: boolean; note: string }>(
        `/api/settings/providers/${form.provider}/sampling?model=${encodeURIComponent(form.model)}`,
      ),
    enabled: !!form.model,
    staleTime: Infinity,
  });
  const useDefault = form.temperature === null;
  const value = form.temperature ?? 0.7;

  return (
    <Card
      title="Response style"
      description="Lower temperature gives consistent, predictable answers. Higher gives more varied, creative wording."
    >
      <div className="space-y-4">
        <Toggle
          checked={useDefault}
          onChange={(v) => set("temperature", v ? null : 0.3)}
          label="Use the model's default"
        />
        <div className={useDefault ? "pointer-events-none opacity-50" : ""}>
          <div className="flex items-center justify-between text-sm">
            <span className="font-medium">Temperature</span>
            <span className="tabular-nums text-slate-600">{useDefault ? "default" : value.toFixed(2)}</span>
          </div>
          <input
            type="range"
            min={0}
            max={1}
            step={0.05}
            value={value}
            disabled={useDefault}
            onChange={(e) => set("temperature", Number(e.target.value))}
            className="mt-2 w-full accent-slate-900"
            aria-label="Temperature"
          />
          <div className="flex justify-between text-xs text-slate-500">
            <span>Precise and consistent</span>
            <span>Varied and creative</span>
          </div>
        </div>
        <Field
          label="Seed (optional)"
          hint="With a low temperature, the same seed and question give the same answer, which is useful when testing prompt changes."
        >
          <Input
            type="number"
            min={0}
            placeholder="None"
            value={form.seed ?? ""}
            onChange={(e) => set("seed", e.target.value === "" ? null : Math.max(0, Math.floor(Number(e.target.value))))}
            className="sm:w-48"
          />
        </Field>
        {support && support.note && (
          <p className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900">
            {support.note} {!support.temperature && !support.seed ? "These settings are saved but have no effect with this model." : ""}
          </p>
        )}
      </div>
    </Card>
  );
}
