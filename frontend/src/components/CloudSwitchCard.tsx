import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api, type CloudSwitch, type ProviderKey } from "../api";
import { Button, Card, Field, Input, Select, Toggle } from "./ui";

const CLOUD = ["anthropic", "openai", "gemini"] as const;

/** One switch that moves every local-model app to a cloud provider, and back. */
export function CloudSwitchCard() {
  const queryClient = useQueryClient();
  const { data: current } = useQuery({
    queryKey: ["cloud-switch"],
    queryFn: () => api<CloudSwitch>("/api/settings/cloud-switch"),
  });
  const { data: providers } = useQuery({
    queryKey: ["provider-keys"],
    queryFn: () => api<ProviderKey[]>("/api/settings/providers"),
  });
  const [form, setForm] = useState<CloudSwitch | null>(null);
  useEffect(() => {
    if (current) setForm(current);
  }, [current]);

  const provider = providers?.find((p) => p.provider === form?.provider);
  const { data: models } = useQuery({
    queryKey: ["models", form?.provider],
    queryFn: () => api<string[]>(`/api/settings/providers/${form?.provider}/models`),
    enabled: !!provider?.configured,
    staleTime: 5 * 60_000,
  });

  const save = useMutation({
    mutationFn: (next: CloudSwitch) =>
      api<CloudSwitch>("/api/settings/cloud-switch", { method: "PUT", body: JSON.stringify(next) }),
    onSuccess: (saved) => {
      queryClient.setQueryData(["cloud-switch"], saved);
      queryClient.invalidateQueries({ queryKey: ["apps"] });
    },
  });

  if (!form) return null;
  const dirty = JSON.stringify(form) !== JSON.stringify(current);

  return (
    <Card
      title="Run local-model apps in the cloud"
      description="Moves every app that uses a local Ollama model, including its classifier, to a cloud provider. The apps' own settings aren't changed, so switching off returns them to their local models."
    >
      <div className="space-y-4">
        <Toggle
          checked={form.enabled}
          onChange={(enabled) => {
            const next = { ...form, enabled };
            setForm(next);
            // Turning it off takes effect immediately; turning it on needs a model first.
            if (!enabled || next.chat_model) save.mutate(next);
          }}
          label={form.enabled ? "On: local-model apps use the cloud provider below" : "Off: local-model apps use Ollama"}
          hint={`${form.local_app_count} app${form.local_app_count === 1 ? "" : "s"} currently use local models.`}
        />
        <div className="grid gap-4 sm:grid-cols-3">
          <Field label="Cloud provider">
            <Select
              value={form.provider}
              onChange={(e) => {
                const next = providers?.find((p) => p.provider === e.target.value);
                setForm({
                  ...form,
                  provider: e.target.value as CloudSwitch["provider"],
                  chat_model: next?.default_model ?? "",
                  classifier_model: "",
                });
              }}
            >
              {CLOUD.map((id) => {
                const p = providers?.find((x) => x.provider === id);
                return (
                  <option key={id} value={id}>
                    {p?.label ?? id}
                    {p?.configured ? "" : " (no key yet)"}
                  </option>
                );
              })}
            </Select>
          </Field>
          <Field label="Chat model" hint={provider?.configured ? "Pick from the list or type a model ID." : "Add this provider's API key above first."}>
            <Input
              list="cloud-switch-models"
              placeholder={provider?.default_model}
              value={form.chat_model}
              onChange={(e) => setForm({ ...form, chat_model: e.target.value })}
            />
          </Field>
          <Field label="Classifier model" hint={`Blank uses ${provider?.default_classifier_model || "the chat model"}.`}>
            <Input
              list="cloud-switch-models"
              placeholder={provider?.default_classifier_model}
              value={form.classifier_model}
              onChange={(e) => setForm({ ...form, classifier_model: e.target.value })}
            />
          </Field>
          <datalist id="cloud-switch-models">
            {models?.map((m) => <option key={m} value={m} />)}
          </datalist>
        </div>
        <div className="flex items-center gap-3">
          <Button onClick={() => save.mutate(form)} disabled={!dirty || save.isPending}>
            {save.isPending ? "Saving..." : "Save"}
          </Button>
          {save.error && <span className="text-sm text-red-600">{save.error.message}</span>}
          {!dirty && current?.enabled && (
            <span className="text-sm text-emerald-700">
              Active: local apps answer with {provider?.label} {current.chat_model}
            </span>
          )}
        </div>
      </div>
    </Card>
  );
}
