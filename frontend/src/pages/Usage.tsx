import { useQuery } from "@tanstack/react-query";
import { AlertTriangle } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router";
import { api, type UsageReport } from "../api";
import { Card } from "../components/ui";

// Categorical slots 1 and 2 of the reference palette (validated: CVD ΔE 24.7, contrast >= 3:1).
const INPUT_COLOR = "#2a78d6";
const OUTPUT_COLOR = "#eb6834";

const PERIODS = [7, 30, 90] as const;

const VERDICT_LABELS: Record<string, string> = {
  off_topic: "Off-topic",
  abuse: "Misuse",
  rate_limited: "Rate limited",
  classifier_error: "Classifier unavailable",
};

const number = (n: number) => n.toLocaleString();

export function money(amount: number | null): string {
  if (amount === null) return "–";
  if (amount > 0 && amount < 0.01) return "<$0.01";
  return amount.toLocaleString(undefined, { style: "currency", currency: "USD" });
}

function compact(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(n >= 10_000_000 ? 0 : 1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(n >= 10_000 ? 0 : 1)}k`;
  return String(n);
}

function shortDate(iso: string): string {
  return new Date(`${iso}T00:00:00Z`).toLocaleDateString(undefined, { month: "short", day: "numeric", timeZone: "UTC" });
}

export function Usage() {
  const [days, setDays] = useState<(typeof PERIODS)[number]>(30);
  const { data, isLoading } = useQuery({
    queryKey: ["usage", days],
    queryFn: () => api<UsageReport>(`/api/usage?days=${days}`),
  });

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-semibold">Usage</h1>
        <div className="inline-flex rounded-md border border-slate-300 bg-white p-0.5" role="group" aria-label="Time range">
          {PERIODS.map((p) => (
            <button
              key={p}
              onClick={() => setDays(p)}
              aria-pressed={days === p}
              className={`rounded px-3 py-1 text-sm ${days === p ? "bg-slate-900 text-white" : "text-slate-600 hover:bg-slate-100"}`}
            >
              {p} days
            </button>
          ))}
        </div>
      </div>

      {isLoading || !data ? (
        <p className="text-sm text-slate-500">Loading...</p>
      ) : (
        <>
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
            <StatTile
              label="Estimated cost"
              value={money(data.cost)}
              detail={
                data.unpriced_models.length
                  ? `excludes ${data.unpriced_models.length} model${data.unpriced_models.length === 1 ? "" : "s"} with no price`
                  : `last ${data.days} days`
              }
            />
            <StatTile
              label="Tokens"
              value={compact(data.input_tokens + data.output_tokens)}
              detail={`${compact(data.input_tokens)} in · ${compact(data.output_tokens)} out`}
            />
            <StatTile label="Questions" value={number(data.questions)} detail={`last ${data.days} days`} />
            <StatTile
              label="Blocked"
              value={number(data.blocked)}
              detail={
                data.questions ? `${((data.blocked / data.questions) * 100).toFixed(1)}% of questions` : "no questions yet"
              }
            />
            <StatTile label="Conversations" value={number(data.conversations)} detail={`last ${data.days} days`} />
          </div>

          <Card title="Tokens per day" description="Input and output tokens across all apps, including the classifier.">
            <TokenChart data={data.by_day} />
          </Card>

          <Card title="By app">
            <AppTable data={data} />
          </Card>

          {data.routing.length > 0 && (
            <Card
              title="Automatic model choice"
              description="Where routed answers went, and what they cost compared with sending every one to the app's most capable model. Includes the routing checks."
            >
              <RoutingTable data={data.routing} />
            </Card>
          )}

          <Card title="By model" description="Chat answers, classifier checks and test runs are listed separately. Costs are estimates from model prices in Settings.">
            <ModelTable data={data} />
          </Card>

          <Card title="Recently blocked questions" description="Review these to tune each app's scope and strictness.">
            <BlockedList data={data} />
          </Card>
        </>
      )}
    </div>
  );
}

export function StatTile({ label, value, detail }: { label: string; value: string; detail: string }) {
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
      <div className="text-sm text-slate-500">{label}</div>
      <div className="mt-1 text-3xl font-semibold tabular-nums">{value}</div>
      <div className="mt-1 text-xs text-slate-500">{detail}</div>
    </div>
  );
}

function TokenChart({ data }: { data: UsageReport["by_day"] }) {
  const [showTable, setShowTable] = useState(false);
  const [hover, setHover] = useState<number | null>(null);
  const max = Math.max(1, ...data.map((d) => d.input_tokens + d.output_tokens));
  const ticks = [max, max / 2, 0];
  const labelEvery = Math.max(1, Math.ceil(data.length / 6));
  const total = data.reduce((sum, d) => sum + d.input_tokens + d.output_tokens, 0);

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-3 text-sm">
        <div className="flex items-center gap-4 text-slate-600">
          <LegendSwatch color={INPUT_COLOR} label="Input tokens" />
          <LegendSwatch color={OUTPUT_COLOR} label="Output tokens" />
        </div>
        <button onClick={() => setShowTable((v) => !v)} className="text-slate-600 underline hover:text-slate-900">
          {showTable ? "Show chart" : "Show as table"}
        </button>
      </div>

      {showTable ? (
        <div className="max-h-80 overflow-y-auto">
          <table className="w-full text-sm">
            <thead className="sticky top-0 bg-white text-left text-slate-500">
              <tr>
                <th className="py-1 font-medium">Date</th>
                <th className="py-1 text-right font-medium">Input</th>
                <th className="py-1 text-right font-medium">Output</th>
                <th className="py-1 text-right font-medium">Total</th>
              </tr>
            </thead>
            <tbody className="tabular-nums">
              {data.map((d) => (
                <tr key={d.date} className="border-t border-slate-100">
                  <td className="py-1">{shortDate(d.date)}</td>
                  <td className="py-1 text-right">{number(d.input_tokens)}</td>
                  <td className="py-1 text-right">{number(d.output_tokens)}</td>
                  <td className="py-1 text-right">{number(d.input_tokens + d.output_tokens)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : total === 0 ? (
        <p className="py-10 text-center text-sm text-slate-500">No token usage in this period yet.</p>
      ) : (
        <div className="flex gap-2">
          {/* Y axis */}
          <div className="relative h-48 w-10 shrink-0 text-right text-xs tabular-nums text-slate-500">
            {ticks.map((t, i) => (
              <span key={i} className="absolute right-0 -translate-y-1/2" style={{ top: `${(i / (ticks.length - 1)) * 100}%` }}>
                {compact(Math.round(t))}
              </span>
            ))}
          </div>
          <div className="min-w-0 flex-1">
            <div className="relative h-48" onMouseLeave={() => setHover(null)}>
              {/* Recessive gridlines */}
              {ticks.map((_, i) => (
                <div
                  key={i}
                  className={`absolute inset-x-0 border-t ${i === ticks.length - 1 ? "border-slate-300" : "border-slate-100"}`}
                  style={{ top: `${(i / (ticks.length - 1)) * 100}%` }}
                />
              ))}
              <div className="absolute inset-0 flex items-end gap-[2px]">
                {data.map((d, i) => {
                  const inputPct = (d.input_tokens / max) * 100;
                  const outputPct = (d.output_tokens / max) * 100;
                  return (
                    <div
                      key={d.date}
                      className="relative flex h-full min-w-0 flex-1 flex-col justify-end"
                      onMouseEnter={() => setHover(i)}
                      aria-label={`${shortDate(d.date)}: ${number(d.input_tokens)} input, ${number(d.output_tokens)} output tokens`}
                    >
                      {/* Output on top: rounded free end, 2px surface gap to the input segment below */}
                      {d.output_tokens > 0 && (
                        <div
                          className="w-full rounded-t-[4px]"
                          style={{
                            height: `${outputPct}%`,
                            background: OUTPUT_COLOR,
                            marginBottom: d.input_tokens > 0 ? 2 : 0,
                            opacity: hover === null || hover === i ? 1 : 0.55,
                          }}
                        />
                      )}
                      {d.input_tokens > 0 && (
                        <div
                          className={`w-full ${d.output_tokens > 0 ? "" : "rounded-t-[4px]"}`}
                          style={{
                            height: `${inputPct}%`,
                            background: INPUT_COLOR,
                            opacity: hover === null || hover === i ? 1 : 0.55,
                          }}
                        />
                      )}
                    </div>
                  );
                })}
              </div>
              {hover !== null && <Tooltip day={data[hover]} index={hover} count={data.length} />}
            </div>
            {/* X axis */}
            <div className="relative mt-1 h-4 text-xs text-slate-500">
              {data.map((d, i) => {
                const last = i === data.length - 1;
                // Every Nth day, plus the last day, skipping a label that would crowd the last one.
                const show = last || (i % labelEvery === 0 && data.length - 1 - i >= labelEvery / 2);
                return show ? (
                  <span
                    key={d.date}
                    className="absolute -translate-x-1/2 whitespace-nowrap"
                    style={{ left: `${((i + 0.5) / data.length) * 100}%` }}
                  >
                    {shortDate(d.date)}
                  </span>
                ) : null;
              })}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function Tooltip({ day, index, count }: { day: UsageReport["by_day"][number]; index: number; count: number }) {
  const left = ((index + 0.5) / count) * 100;
  return (
    <div
      className="pointer-events-none absolute top-0 z-10 w-44 rounded-md border border-slate-200 bg-white p-2 text-xs shadow-lg"
      style={left > 60 ? { right: `${100 - left + 2}%` } : { left: `${left + 2}%` }}
    >
      <div className="font-medium text-slate-900">{shortDate(day.date)}</div>
      <TooltipRow color={INPUT_COLOR} label="Input" value={day.input_tokens} />
      <TooltipRow color={OUTPUT_COLOR} label="Output" value={day.output_tokens} />
      <div className="mt-1 flex justify-between border-t border-slate-100 pt-1 text-slate-600">
        <span>Total</span>
        <span className="tabular-nums">{number(day.input_tokens + day.output_tokens)}</span>
      </div>
    </div>
  );
}

function TooltipRow({ color, label, value }: { color: string; label: string; value: number }) {
  return (
    <div className="mt-1 flex items-center justify-between gap-2 text-slate-600">
      <span className="flex items-center gap-1.5">
        <span className="inline-block h-2 w-2 rounded-sm" style={{ background: color }} />
        {label}
      </span>
      <span className="tabular-nums text-slate-900">{number(value)}</span>
    </div>
  );
}

function LegendSwatch({ color, label }: { color: string; label: string }) {
  return (
    <span className="flex items-center gap-1.5">
      <span className="inline-block h-3 w-3 rounded-sm" style={{ background: color }} />
      {label}
    </span>
  );
}

function AppTable({ data }: { data: UsageReport }) {
  if (!data.by_app.length) return <p className="text-sm text-slate-500">No apps yet.</p>;
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead className="text-left text-slate-500">
          <tr>
            <th className="py-1 font-medium">App</th>
            <th className="py-1 text-right font-medium">Questions</th>
            <th className="py-1 text-right font-medium">Blocked</th>
            <th className="py-1 text-right font-medium">Tokens</th>
            <th className="py-1 text-right font-medium">Cost</th>
            <th className="py-1 pl-4 font-medium">Today vs daily limit</th>
          </tr>
        </thead>
        <tbody className="tabular-nums">
          {data.by_app.map((a) => {
            const pct = a.daily_token_limit ? Math.min(100, (a.tokens_today / a.daily_token_limit) * 100) : 0;
            const near = pct >= 80;
            return (
              <tr key={a.app_id} className="border-t border-slate-100">
                <td className="py-2">
                  <Link to={`/apps/${a.app_id}`} className="hover:underline">
                    {a.name}
                  </Link>
                </td>
                <td className="py-2 text-right">{number(a.questions)}</td>
                <td className="py-2 text-right">{number(a.blocked)}</td>
                <td className="py-2 text-right">{number(a.input_tokens + a.output_tokens)}</td>
                <td className="py-2 text-right">{money(a.cost)}</td>
                <td className="py-2 pl-4">
                  <div className="flex items-center gap-2">
                    <div className="h-1.5 w-24 overflow-hidden rounded-full bg-slate-100">
                      <div className="h-full rounded-full bg-slate-500" style={{ width: `${pct}%` }} />
                    </div>
                    <span className="text-xs text-slate-600">
                      {compact(a.tokens_today)} / {compact(a.daily_token_limit)}
                    </span>
                    {near && (
                      <span className="inline-flex items-center gap-1 text-xs text-amber-700">
                        <AlertTriangle size={12} /> {pct >= 100 ? "Limit reached" : "Near limit"}
                      </span>
                    )}
                  </div>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function ModelTable({ data }: { data: UsageReport }) {
  if (!data.by_model.length) return <p className="text-sm text-slate-500">No model calls in this period.</p>;
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead className="text-left text-slate-500">
          <tr>
            <th className="py-1 font-medium">Used for</th>
            <th className="py-1 font-medium">Model</th>
            <th className="py-1 text-right font-medium">Calls</th>
            <th className="py-1 text-right font-medium">Input</th>
            <th className="py-1 text-right font-medium">Output</th>
            <th className="py-1 text-right font-medium">Cost</th>
          </tr>
        </thead>
        <tbody className="tabular-nums">
          {data.by_model.map((m) => (
            <tr key={`${m.kind}-${m.provider}-${m.model}`} className="border-t border-slate-100">
              <td className="py-2 capitalize">{m.kind}</td>
              <td className="py-2">
                {m.model} <span className="text-slate-500">({m.provider})</span>
              </td>
              <td className="py-2 text-right">{number(m.requests)}</td>
              <td className="py-2 text-right">{number(m.input_tokens)}</td>
              <td className="py-2 text-right">{number(m.output_tokens)}</td>
              <td className="py-2 text-right">
                {m.cost === null ? (
                  <Link to="/settings" className="text-amber-700 underline">
                    set price
                  </Link>
                ) : (
                  money(m.cost)
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function BlockedList({ data }: { data: UsageReport }) {
  if (!data.recent_blocked.length) return <p className="text-sm text-slate-500">Nothing blocked in this period.</p>;
  return (
    <ul className="divide-y divide-slate-100">
      {data.recent_blocked.map((b, i) => (
        <li key={i} className="py-3">
          <div className="flex flex-wrap items-center gap-2 text-xs text-slate-500">
            <span className="rounded-full bg-slate-100 px-2 py-0.5 font-medium text-slate-700">
              {VERDICT_LABELS[b.verdict] ?? b.verdict}
            </span>
            <Link to={`/apps/${b.app_id}`} className="hover:underline">
              {b.app_name}
            </Link>
            <span>{new Date(b.created_at).toLocaleString()}</span>
          </div>
          <p className="mt-1 text-sm">{b.question}</p>
          {b.reason && <p className="mt-0.5 text-xs text-slate-500">{b.reason}</p>}
        </li>
      ))}
    </ul>
  );
}

function RoutingTable({ data }: { data: UsageReport["routing"] }) {
  return (
    <div className="space-y-5">
      {data.map((r) => {
        const saved = r.actual_cost !== null && r.baseline_cost !== null ? r.baseline_cost - r.actual_cost : null;
        const models = Object.entries(r.by_model).sort((a, b) => b[1] - a[1]);
        return (
          <div key={r.app_id}>
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <Link to={`/apps/${r.app_id}`} className="font-medium hover:underline">
                {r.name}
              </Link>
              <span className="text-sm text-slate-600">
                {saved === null ? (
                  "Set model prices in Settings to see savings"
                ) : (
                  <>
                    {money(r.actual_cost)} instead of {money(r.baseline_cost)} on {r.baseline_model}
                    {saved > 0 && r.baseline_cost ? (
                      <strong className="ml-1 text-emerald-700">
                        (saved {money(saved)}, {Math.round((saved / r.baseline_cost) * 100)}%)
                      </strong>
                    ) : null}
                  </>
                )}
              </span>
            </div>
            <div className="mt-2 space-y-1">
              {models.map(([model, n]) => (
                <div key={model} className="flex items-center gap-3 text-sm">
                  <span className="w-72 shrink-0 truncate text-slate-600" title={model}>
                    {model}
                  </span>
                  <div className="h-2 flex-1 overflow-hidden rounded-full bg-slate-100">
                    <div className="h-full rounded-full bg-slate-500" style={{ width: `${(n / r.answers) * 100}%` }} />
                  </div>
                  <span className="w-24 shrink-0 text-right tabular-nums text-slate-600">
                    {number(n)} ({Math.round((n / r.answers) * 100)}%)
                  </span>
                </div>
              ))}
            </div>
            <p className="mt-2 text-xs text-slate-500">
              {number(r.answers)} answers · {number(r.moved_up)} moved up · {number(r.better_requests)} better answers requested
            </p>
          </div>
        );
      })}
    </div>
  );
}
