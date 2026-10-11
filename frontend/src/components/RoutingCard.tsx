import { useQuery } from "@tanstack/react-query";
import { ArrowDown, ArrowUp, Plus, Trash2 } from "lucide-react";
import { api, type ChatAppInput, type ProviderKey, type RouteTier } from "../api";
import { Button, Card, Field, Input, Select, Toggle } from "./ui";

const MAX_TIERS = 4;

// Suggested ladders, cheapest first. Each model starts at its lowest effort.
const SUGGESTED: Record<string, RouteTier[]> = {
  anthropic: [
    { provider: "anthropic", model: "claude-haiku-5-5", efforts: ["low"], use_for: "" },
    { provider: "anthropic", model: "claude-sonnet-5-5", efforts: ["low", "high"], use_for: "" },
    { provider: "anthropic", model: "claude-opus-5-5", efforts: ["low", "high"], use_for: "" },
  ],
  openai: [
    { provider: "openai", model: "gpt-5-mini", efforts: ["low"], use_for: "" },
    { provider: "openai", model: "gpt-5", efforts: ["low", "high"], use_for: "" },
  ],
  gemini: [
    { provider: "gemini", model: "gemini-2.5-flash", efforts: ["low"], use_for: "" },
    { provider: "gemini", model: "gemini-2.5-pro", efforts: ["low", "high"], use_for: "" },
  ],
};
const CLOUD = ["anthropic", "openai", "gemini"];

function suggestedLadder(form: ChatAppInput, providers: ProviderKey[] | undefined): RouteTier[] {
  if (form.provider !== "ollama") return SUGGESTED[form.provider] ?? [];
  // A local app starts on its own model and moves up to the first cloud provider with a key.
  const cloud = CLOUD.find((id) => providers?.find((p) => p.provider === id)?.configured);
  const local: RouteTier = { provider: "ollama", model: form.model, efforts: [], use_for: "" };
  return cloud ? [local, ...SUGGESTED[cloud].slice(-2)] : [local];
}

export function RoutingCard({
  form,
  set,
  providers,
}: {
  form: ChatAppInput;
  set: <K extends keyof ChatAppInput>(key: K, value: ChatAppInput[K]) => void;
  providers: ProviderKey[] | undefined;
}) {
  const jevReady = !!providers?.find((p) => p.provider === "typesafe")?.configured;
  const hasCloudKey = CLOUD.some((id) => providers?.find((p) => p.provider === id)?.configured);
  const tiers = form.route_tiers;
  const steps = tiers.reduce((n, t) => n + Math.max(1, t.efforts.length), 0);

  const update = (index: number, change: Partial<RouteTier>) =>
    set("route_tiers", tiers.map((t, i) => (i === index ? { ...t, ...change } : t)));
  const move = (index: number, by: number) => {
    const next = [...tiers];
    [next[index], next[index + by]] = [next[index + by], next[index]];
    set("route_tiers", next);
  };

  function turnOn(on: boolean) {
    set("auto_route", on);
    if (on && tiers.length === 0) set("route_tiers", suggestedLadder(form, providers));
    if (on && !jevReady) set("router", "llm");
  }

  if (form.provider === "ollama" && !hasCloudKey && !form.auto_route) {
    return (
      <Card title="Automatic model choice">
        <p className="text-sm text-slate-600">
          This app always answers with its local model. Add a cloud provider's API key in Settings to let it move up to a
          cloud model when the local one doesn't answer a question well.
        </p>
      </Card>
    );
  }

  return (
    <Card
      title="Automatic model choice"
      description="Each question goes to the cheapest model that fits it, starting at that model's lowest effort. If the person says an answer missed, the conversation moves up a step: more effort first, then a bigger model."
    >
      <div className="space-y-4">
        <Toggle
          checked={form.auto_route}
          onChange={turnOn}
          label={form.provider === "ollama" ? "Move up to a cloud model when needed" : "Choose the model automatically"}
          hint="Keeps simple questions on inexpensive models. Adds one fast routing check per message."
        />
        {form.auto_route && (
          <>
            <Field
              label="Chosen by"
              hint={
                jevReady
                  ? "Jev judges difficulty and satisfaction in about a tenth of a second, for a fraction of a cent per thousand messages."
                  : "Add a TypeSafe key in Settings to let Jev choose."
              }
            >
              <Select value={form.router} onChange={(e) => set("router", e.target.value as ChatAppInput["router"])}>
                <option value="jev" disabled={!jevReady}>
                  TypeSafe Jev (recommended){jevReady ? "" : " (no key yet)"}
                </option>
                <option value="llm">The app's classifier model</option>
              </Select>
            </Field>

            <div className="space-y-3">
              <p className="text-sm font-medium">Models, cheapest first</p>
              {tiers.map((tier, index) => (
                <TierRow
                  key={index}
                  tier={tier}
                  index={index}
                  count={tiers.length}
                  providers={providers}
                  onChange={(change) => update(index, change)}
                  onMove={(by) => move(index, by)}
                  onRemove={() => set("route_tiers", tiers.filter((_, i) => i !== index))}
                />
              ))}
              <div className="flex flex-wrap gap-2">
                <Button
                  type="button"
                  variant="secondary"
                  disabled={tiers.length >= MAX_TIERS}
                  onClick={() =>
                    set("route_tiers", [...tiers, { provider: form.provider, model: "", efforts: [], use_for: "" }])
                  }
                >
                  <Plus size={14} /> Add a model
                </Button>
                <Button type="button" variant="secondary" onClick={() => set("route_tiers", suggestedLadder(form, providers))}>
                  Use the suggested models
                </Button>
              </div>
              {steps < 2 && (
                <p className="text-sm text-amber-700">Add at least two models or effort levels for the router to choose between.</p>
              )}
            </div>
          </>
        )}
      </div>
    </Card>
  );
}

function TierRow({
  tier,
  index,
  count,
  providers,
  onChange,
  onMove,
  onRemove,
}: {
  tier: RouteTier;
  index: number;
  count: number;
  providers: ProviderKey[] | undefined;
  onChange: (change: Partial<RouteTier>) => void;
  onMove: (by: number) => void;
  onRemove: () => void;
}) {
  const provider = providers?.find((p) => p.provider === tier.provider);
  const { data: models } = useQuery({
    queryKey: ["models", tier.provider],
    queryFn: () => api<string[]>(`/api/settings/providers/${tier.provider}/models`),
    enabled: !!provider?.configured,
    staleTime: 5 * 60_000,
  });
  const { data: support } = useQuery({
    queryKey: ["sampling", tier.provider, tier.model],
    queryFn: () =>
      api<{ efforts: string[] }>(
        `/api/settings/providers/${tier.provider}/sampling?model=${encodeURIComponent(tier.model)}`,
      ),
    enabled: !!tier.model,
    staleTime: Infinity,
  });
  const available = support?.efforts ?? [];
  const listId = `tier-models-${index}`;

  return (
    <div className="rounded-md border border-slate-200 p-3">
      <div className="grid gap-3 sm:grid-cols-[10rem_1fr_auto]">
        <Select
          aria-label="Provider"
          value={tier.provider}
          onChange={(e) => onChange({ provider: e.target.value, model: "", efforts: [] })}
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
        <div>
          <Input
            aria-label="Model"
            list={listId}
            placeholder="Model ID"
            value={tier.model}
            onChange={(e) => onChange({ model: e.target.value, efforts: [] })}
          />
          <datalist id={listId}>{models?.map((m) => <option key={m} value={m} />)}</datalist>
        </div>
        <div className="flex gap-1">
          <IconButton label="Move up" disabled={index === 0} onClick={() => onMove(-1)}>
            <ArrowUp size={14} />
          </IconButton>
          <IconButton label="Move down" disabled={index === count - 1} onClick={() => onMove(1)}>
            <ArrowDown size={14} />
          </IconButton>
          <IconButton label="Remove" onClick={onRemove}>
            <Trash2 size={14} />
          </IconButton>
        </div>
      </div>
      <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-2 text-sm">
        {available.length > 0 ? (
          <>
            <span className="text-slate-500">Effort levels to try:</span>
            {available.map((effort) => (
              <label key={effort} className="flex items-center gap-1.5">
                <input
                  type="checkbox"
                  className="h-4 w-4 accent-slate-900"
                  checked={tier.efforts.includes(effort)}
                  onChange={(e) =>
                    onChange({
                      efforts: e.target.checked
                        ? available.filter((x) => x === effort || tier.efforts.includes(x))
                        : tier.efforts.filter((x) => x !== effort),
                    })
                  }
                />
                {effort}
              </label>
            ))}
          </>
        ) : (
          tier.model && <span className="text-slate-500">This model has no effort setting.</span>
        )}
      </div>
      <Input
        className="mt-3"
        aria-label="Used for"
        maxLength={300}
        placeholder="Used for (optional), e.g. stock and price lookups"
        value={tier.use_for}
        onChange={(e) => onChange({ use_for: e.target.value })}
      />
    </div>
  );
}

function IconButton({
  label,
  disabled,
  onClick,
  children,
}: {
  label: string;
  disabled?: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      disabled={disabled}
      onClick={onClick}
      className="flex h-9 w-9 items-center justify-center rounded-md border border-slate-300 text-slate-600 hover:bg-slate-100 disabled:opacity-30"
    >
      {children}
    </button>
  );
}
