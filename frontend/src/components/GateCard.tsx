import { useEffect, useState } from "react";
import type { GateSettings } from "../api";
import { Card, Field, Input, Textarea, Toggle } from "./ui";

const parse = (text: string) => text.split("\n").map((l) => l.trim()).filter(Boolean);

/** Settings for the review gate: hold only the answers that need a person. */
export function GateCard({
  gate,
  reviewing,
  onChange,
}: {
  gate: GateSettings;
  reviewing: boolean;
  onChange: (gate: GateSettings) => void;
}) {
  const set = <K extends keyof GateSettings>(key: K, value: GateSettings[K]) => onChange({ ...gate, [key]: value });
  // Kept as typed so a half-written line isn't trimmed away; parsed on every change.
  const [intents, setIntents] = useState(gate.intents.join("\n"));
  const joined = gate.intents.join("\n");
  useEffect(() => {
    // Reloaded from the server (or reset): show its list, unless it's what's typed already.
    setIntents((typed) => (parse(typed).join("\n") === joined ? typed : joined));
  }, [joined]);

  return (
    <Card
      title="Review gate"
      description="Hold only the answers that need a person, instead of all of them or none. Applies to API calls and, when “Review answers to anonymous visitors” is on, to those visitors. Signed-in people always get answers straight away."
    >
      <div className="space-y-4">
        <Toggle
          checked={gate.enabled}
          onChange={(v) => set("enabled", v)}
          label="Hold only answers that need a person"
          hint="An answer is held when a tool behind it changed data that can't be undone, a lookup failed, it says something the lookups and documents don't support (after one rewrite), or its kind of question hasn't yet earned the right to go out on its own."
        />
        {gate.enabled && (
          <>
            {!reviewing && (
              <p className="rounded-md bg-amber-50 px-3 py-2 text-xs text-amber-900">
                Anonymous visitors aren’t reviewed, so the gate applies to API calls only. Their verdict comes back in
                the answer’s “review” field for your system to act on.
              </p>
            )}
            <Field
              label="Kinds of question"
              hint="One per line, like “fitment” or “order status”. Each kind earns its own record. Anything else counts as “general”."
            >
              <Textarea
                rows={4}
                value={intents}
                placeholder={"fitment\norder status\nreturns\npricing"}
                onChange={(e) => {
                  setIntents(e.target.value);
                  onChange({ ...gate, intents: parse(e.target.value) });
                }}
              />
            </Field>
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
              <Field label="Sent unchanged (%)" hint="Share of held answers sent as written that earns “auto”.">
                <Input
                  type="number"
                  min={50}
                  max={100}
                  value={Math.round(gate.threshold * 100)}
                  onChange={(e) => set("threshold", Math.min(100, Math.max(50, Number(e.target.value) || 95)) / 100)}
                />
              </Field>
              <Field label="Reviewed at least" hint="Decisions needed before that share counts.">
                <Input
                  type="number"
                  min={5}
                  max={1000}
                  value={gate.min_samples}
                  onChange={(e) => set("min_samples", Math.min(1000, Math.max(5, Number(e.target.value) || 50)))}
                />
              </Field>
              <Field label="Minor edit (% of words)" hint="Edits this small (or short cuts) count as sent unchanged.">
                <Input
                  type="number"
                  min={0}
                  max={50}
                  value={Math.round(gate.minor_edit * 100)}
                  onChange={(e) => set("minor_edit", Math.min(50, Math.max(0, Number(e.target.value) || 0)) / 100)}
                />
              </Field>
              <Field label="Spot checks (%)" hint="Share of “auto” answers still held, so the record stays current.">
                <Input
                  type="number"
                  min={0}
                  max={100}
                  value={Math.round(gate.audit_rate * 100)}
                  onChange={(e) => set("audit_rate", Math.min(100, Math.max(0, Number(e.target.value) || 0)) / 100)}
                />
              </Field>
            </div>
          </>
        )}
      </div>
    </Card>
  );
}
