import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, type ModelPrice } from "../api";
import { Button, Card, Input } from "./ui";

/** Prices per million tokens, used for the Usage page's cost estimates. */
export function PricesCard() {
  const queryClient = useQueryClient();
  const { data } = useQuery({ queryKey: ["prices"], queryFn: () => api<ModelPrice[]>("/api/settings/prices") });
  const update = (next: ModelPrice[]) => {
    queryClient.setQueryData(["prices"], next);
    queryClient.invalidateQueries({ queryKey: ["usage"] });
  };
  return (
    <Card
      title="Model prices"
      description="US dollars per million tokens, for cost estimates on the Usage page. Claude prices are built in and local models are free; set prices for other models from your provider's pricing page."
    >
      {!data?.length ? (
        <p className="text-sm text-slate-500">Models appear here once they've been used.</p>
      ) : (
        <table className="w-full text-sm">
          <thead className="text-left text-slate-500">
            <tr>
              <th className="py-1 font-medium">Model</th>
              <th className="py-1 font-medium">Input</th>
              <th className="py-1 font-medium">Output</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {data.map((p) => (
              <PriceRow key={`${p.provider}/${p.model}`} price={p} onSaved={update} />
            ))}
          </tbody>
        </table>
      )}
    </Card>
  );
}

function PriceRow({ price, onSaved }: { price: ModelPrice; onSaved: (prices: ModelPrice[]) => void }) {
  const [input, setInput] = useState(price.input?.toString() ?? "");
  const [output, setOutput] = useState(price.output?.toString() ?? "");
  const dirty = input !== (price.input?.toString() ?? "") || output !== (price.output?.toString() ?? "");
  const save = useMutation({
    mutationFn: () =>
      api<ModelPrice[]>("/api/settings/prices", {
        method: "PUT",
        body: JSON.stringify({ provider: price.provider, model: price.model, input: Number(input), output: Number(output) }),
      }),
    onSuccess: onSaved,
  });
  const reset = useMutation({
    mutationFn: () =>
      api<ModelPrice[]>(`/api/settings/prices?provider=${encodeURIComponent(price.provider)}&model=${encodeURIComponent(price.model)}`, {
        method: "DELETE",
      }),
    onSuccess: (next) => {
      const row = next.find((p) => p.provider === price.provider && p.model === price.model);
      setInput(row?.input?.toString() ?? "");
      setOutput(row?.output?.toString() ?? "");
      onSaved(next);
    },
  });
  return (
    <tr className="border-t border-slate-100">
      <td className="py-2">
        {price.model} <span className="text-slate-500">({price.provider})</span>
        {price.source === "missing" && <span className="ml-2 text-xs text-amber-700">no price set</span>}
        {price.source === "custom" && <span className="ml-2 text-xs text-slate-500">custom</span>}
      </td>
      <td className="w-32 py-2 pr-2">
        <Input type="number" min={0} step="0.01" value={input} placeholder="$" onChange={(e) => setInput(e.target.value)} />
      </td>
      <td className="w-32 py-2 pr-2">
        <Input type="number" min={0} step="0.01" value={output} placeholder="$" onChange={(e) => setOutput(e.target.value)} />
      </td>
      <td className="py-2 text-right whitespace-nowrap">
        {dirty && input !== "" && output !== "" && (
          <Button variant="secondary" className="px-2 py-1 text-xs" onClick={() => save.mutate()} disabled={save.isPending}>
            Save
          </Button>
        )}
        {price.source === "custom" && !dirty && (
          <button onClick={() => reset.mutate()} className="text-xs text-slate-500 underline">
            reset
          </button>
        )}
      </td>
    </tr>
  );
}
