import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, Pencil, Trash2, Wrench } from "lucide-react";
import { useState } from "react";
import { api } from "../api";
import { Button, Card, Textarea } from "../components/ui";
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
export function ReviewTab({ appId, reviewing }: { appId: string; reviewing: boolean }) {
  const [days, setDays] = useState(30);
  const { data: stats } = useQuery({
    queryKey: ["review-stats", appId, days],
    queryFn: () => api<ReviewStats>(`/api/apps/${appId}/review/stats?days=${days}`),
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
        <p className="font-medium">{item.question || "(no question)"}</p>
        <span className="text-xs text-slate-500">
          {new Date(item.created_at).toLocaleString()}
          {item.model ? ` · ${item.model}` : ""}
        </span>
      </div>

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
