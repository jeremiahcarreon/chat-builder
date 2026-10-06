import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Loader2,
  Play,
  Plus,
  Sparkles,
  ThumbsDown,
  ThumbsUp,
  Trash2,
  XCircle,
} from "lucide-react";
import { useState, type FormEvent } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  api,
  type Calibration as CalibrationData,
  type ChatApp,
  type ReviewedConversation,
  type ReviewSummary,
  type TranscriptMessage,
  type PromptVersionDetail,
  type PromptVersionSummary,
  type RatedAnswer,
  type TestCase,
  type TestCaseInput,
  type TestResultRow,
  type TestRunSummary,
} from "../api";
import { Button, Card, Field, Input, Select, Textarea } from "../components/ui";

const FIELD_LABELS: Record<string, string> = {
  name: "name",
  description: "description",
  personality: "personality",
  knowledge_notes: "product knowledge",
  in_scope: "in scope",
  out_of_scope: "out of scope",
  refusal_message: "refusal message",
  strict_mode: "strict mode",
  provider: "provider",
  model: "model",
  temperature: "temperature",
  seed: "seed",
  max_answer_tokens: "answer length",
  classifier_enabled: "classifier on/off",
  classifier_provider: "classifier provider",
  classifier_model: "classifier model",
  classifier_strictness: "classifier strictness",
};

function percent(part: number, whole: number): string {
  return whole ? `${Math.round((part / whole) * 100)}%` : "–";
}

export function QualityTab({ appId }: { appId: string }) {
  const base = `/api/apps/${appId}/quality`;
  return (
    <div className="space-y-6">
      <Satisfaction base={base} />
      <TestSet base={base} />
      <TestRuns base={base} />
      <Calibration appId={appId} base={base} />
      <Feedback base={base} />
      <Versions base={base} />
    </div>
  );
}

// ---------------------------------------------------------------- test set

const emptyCase: TestCaseInput = { question: "", expectation: "answer", must_include: "", reference_answer: "", notes: "" };

function TestSet({ base }: { base: string }) {
  const queryClient = useQueryClient();
  const [adding, setAdding] = useState(false);
  const { data: cases } = useQuery({ queryKey: ["tests", base], queryFn: () => api<TestCase[]>(`${base}/tests`) });
  const { data: runs } = useQuery({
    queryKey: ["runs", base],
    queryFn: () => api<TestRunSummary[]>(`${base}/runs`),
    refetchInterval: (q) => (q.state.data?.some((r) => r.status === "queued" || r.status === "running") ? 2000 : false),
  });
  const running = runs?.some((r) => r.status === "queued" || r.status === "running");
  const run = useMutation({
    mutationFn: () => api<TestRunSummary>(`${base}/runs`, { method: "POST" }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["runs", base] }),
  });
  const remove = useMutation({
    mutationFn: (id: string) => api(`${base}/tests/${id}`, { method: "DELETE" }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["tests", base] }),
  });

  return (
    <Card
      title="Test set"
      description="Questions with known good outcomes. Run them after changing the prompt or model to see whether answers got better or broke."
    >
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <Button onClick={() => run.mutate()} disabled={!cases?.length || running || run.isPending}>
          {running ? <Loader2 size={14} className="animate-spin" /> : <Play size={14} />}
          {running ? "Running..." : `Run ${cases?.length ?? 0} test${cases?.length === 1 ? "" : "s"}`}
        </Button>
        <Button variant="secondary" onClick={() => setAdding((v) => !v)}>
          <Plus size={14} /> Add test
        </Button>
        {run.error && <span className="text-sm text-red-600">{run.error.message}</span>}
      </div>
      {adding && <CaseForm base={base} initial={emptyCase} onDone={() => setAdding(false)} />}
      {!cases?.length ? (
        <p className="text-sm text-slate-500">
          No tests yet. Add one, or turn a rated answer into a test from <em>Rated answers</em> below.
        </p>
      ) : (
        <ul className="divide-y divide-slate-200">
          {cases.map((c) => (
            <CaseRow key={c.id} base={base} item={c} onDelete={() => confirm("Delete this test?") && remove.mutate(c.id)} />
          ))}
        </ul>
      )}
    </Card>
  );
}

function CaseRow({ base, item, onDelete }: { base: string; item: TestCase; onDelete: () => void }) {
  const [editing, setEditing] = useState(false);
  if (editing) return <CaseForm base={base} initial={item} id={item.id} onDone={() => setEditing(false)} />;
  const checks = [
    item.expectation === "refuse" ? "should be refused" : "should be answered",
    ...item.must_include.split("\n").filter(Boolean).map((p) => `must mention "${p.trim()}"`),
    item.reference_answer ? "matches the reference answer" : "",
  ].filter(Boolean);
  return (
    <li className="flex items-start gap-3 py-3">
      <span className="mt-0.5">
        {item.last_passed === true ? (
          <CheckCircle2 size={16} className="text-emerald-600" aria-label="Passed last run" />
        ) : item.last_passed === false ? (
          <XCircle size={16} className="text-red-600" aria-label="Failed last run" />
        ) : (
          <span className="inline-block h-4 w-4 rounded-full border border-slate-300" aria-label="Not run yet" />
        )}
      </span>
      <button onClick={() => setEditing(true)} className="min-w-0 flex-1 text-left">
        <div className="text-sm font-medium">{item.question}</div>
        <div className="text-xs text-slate-500">{checks.join(" · ")}</div>
        {item.notes && <div className="mt-0.5 text-xs text-slate-400">{item.notes}</div>}
      </button>
      <button onClick={onDelete} className="rounded p-1.5 text-slate-400 hover:bg-red-50 hover:text-red-600" aria-label="Delete test">
        <Trash2 size={14} />
      </button>
    </li>
  );
}

function CaseForm({ base, initial, id, onDone }: { base: string; initial: TestCaseInput; id?: string; onDone: () => void }) {
  const queryClient = useQueryClient();
  const [form, setForm] = useState<TestCaseInput>({
    question: initial.question,
    expectation: initial.expectation,
    must_include: initial.must_include,
    reference_answer: initial.reference_answer,
    notes: initial.notes,
  });
  const save = useMutation({
    mutationFn: () =>
      api<TestCase>(id ? `${base}/tests/${id}` : `${base}/tests`, {
        method: id ? "PUT" : "POST",
        body: JSON.stringify(form),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["tests", base] });
      onDone();
    },
  });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    save.mutate();
  };
  return (
    <form onSubmit={submit} className="my-3 space-y-3 rounded-lg border border-slate-200 bg-slate-50 p-4">
      <Field label="Question">
        <Textarea required rows={2} value={form.question} onChange={(e) => setForm({ ...form, question: e.target.value })} />
      </Field>
      <Field label="Expected outcome">
        <Select value={form.expectation} onChange={(e) => setForm({ ...form, expectation: e.target.value as TestCaseInput["expectation"] })}>
          <option value="answer">Should be answered</option>
          <option value="refuse">Should be refused (out of scope or misuse)</option>
        </Select>
      </Field>
      {form.expectation === "answer" && (
        <>
          <Field label="Must mention" hint="One phrase per line, for example a part number or a price. Not case-sensitive.">
            <Textarea rows={2} value={form.must_include} onChange={(e) => setForm({ ...form, must_include: e.target.value })} />
          </Field>
          <Field
            label="Reference answer (optional)"
            hint="A known-good answer. A judge model checks that new answers agree with it; wording can differ."
          >
            <Textarea rows={3} value={form.reference_answer} onChange={(e) => setForm({ ...form, reference_answer: e.target.value })} />
          </Field>
        </>
      )}
      <Field label="Notes">
        <Input value={form.notes} onChange={(e) => setForm({ ...form, notes: e.target.value })} />
      </Field>
      <div className="flex gap-2">
        <Button type="submit" disabled={save.isPending}>
          {id ? "Save test" : "Add test"}
        </Button>
        <Button type="button" variant="secondary" onClick={onDone}>
          Cancel
        </Button>
        {save.error && <span className="text-sm text-red-600">{save.error.message}</span>}
      </div>
    </form>
  );
}

// --------------------------------------------------------------- test runs

function TestRuns({ base }: { base: string }) {
  const { data: runs } = useQuery({ queryKey: ["runs", base], queryFn: () => api<TestRunSummary[]>(`${base}/runs`) });
  const [open, setOpen] = useState<string | null>(null);
  if (!runs?.length) return null;
  return (
    <Card title="Test runs" description="Each run is compared with the one before it. Regressions are tests that passed last time and fail now.">
      <ul className="divide-y divide-slate-200">
        {runs.map((run) => {
          const active = run.status === "queued" || run.status === "running";
          const expanded = open === run.id;
          return (
            <li key={run.id} className="py-2">
              <button onClick={() => setOpen(expanded ? null : run.id)} className="flex w-full items-center gap-3 text-left text-sm" disabled={active}>
                {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                <span className="w-36 shrink-0 whitespace-nowrap text-slate-500">{new Date(run.created_at).toLocaleString(undefined, { dateStyle: "short", timeStyle: "short" })}</span>
                <span className="w-20 shrink-0">Version {run.version_number ?? "?"}</span>
                {active ? (
                  <span className="flex items-center gap-2 text-slate-600">
                    <Loader2 size={14} className="animate-spin" /> {run.completed} of {run.total} done
                  </span>
                ) : run.status === "failed" ? (
                  <span className="text-red-600">Failed: {run.error}</span>
                ) : (
                  <>
                    <span className="w-28 font-medium tabular-nums">
                      {run.passed}/{run.total} passed
                    </span>
                    {run.regressions > 0 && (
                      <span className="rounded-full bg-red-50 px-2 py-0.5 text-xs font-medium text-red-700">
                        {run.regressions} regressed
                      </span>
                    )}
                    {run.fixed > 0 && (
                      <span className="rounded-full bg-emerald-50 px-2 py-0.5 text-xs font-medium text-emerald-700">{run.fixed} fixed</span>
                    )}
                  </>
                )}
              </button>
              {expanded && <RunResults base={base} runId={run.id} />}
            </li>
          );
        })}
      </ul>
    </Card>
  );
}

// ------------------------------------------------------------- calibration

function Calibration({ appId, base }: { appId: string; base: string }) {
  const queryClient = useQueryClient();
  const { data: app } = useQuery({ queryKey: ["app", appId], queryFn: () => api<ChatApp>(`/api/apps/${appId}`) });
  const { data } = useQuery({
    queryKey: ["calibration", base],
    queryFn: () => api<CalibrationData>(`${base}/calibration`),
    refetchInterval: (query) => (query.state.data?.id && !query.state.data.done ? 2000 : false),
  });
  const start = useMutation({
    mutationFn: () => api<CalibrationData>(`${base}/calibrate`, { method: "POST" }),
    onSuccess: (result) => queryClient.setQueryData(["calibration", base], result),
  });
  if (!app || app.route_tiers.length < 2) return null;
  const running = !!data?.id && !data.done;

  return (
    <Card
      title="Model ladder calibration"
      description="Runs the test set on each model in automatic model choice (at its lowest effort) to show which questions the cheaper models already get right."
    >
      <div className="space-y-4">
        <div className="flex flex-wrap items-center gap-3">
          <Button type="button" variant="secondary" onClick={() => start.mutate()} disabled={running || start.isPending}>
            {running ? <Loader2 size={14} className="animate-spin" /> : <Play size={14} />}
            {running ? "Calibrating..." : data?.id ? "Calibrate again" : "Calibrate"}
          </Button>
          {data?.created_at && (
            <span className="text-xs text-slate-500">Last run {new Date(data.created_at).toLocaleString()}</span>
          )}
          {start.error && <span className="text-sm text-red-600">{start.error.message}</span>}
        </div>
        {data?.id && (
          <>
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              {data.tiers.map((t) => (
                <div key={t.tier} className="rounded-md border border-slate-200 p-3">
                  <div className="truncate text-xs text-slate-500" title={t.model}>
                    {t.tier + 1}. {t.model}
                  </div>
                  <div className="mt-1 text-lg font-semibold tabular-nums">
                    {t.status === "done" ? `${t.passed}/${t.total}` : `${t.completed} of ${t.total}`}
                  </div>
                  <div className="text-xs text-slate-500">{t.status === "done" ? "passed" : t.status === "failed" ? "failed" : "answering..."}</div>
                </div>
              ))}
            </div>
            {data.suggestions.length > 0 && (
              <ul className="list-disc space-y-1 rounded-md bg-sky-50 py-2 pl-7 pr-3 text-sm text-sky-900">
                {data.suggestions.map((tip) => (
                  <li key={tip}>{tip}</li>
                ))}
              </ul>
            )}
            {data.done && data.cases.length > 0 && (
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-slate-200 text-left text-xs text-slate-500">
                    <th className="py-2 font-medium">Question</th>
                    {data.tiers.map((t) => (
                      <th key={t.tier} className="w-16 py-2 text-center font-medium" title={t.model}>
                        {t.tier + 1}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {data.cases.map((c) => (
                    <tr key={c.question} className="border-b border-slate-100 last:border-0">
                      <td className="py-2 pr-3">{c.question}</td>
                      {c.passed.map((ok, i) => (
                        <td key={i} className={`py-2 text-center ${c.cheapest === i ? "bg-emerald-50" : ""}`}>
                          {ok === null ? "–" : ok ? (
                            <CheckCircle2 size={15} className="inline text-emerald-600" aria-label="passed" />
                          ) : (
                            <XCircle size={15} className="inline text-red-500" aria-label="failed" />
                          )}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </>
        )}
      </div>
    </Card>
  );
}

const CHANGE_STYLES: Record<TestResultRow["change"], string> = {
  regressed: "bg-red-50 text-red-700",
  fixed: "bg-emerald-50 text-emerald-700",
  new: "bg-slate-100 text-slate-600",
  same: "",
};

function RunResults({ base, runId }: { base: string; runId: string }) {
  const { data } = useQuery({ queryKey: ["run", runId], queryFn: () => api<TestResultRow[]>(`${base}/runs/${runId}`) });
  if (!data) return <p className="py-2 pl-7 text-sm text-slate-500">Loading...</p>;
  return (
    <ul className="mt-2 space-y-2 pl-7">
      {data.map((r) => (
        <li key={r.case_id} className="rounded-md border border-slate-200 p-3 text-sm">
          <div className="flex items-start gap-2">
            {r.passed ? <CheckCircle2 size={16} className="mt-0.5 shrink-0 text-emerald-600" /> : <XCircle size={16} className="mt-0.5 shrink-0 text-red-600" />}
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-medium">{r.question}</span>
                {r.change !== "same" && <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${CHANGE_STYLES[r.change]}`}>{r.change}</span>}
              </div>
              {r.failures.length > 0 && <div className="mt-1 text-xs text-red-700">{r.failures.join(" · ")}</div>}
              <details className="mt-1">
                <summary className="cursor-pointer text-xs text-slate-500">
                  Answer{r.seconds != null ? ` (${r.seconds.toFixed(1)}s)` : ""}
                </summary>
                <div className="prose-chat mt-1 rounded bg-slate-50 p-2 text-sm">
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>{r.answer || "(empty)"}</ReactMarkdown>
                </div>
              </details>
            </div>
          </div>
        </li>
      ))}
    </ul>
  );
}

// ---------------------------------------------------------- rated answers

function Feedback({ base }: { base: string }) {
  const queryClient = useQueryClient();
  const [rating, setRating] = useState<"all" | "helpful" | "not_helpful">("not_helpful");
  const { data } = useQuery({
    queryKey: ["feedback", base, rating],
    queryFn: () => api<RatedAnswer[]>(`${base}/feedback?rating=${rating}`),
  });
  const toTest = useMutation({
    mutationFn: ({ id, expectation }: { id: number; expectation: "answer" | "refuse" }) =>
      api<TestCase>(`${base}/tests/from-answer/${id}`, { method: "POST", body: JSON.stringify({ expectation }) }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["feedback", base] });
      queryClient.invalidateQueries({ queryKey: ["tests", base] });
    },
  });

  return (
    <Card title="Rated answers" description="What people said about the answers. Turn them into tests so a fix stays fixed.">
      <div className="mb-3 inline-flex rounded-md border border-slate-300 bg-white p-0.5 text-sm" role="group">
        {(
          [
            ["not_helpful", "Not helpful"],
            ["helpful", "Helpful"],
            ["all", "All"],
          ] as const
        ).map(([value, label]) => (
          <button
            key={value}
            onClick={() => setRating(value)}
            aria-pressed={rating === value}
            className={`rounded px-3 py-1 ${rating === value ? "bg-slate-900 text-white" : "text-slate-600 hover:bg-slate-100"}`}
          >
            {label}
          </button>
        ))}
      </div>
      {!data?.length ? (
        <p className="text-sm text-slate-500">No rated answers here yet.</p>
      ) : (
        <ul className="divide-y divide-slate-200">
          {data.map((r) => (
            <li key={r.message_id} className="py-3 text-sm">
              <div className="flex flex-wrap items-center gap-2 text-xs text-slate-500">
                {r.better_answer_by ? (
                  <Sparkles size={13} className="text-red-600" />
                ) : r.rating === 1 ? (
                  <ThumbsUp size={13} className="text-emerald-600" />
                ) : (
                  <ThumbsDown size={13} className="text-red-600" />
                )}
                <span>Version {r.version_number ?? "?"}</span>
                {r.model && <span>{r.model}</span>}
                {r.better_answer_by && <span className="font-medium text-slate-700">Asked for a better answer → {r.better_answer_by}</span>}
                {r.rated_at && <span>{new Date(r.rated_at).toLocaleString()}</span>}
              </div>
              <div className="mt-1 font-medium">{r.question}</div>
              {r.feedback && <div className="mt-1 rounded bg-amber-50 px-2 py-1 text-amber-900">“{r.feedback}”</div>}
              <details className="mt-1">
                <summary className="cursor-pointer text-xs text-slate-500">Answer</summary>
                <div className="prose-chat mt-1 rounded bg-slate-50 p-2">
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>{r.answer}</ReactMarkdown>
                </div>
              </details>
              <div className="mt-2 flex gap-2">
                {r.test_case_id ? (
                  <span className="text-xs text-emerald-700">In the test set</span>
                ) : r.rating === 1 ? (
                  <Button variant="secondary" className="px-2 py-1 text-xs" onClick={() => toTest.mutate({ id: r.message_id, expectation: "answer" })}>
                    Add as a test (this answer is the reference)
                  </Button>
                ) : (
                  <>
                    <Button variant="secondary" className="px-2 py-1 text-xs" onClick={() => toTest.mutate({ id: r.message_id, expectation: "answer" })}>
                      Add as a test: should be answered
                    </Button>
                    <Button variant="secondary" className="px-2 py-1 text-xs" onClick={() => toTest.mutate({ id: r.message_id, expectation: "refuse" })}>
                      Add as a test: should be refused
                    </Button>
                  </>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

// ---------------------------------------------------------------- versions

function Versions({ base }: { base: string }) {
  const { data } = useQuery({ queryKey: ["versions", base], queryFn: () => api<PromptVersionSummary[]>(`${base}/versions`) });
  const [open, setOpen] = useState<number | null>(null);
  return (
    <Card
      title="Prompt versions"
      description="A new version is recorded whenever settings that shape answers change. Every answer and test run is tied to the version that produced it."
    >
      {!data?.length ? (
        <p className="text-sm text-slate-500">No versions yet.</p>
      ) : (
        <table className="w-full text-sm">
          <thead className="text-left text-slate-500">
            <tr>
              <th className="py-1 font-medium">Version</th>
              <th className="py-1 font-medium">What changed</th>
              <th className="py-1 text-right font-medium">Answers</th>
              <th className="py-1 text-right font-medium">Helpful</th>
              <th className="py-1 text-right font-medium">Satisfaction</th>
              <th className="py-1 text-right font-medium">Tests</th>
            </tr>
          </thead>
          <tbody>
            {data.map((v) => {
              const rated = v.helpful + v.not_helpful;
              return [
                <tr key={v.id} className="cursor-pointer border-t border-slate-100 hover:bg-slate-50" onClick={() => setOpen(open === v.id ? null : v.id)}>
                  <td className="py-2">
                    <div className="font-medium">v{v.number}</div>
                    <div className="text-xs text-slate-500">{new Date(v.created_at).toLocaleDateString()}</div>
                  </td>
                  <td className="py-2 text-slate-600">
                    {v.number === 1 ? "First version" : v.changed.map((c) => FIELD_LABELS[c] ?? c).join(", ") || "–"}
                    {v.note && <div className="text-xs text-slate-500">{v.note}</div>}
                  </td>
                  <td className="py-2 text-right tabular-nums">{v.answers}</td>
                  <td className="py-2 text-right tabular-nums" title={`${v.helpful} helpful, ${v.not_helpful} not helpful`}>
                    {rated ? `${percent(v.helpful, rated)} of ${rated}` : "–"}
                  </td>
                  <td className="py-2 text-right tabular-nums" title={`Average over ${v.reviewed} reviewed conversations`}>
                    {v.satisfaction != null ? `${Math.round(v.satisfaction)} / 100` : "–"}
                  </td>
                  <td className="py-2 text-right tabular-nums">{v.test_total != null ? `${v.test_passed}/${v.test_total}` : "–"}</td>
                </tr>,
                open === v.id && (
                  <tr key={`${v.id}-detail`}>
                    <td colSpan={6} className="pb-3">
                      <VersionDetail base={base} id={v.id} />
                    </td>
                  </tr>
                ),
              ];
            })}
          </tbody>
        </table>
      )}
    </Card>
  );
}

function VersionDetail({ base, id }: { base: string; id: number }) {
  const queryClient = useQueryClient();
  const { data } = useQuery({ queryKey: ["version", id], queryFn: () => api<PromptVersionDetail>(`${base}/versions/${id}`) });
  const [note, setNote] = useState<string | null>(null);
  const save = useMutation({
    mutationFn: (value: string) => api(`${base}/versions/${id}`, { method: "PATCH", body: JSON.stringify({ note: value }) }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["versions", base] });
      queryClient.invalidateQueries({ queryKey: ["version", id] });
    },
  });
  if (!data) return <p className="text-sm text-slate-500">Loading...</p>;
  return (
    <div className="space-y-3 rounded-lg border border-slate-200 bg-slate-50 p-4">
      <form
        className="flex gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          save.mutate(note ?? data.note);
        }}
      >
        <Input placeholder="Label this version, e.g. 'tightened scope'" value={note ?? data.note} onChange={(e) => setNote(e.target.value)} />
        <Button type="submit" variant="secondary" disabled={save.isPending}>
          Save label
        </Button>
      </form>
      <details>
        <summary className="cursor-pointer text-sm font-medium">System prompt</summary>
        <pre className="mt-2 max-h-80 overflow-auto whitespace-pre-wrap rounded bg-white p-3 text-xs">{data.system_prompt}</pre>
      </details>
      <details>
        <summary className="cursor-pointer text-sm font-medium">Settings</summary>
        <dl className="mt-2 grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1 text-xs">
          {Object.entries(data.settings).map(([key, value]) => (
            <div key={key} className="contents">
              <dt className="text-slate-500">{FIELD_LABELS[key] ?? key}</dt>
              <dd className="whitespace-pre-wrap break-words">{value === null || value === "" ? "–" : String(value)}</dd>
            </div>
          ))}
        </dl>
      </details>
    </div>
  );
}

// ------------------------------------------------------------ satisfaction

function scoreStyle(score: number): string {
  if (score < 35) return "bg-red-50 text-red-700";
  if (score < 65) return "bg-amber-50 text-amber-800";
  return "bg-emerald-50 text-emerald-700";
}

function Satisfaction({ base }: { base: string }) {
  const queryClient = useQueryClient();
  const [order, setOrder] = useState<"lowest" | "highest" | "recent">("lowest");
  const [open, setOpen] = useState<string | null>(null);
  const { data: summary } = useQuery({
    queryKey: ["reviews", base],
    queryFn: () => api<ReviewSummary>(`${base}/reviews`),
    refetchInterval: (q) => (q.state.data && reviewing ? 3000 : false),
  });
  const { data: conversations } = useQuery({
    queryKey: ["reviewed", base, order],
    queryFn: () => api<ReviewedConversation[]>(`${base}/reviews/conversations?order=${order}`),
    enabled: !!summary?.reviewed,
  });
  const [reviewing, setReviewing] = useState(false);
  const run = useMutation({
    mutationFn: () => api(`${base}/reviews/run`, { method: "POST" }),
    onSuccess: () => {
      setReviewing(true);
      // Reviews take well under a second each; refresh shortly after.
      setTimeout(() => {
        setReviewing(false);
        queryClient.invalidateQueries({ queryKey: ["reviews", base] });
        queryClient.invalidateQueries({ queryKey: ["reviewed", base] });
        queryClient.invalidateQueries({ queryKey: ["versions", base] });
      }, 6000);
    },
  });

  if (summary && !summary.available) {
    return (
      <Card title="Conversation satisfaction">
        <p className="text-sm text-slate-500">
          Jev, TypeSafe's System One model, can score how satisfied people seemed in each conversation. Add a TypeSafe
          key in Settings to turn this on.
        </p>
      </Card>
    );
  }

  return (
    <Card
      title="Conversation satisfaction"
      description="Jev reviews each conversation after 30 quiet minutes and scores how satisfied the person seemed (0 to 100) and whether they got what they needed. Refused misuse attempts aren't scored."
    >
      <div className="mb-4 flex flex-wrap items-end gap-6">
        <div>
          <div className="text-sm text-slate-500">Average</div>
          <div className="text-3xl font-semibold tabular-nums">
            {summary?.average != null ? Math.round(summary.average) : "–"}
            <span className="text-base font-normal text-slate-500"> / 100</span>
          </div>
        </div>
        <div>
          <div className="text-sm text-slate-500">Needs met</div>
          <div className="text-3xl font-semibold tabular-nums">
            {summary?.resolved_rate != null ? `${Math.round(summary.resolved_rate * 100)}%` : "–"}
          </div>
        </div>
        <div className="text-sm text-slate-500">
          {summary?.reviewed ?? 0} reviewed · {summary?.waiting ?? 0} not yet reviewed
        </div>
        <Button variant="secondary" className="ml-auto" onClick={() => run.mutate()} disabled={run.isPending || reviewing}>
          {reviewing ? <Loader2 size={14} className="animate-spin" /> : null}
          {reviewing ? "Reviewing..." : "Review now"}
        </Button>
      </div>
      {run.error && <p className="mb-3 text-sm text-red-600">{run.error.message}</p>}

      {!!conversations?.length && (
        <>
          <div className="mb-2 inline-flex rounded-md border border-slate-300 bg-white p-0.5 text-sm" role="group">
            {(
              [
                ["lowest", "Least satisfied"],
                ["highest", "Most satisfied"],
                ["recent", "Recently reviewed"],
              ] as const
            ).map(([value, label]) => (
              <button
                key={value}
                onClick={() => setOrder(value)}
                aria-pressed={order === value}
                className={`rounded px-3 py-1 ${order === value ? "bg-slate-900 text-white" : "text-slate-600 hover:bg-slate-100"}`}
              >
                {label}
              </button>
            ))}
          </div>
          <ul className="divide-y divide-slate-200">
            {conversations.map((c) => (
              <li key={c.id} className="py-2">
                <button onClick={() => setOpen(open === c.id ? null : c.id)} className="flex w-full items-center gap-3 text-left text-sm">
                  {open === c.id ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                  <span className={`w-16 shrink-0 rounded-full px-2 py-0.5 text-center text-xs font-medium tabular-nums ${scoreStyle(c.satisfaction)}`}>
                    {Math.round(c.satisfaction)}
                  </span>
                  <span className="min-w-0 flex-1 truncate">{c.title || "Untitled"}</span>
                  <span className="shrink-0 text-xs text-slate-500">
                    {c.resolved != null && `${c.resolved >= 0.5 ? "needs met" : "needs not met"} · `}
                    {c.messages} messages{c.version_number ? ` · v${c.version_number}` : ""}
                  </span>
                </button>
                {open === c.id && <Transcript base={base} id={c.id} />}
              </li>
            ))}
          </ul>
        </>
      )}
    </Card>
  );
}

function Transcript({ base, id }: { base: string; id: string }) {
  const { data } = useQuery({ queryKey: ["transcript", id], queryFn: () => api<TranscriptMessage[]>(`${base}/reviews/conversations/${id}`) });
  if (!data) return <p className="py-2 pl-7 text-sm text-slate-500">Loading...</p>;
  return (
    <div className="mt-2 space-y-2 rounded-lg border border-slate-200 bg-slate-50 p-3 pl-7 text-sm">
      {data.map((m, i) => (
        <div key={i}>
          <div className="text-xs font-medium text-slate-500">{m.role === "user" ? "Person" : m.blocked ? "Assistant (refused)" : "Assistant"}</div>
          <div className="prose-chat">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{m.content}</ReactMarkdown>
          </div>
          {m.rating != null && (
            <div className="mt-0.5 text-xs text-slate-500">
              Rated {m.rating === 1 ? "helpful" : "not helpful"}
              {m.feedback ? `: “${m.feedback}”` : ""}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}
