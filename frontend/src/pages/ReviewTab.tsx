import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, Pencil, ShieldAlert, Trash2, Wrench } from "lucide-react";
import { useState } from "react";
import { api } from "../api";
import { Button, Card, Select, Textarea } from "../components/ui";
import { money, StatTile } from "./Usage";

interface HeldAnswer {
  message_id: number;
  conversation_id: string;
  question: string;
  draft: string;
  tool_calls: { name: string; arguments: Record<string, unknown> | null; error: boolean }[];
  sources: string[];
  model: string | null;
  created_at: string;
  reasons: string[];
  intent: string | null;
}

interface IntentRecord {
  intent: string;
  tier: "auto" | "approval";
  pinned: boolean;
  reviewed: number;
  sent: number;
  sent_rate: number | null;
}

interface GateOverview {
  auto: number;
  held: number;
  waiting: number;
  hold_reasons: { reason: string; count: number }[];
  intents: IntentRecord[];
}

interface ReviewStats {
  days: number;
  answers: number;
  blocked: number;
  held_now: number;
  awaiting_caller: number;
  reviewed: number;
  sent: number;
  edited: number;
  discarded: number;
  median_review_minutes: number | null;
  cost: number | null;
  unpriced: string[];
}

function percent(part: number, whole: number): string {
  return whole ? `${Math.round((part / whole) * 100)}%` : "–";
}

/** Answers waiting for a person, and how often reviewed answers go out unchanged. */
export function ReviewTab({ appId, reviewing, gated }: { appId: string; reviewing: boolean; gated: boolean }) {
  const [days, setDays] = useState(30);
  const { data: stats } = useQuery({
    queryKey: ["review-stats", appId, days],
    queryFn: () => api<ReviewStats>(`/api/apps/${appId}/review/stats?days=${days}`),
  });
  const { data: overview } = useQuery({
    queryKey: ["review-gate", appId, days],
    queryFn: () => api<GateOverview>(`/api/apps/${appId}/review/gate?days=${days}`),
    enabled: gated,
  });
  const { data: held } = useQuery({
    queryKey: ["review", appId],
    queryFn: () => api<HeldAnswer[]>(`/api/apps/${appId}/review`),
    refetchInterval: 10_000,
  });

  return (
    <div className="space-y-6">
      {!reviewing && (
        <p className="rounded-md bg-amber-50 px-3 py-2 text-sm text-amber-900">
          Review is off for this app, so answers go straight to visitors. Turn on “Review answers to anonymous visitors”
          in Settings to hold them here. Answers given through the API still count below when the caller reports what it
          did with them.
        </p>
      )}

      <div className="flex items-center justify-between gap-4">
        <h2 className="text-lg font-semibold">Before anything runs unattended</h2>
        <div className="flex gap-1 rounded-md border border-slate-200 bg-white p-1">
          {[7, 30, 90].map((p) => (
            <button
              key={p}
              type="button"
              onClick={() => setDays(p)}
              aria-pressed={days === p}
              className={`rounded px-3 py-1 text-sm ${days === p ? "bg-slate-900 text-white" : "text-slate-600 hover:bg-slate-100"}`}
            >
              {p} days
            </button>
          ))}
        </div>
      </div>

      {stats && (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
          <StatTile
            label="Answers"
            value={String(stats.answers)}
            detail={`${stats.blocked} refused by the guardrails`}
          />
          <StatTile
            label="Held now"
            value={String(stats.held_now)}
            detail={stats.awaiting_caller ? `plus ${stats.awaiting_caller} waiting on the API caller` : "waiting for a person"}
          />
          <StatTile
            label="Sent unchanged"
            value={percent(stats.sent, stats.reviewed)}
            detail={`${stats.sent} of ${stats.reviewed} reviewed`}
          />
          <StatTile
            label="Edited or discarded"
            value={percent(stats.edited + stats.discarded, stats.reviewed)}
            detail={`${stats.edited} edited, ${stats.discarded} discarded${
              stats.median_review_minutes !== null ? ` · ${stats.median_review_minutes} min to review` : ""
            }`}
          />
          <StatTile
            label="Estimated cost"
            value={money(stats.cost)}
            detail={stats.unpriced.length ? `excludes ${stats.unpriced.length} unpriced model(s)` : `last ${stats.days} days`}
          />
        </div>
      )}

      {gated && overview && <GateSection appId={appId} overview={overview} />}

      <Card
        title={`Held answers${held?.length ? ` (${held.length})` : ""}`}
        description="Visitors see a short notice until you send, edit or discard the answer. Edits teach the app, like edited API drafts do."
      >
        {!held ? (
          <p className="text-sm text-slate-500">Loading...</p>
        ) : held.length === 0 ? (
          <p className="text-sm text-slate-500">Nothing is waiting.</p>
        ) : (
          <ul className="space-y-4">
            {held.map((h) => (
              <HeldItem key={h.message_id} appId={appId} item={h} />
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}

function HeldItem({ appId, item }: { appId: string; item: HeldAnswer }) {
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(item.draft);
  const decide = useMutation({
    mutationFn: (decision: "sent" | "edited" | "discarded") =>
      api(`/api/apps/${appId}/review/${item.message_id}`, {
        method: "POST",
        body: JSON.stringify({ decision, text: decision === "edited" ? text : null }),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["review", appId] });
      queryClient.invalidateQueries({ queryKey: ["review-stats", appId] });
    },
  });

  return (
    <li className="space-y-3 rounded-lg border border-slate-200 p-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <p className="font-medium">
          {item.question || "(no question)"}
          {item.intent && <span className="ml-2 rounded bg-slate-100 px-1.5 py-0.5 text-xs text-slate-600">{item.intent}</span>}
        </p>
        <span className="text-xs text-slate-500">
          {new Date(item.created_at).toLocaleString()}
          {item.model ? ` · ${item.model}` : ""}
        </span>
      </div>

      {item.reasons.length > 0 && (
        <ul className="space-y-1 rounded-md bg-amber-50 px-3 py-2 text-xs text-amber-900">
          {item.reasons.map((r, i) => (
            <li key={i} className="flex items-start gap-2">
              <ShieldAlert size={12} className="mt-0.5 shrink-0" />
              {r}
            </li>
          ))}
        </ul>
      )}

      {item.tool_calls.length > 0 && (
        <ul className="space-y-1 text-xs text-slate-600">
          {item.tool_calls.map((c, i) => (
            <li key={i} className="flex items-start gap-2">
              <Wrench size={12} className="mt-0.5 shrink-0" />
              <code className={c.error ? "text-red-700" : ""}>
                {c.name}({c.arguments ? JSON.stringify(c.arguments) : ""}){c.error ? " — refused or failed" : ""}
              </code>
            </li>
          ))}
        </ul>
      )}
      {item.sources.length > 0 && <p className="text-xs text-slate-500">Sources: {item.sources.join(", ")}</p>}

      {editing ? (
        <Textarea rows={8} value={text} onChange={(e) => setText(e.target.value)} />
      ) : (
        <div className="whitespace-pre-wrap rounded-md bg-slate-50 p-3 text-sm">{item.draft}</div>
      )}

      {decide.error && <p className="text-sm text-red-600">{(decide.error as Error).message}</p>}
      <div className="flex flex-wrap gap-2">
        {editing ? (
          <>
            <Button disabled={decide.isPending} onClick={() => decide.mutate("edited")}>
              <CheckCircle2 size={16} /> Send edited answer
            </Button>
            <Button variant="secondary" onClick={() => (setEditing(false), setText(item.draft))}>
              Cancel
            </Button>
          </>
        ) : (
          <>
            <Button disabled={decide.isPending} onClick={() => decide.mutate("sent")}>
              <CheckCircle2 size={16} /> Send as written
            </Button>
            <Button variant="secondary" onClick={() => setEditing(true)}>
              <Pencil size={16} /> Edit
            </Button>
            <Button variant="danger" disabled={decide.isPending} onClick={() => decide.mutate("discarded")}>
              <Trash2 size={16} /> Discard
            </Button>
          </>
        )}
      </div>
    </li>
  );
}

/** What the review gate let through, why it held the rest, and where each kind of question stands. */
function GateSection({ appId, overview }: { appId: string; overview: GateOverview }) {
  const queryClient = useQueryClient();
  const pin = useMutation({
    mutationFn: ({ intent, tier }: { intent: string; tier: string | null }) =>
      api(`/api/apps/${appId}/review/intents/${encodeURIComponent(intent)}`, {
        method: "PUT",
        body: JSON.stringify({ tier }),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["review-gate", appId] });
      queryClient.invalidateQueries({ queryKey: ["app", appId] });
    },
  });
  const total = overview.auto + overview.held;

  return (
    <Card
      title="Review gate"
      description="Answers go out on their own only when their tools were safe, every claim was backed by a lookup or document, and their kind of question has earned it."
    >
      <div className="space-y-5">
        <div className="grid gap-4 sm:grid-cols-3">
          <StatTile label="Went out on their own" value={percent(overview.auto, total)} detail={`${overview.auto} of ${total} answers`} />
          <StatTile label="Held" value={String(overview.held)} detail={`${overview.waiting} still waiting for a decision`} />
          <StatTile
            label="Top reason held"
            value={overview.hold_reasons[0] ? String(overview.hold_reasons[0].count) : "–"}
            detail={overview.hold_reasons[0]?.reason ?? "nothing held yet"}
          />
        </div>

        {overview.hold_reasons.length > 1 && (
          <ul className="space-y-1 text-sm text-slate-600">
            {overview.hold_reasons.map((r) => (
              <li key={r.reason} className="flex justify-between gap-4">
                <span>{r.reason}</span>
                <span className="tabular-nums">{r.count}</span>
              </li>
            ))}
          </ul>
        )}

        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-slate-200 text-left text-xs text-slate-500">
                <th className="py-2 pr-4 font-medium">Kind of question</th>
                <th className="py-2 pr-4 font-medium">Sent unchanged</th>
                <th className="py-2 pr-4 font-medium">Now</th>
                <th className="py-2 font-medium">Setting</th>
              </tr>
            </thead>
            <tbody>
              {overview.intents.map((r) => (
                <tr key={r.intent} className="border-b border-slate-100">
                  <td className="py-2 pr-4 capitalize">{r.intent}</td>
                  <td className="py-2 pr-4 tabular-nums">
                    {r.sent_rate === null ? "–" : `${Math.round(r.sent_rate * 100)}%`}
                    <span className="text-xs text-slate-500"> ({r.sent} of {r.reviewed})</span>
                  </td>
                  <td className="py-2 pr-4">
                    <span
                      className={`rounded px-1.5 py-0.5 text-xs ${
                        r.tier === "auto" ? "bg-emerald-50 text-emerald-800" : "bg-amber-50 text-amber-900"
                      }`}
                    >
                      {r.tier === "auto" ? "Goes out on its own" : "Needs approval"}
                    </span>
                  </td>
                  <td className="py-2">
                    <Select
                      aria-label={`Setting for ${r.intent}`}
                      value={r.pinned ? r.tier : "earned"}
                      disabled={pin.isPending}
                      onChange={(e) =>
                        pin.mutate({ intent: r.intent, tier: e.target.value === "earned" ? null : e.target.value })
                      }
                    >
                      <option value="earned">Earned from reviews</option>
                      <option value="approval">Always needs approval</option>
                      <option value="auto">Always on its own</option>
                    </Select>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </Card>
  );
}
