import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { RefreshCw } from "lucide-react";
import { useState } from "react";
import { api, type ProviderKey, type ServiceStatus } from "../api";
import { Button, Card, Input, StatusDot } from "../components/ui";

export function Settings() {
  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold">Settings</h1>
      <ProviderKeys />
      <SystemStatus />
    </div>
  );
}

function ProviderKeys() {
  const { data: keys, isLoading } = useQuery({
    queryKey: ["provider-keys"],
    queryFn: () => api<ProviderKey[]>("/api/settings/providers"),
  });

  return (
    <Card
      title="LLM provider API keys"
      description="Keys are encrypted before they're stored. Testing a key lists the provider's models, which costs nothing."
    >
      {isLoading ? (
        <p className="text-sm text-slate-500">Loading...</p>
      ) : (
        <div className="divide-y divide-slate-200">
          {keys?.map((key) => <ProviderKeyRow key={key.provider} item={key} />)}
        </div>
      )}
    </Card>
  );
}

function ProviderKeyRow({ item }: { item: ProviderKey }) {
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState("");
  const path = `/api/settings/providers/${item.provider}`;
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["provider-keys"] });

  const save = useMutation({
    mutationFn: () => api<ProviderKey>(path, { method: "PUT", body: JSON.stringify({ api_key: value }) }),
    onSuccess: () => {
      setEditing(false);
      setValue("");
      refresh();
    },
  });
  const test = useMutation({ mutationFn: () => api<ProviderKey>(`${path}/test`, { method: "POST" }), onSuccess: refresh });
  const remove = useMutation({ mutationFn: () => api(path, { method: "DELETE" }), onSuccess: refresh });

  return (
    <div className="py-4 first:pt-0 last:pb-0">
      <div className="flex items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <StatusDot ok={item.configured ? item.last_test_ok : null} />
          <div>
            <div className="font-medium">{item.label}</div>
            <div className="text-sm text-slate-500">
              {!item.configured
                ? "Not configured"
                : item.last_test_ok
                  ? `Working · key ending in ${item.last4}`
                  : `${item.last_test_error ?? "Not tested"} · key ending in ${item.last4}`}
            </div>
          </div>
        </div>
        {!editing && (
          <div className="flex gap-2">
            {item.configured && (
              <Button variant="secondary" onClick={() => test.mutate()} disabled={test.isPending}>
                {test.isPending ? "Testing..." : "Test"}
              </Button>
            )}
            <Button variant="secondary" onClick={() => setEditing(true)}>
              {item.configured ? "Replace" : "Add key"}
            </Button>
            {item.configured && (
              <Button
                variant="danger"
                onClick={() => confirm(`Remove the ${item.label} key?`) && remove.mutate()}
                disabled={remove.isPending}
              >
                Remove
              </Button>
            )}
          </div>
        )}
      </div>
      {editing && (
        <form
          className="mt-3 flex gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            save.mutate();
          }}
        >
          <Input
            type="password"
            autoComplete="off"
            placeholder={item.key_hint}
            value={value}
            onChange={(e) => setValue(e.target.value)}
            autoFocus
          />
          <Button type="submit" disabled={value.trim().length < 8 || save.isPending}>
            {save.isPending ? "Saving..." : "Save & test"}
          </Button>
          <Button
            type="button"
            variant="secondary"
            onClick={() => {
              setEditing(false);
              setValue("");
            }}
          >
            Cancel
          </Button>
        </form>
      )}
      {save.error && <p className="mt-2 text-sm text-red-600">{save.error.message}</p>}
    </div>
  );
}

function SystemStatus() {
  const { data, isFetching, refetch } = useQuery({
    queryKey: ["system-status"],
    queryFn: () => api<ServiceStatus[]>("/api/system/status"),
    refetchInterval: 15_000,
  });

  return (
    <Card title="System status" description="Checks refresh every 15 seconds.">
      <ul className="space-y-3">
        {data?.map((service) => (
          <li key={service.name} className="flex items-center gap-3 text-sm">
            <StatusDot ok={service.ok} />
            <span className="w-56 font-medium">{service.name}</span>
            <span className="text-slate-500">{service.detail}</span>
          </li>
        ))}
      </ul>
      <Button variant="secondary" className="mt-4" onClick={() => refetch()} disabled={isFetching}>
        <RefreshCw size={14} className={isFetching ? "animate-spin" : ""} />
        Refresh
      </Button>
    </Card>
  );
}
