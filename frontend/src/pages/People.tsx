import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Copy, UserPlus } from "lucide-react";
import { useState, type FormEvent } from "react";
import { api, type ChatApp, type InviteLink, type Person } from "../api";
import { useCurrentUser } from "../auth";
import { Button, Card, Field, Input, Toggle } from "../components/ui";

function lastSeen(iso: string | null): string {
  if (!iso) return "never";
  const minutes = (Date.now() - new Date(iso).getTime()) / 60_000;
  if (minutes < 2) return "just now";
  if (minutes < 60) return `${Math.round(minutes)} min ago`;
  if (minutes < 48 * 60) return `${Math.round(minutes / 60)} h ago`;
  return new Date(iso).toLocaleDateString();
}

export function People() {
  const queryClient = useQueryClient();
  const { data: me } = useCurrentUser();
  const { data: people } = useQuery({ queryKey: ["people"], queryFn: () => api<Person[]>("/api/users") });
  const { data: apps } = useQuery({ queryKey: ["apps"], queryFn: () => api<ChatApp[]>("/api/apps") });
  const [inviting, setInviting] = useState(false);
  const [link, setLink] = useState<InviteLink | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["people"] });

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-6">
        <div>
          <h1 className="text-2xl font-semibold">People</h1>
          <p className="mt-1 text-sm text-slate-500">
            Admins manage apps and settings. Members can only chat with the apps you give them. People signed in by an embedding
            site are added automatically.
          </p>
        </div>
        <Button className="shrink-0" onClick={() => { setInviting(true); setLink(null); }}>
          <UserPlus size={16} /> Invite
        </Button>
      </div>

      {link && <LinkNotice link={link} onClose={() => setLink(null)} />}
      {inviting && (
        <InviteForm
          apps={apps ?? []}
          onDone={(created) => {
            setInviting(false);
            if (created) {
              setLink(created);
              refresh();
            }
          }}
        />
      )}

      <Card title={`${people?.length ?? 0} people`}>
        <ul className="divide-y divide-slate-200">
          {people?.map((p) => (
            <li key={p.id} className="py-3">
              <button onClick={() => setEditing(editing === p.id ? null : p.id)} className="flex w-full items-center gap-3 text-left text-sm">
                <div className="min-w-0 flex-1">
                  <div className="font-medium">
                    {p.name || p.email}
                    {me?.email === p.email && <span className="ml-2 text-xs text-slate-500">(you)</span>}
                  </div>
                  <div className="truncate text-xs text-slate-500">
                    {p.name ? p.email : ""}
                    {p.username ? `${p.name ? " · " : ""}username ${p.username}` : ""}
                  </div>
                </div>
                <div className="flex shrink-0 items-center gap-2 text-xs">
                  {p.blocked && <span className="rounded-full bg-red-50 px-2 py-0.5 font-medium text-red-700">Blocked</span>}
                  {p.invite_pending && !p.has_password && (
                    <span className="rounded-full bg-amber-50 px-2 py-0.5 font-medium text-amber-800">Invite pending</span>
                  )}
                  {p.source === "embed" && <span className="rounded-full bg-sky-50 px-2 py-0.5 font-medium text-sky-700">Embedding site</span>}
                  <span className="rounded-full bg-slate-100 px-2 py-0.5 font-medium text-slate-700">{p.is_admin ? "Admin" : "Member"}</span>
                  <span className="w-24 text-right text-slate-500">{lastSeen(p.last_seen_at)}</span>
                </div>
              </button>
              {editing === p.id && (
                <EditPerson
                  person={p}
                  apps={apps ?? []}
                  isSelf={me?.email === p.email}
                  onLink={setLink}
                  onDone={() => {
                    setEditing(null);
                    refresh();
                  }}
                />
              )}
            </li>
          ))}
        </ul>
      </Card>
    </div>
  );
}

function LinkNotice({ link, onClose }: { link: InviteLink; onClose: () => void }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="rounded-lg border border-emerald-200 bg-emerald-50 p-4 text-sm">
      <p className="font-medium text-emerald-900">
        Send this link to {link.user.name || link.user.email}. It works once and expires on{" "}
        {new Date(link.expires_at).toLocaleDateString()}.
      </p>
      <div className="mt-2 flex gap-2">
        <Input readOnly value={link.link} onFocus={(e) => e.target.select()} />
        <Button
          variant="secondary"
          onClick={() => {
            navigator.clipboard?.writeText(link.link);
            setCopied(true);
          }}
        >
          {copied ? <Check size={14} /> : <Copy size={14} />} {copied ? "Copied" : "Copy"}
        </Button>
        <Button variant="secondary" onClick={onClose}>
          Done
        </Button>
      </div>
    </div>
  );
}

function AppChecklist({ apps, selected, onChange }: { apps: ChatApp[]; selected: string[]; onChange: (ids: string[]) => void }) {
  if (!apps.length) return <p className="text-sm text-slate-500">No chat apps yet.</p>;
  return (
    <div className="grid gap-2 sm:grid-cols-2">
      {apps.map((app) => (
        <label key={app.id} className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            className="h-4 w-4 accent-slate-900"
            checked={selected.includes(app.id)}
            onChange={(e) => onChange(e.target.checked ? [...selected, app.id] : selected.filter((id) => id !== app.id))}
          />
          {app.name}
        </label>
      ))}
    </div>
  );
}

function InviteForm({ apps, onDone }: { apps: ChatApp[]; onDone: (link: InviteLink | null) => void }) {
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [isAdmin, setIsAdmin] = useState(false);
  const [appIds, setAppIds] = useState<string[]>([]);
  const invite = useMutation({
    mutationFn: () =>
      api<InviteLink>("/api/users", { method: "POST", body: JSON.stringify({ email, name, is_admin: isAdmin, app_ids: appIds }) }),
    onSuccess: onDone,
  });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    invite.mutate();
  };
  return (
    <Card title="Invite someone" description="You'll get a link to send them; they choose their own password.">
      <form onSubmit={submit} className="space-y-4">
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Email">
            <Input type="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
          </Field>
          <Field label="Name">
            <Input value={name} onChange={(e) => setName(e.target.value)} />
          </Field>
        </div>
        <Toggle checked={isAdmin} onChange={setIsAdmin} label="Admin" hint="Can manage every app, API keys, settings and people." />
        {!isAdmin && (
          <fieldset className="space-y-1 text-sm">
            <legend className="mb-1 font-medium">Chat apps they can use</legend>
            <AppChecklist apps={apps} selected={appIds} onChange={setAppIds} />
          </fieldset>
        )}
        <div className="flex gap-2">
          <Button type="submit" disabled={invite.isPending}>
            Create invite link
          </Button>
          <Button type="button" variant="secondary" onClick={() => onDone(null)}>
            Cancel
          </Button>
          {invite.error && <span className="text-sm text-red-600">{invite.error.message}</span>}
        </div>
      </form>
    </Card>
  );
}

function EditPerson({
  person,
  apps,
  isSelf,
  onLink,
  onDone,
}: {
  person: Person;
  apps: ChatApp[];
  isSelf: boolean;
  onLink: (link: InviteLink) => void;
  onDone: () => void;
}) {
  const [name, setName] = useState(person.name);
  const [isAdmin, setIsAdmin] = useState(person.is_admin);
  const [blocked, setBlocked] = useState(person.blocked);
  const [appIds, setAppIds] = useState(person.app_ids);
  const save = useMutation({
    mutationFn: () =>
      api(`/api/users/${person.id}`, { method: "PUT", body: JSON.stringify({ name, is_admin: isAdmin, blocked, app_ids: appIds }) }),
    onSuccess: onDone,
  });
  const reset = useMutation({ mutationFn: () => api<InviteLink>(`/api/users/${person.id}/reset`, { method: "POST" }), onSuccess: onLink });
  const remove = useMutation({ mutationFn: () => api(`/api/users/${person.id}`, { method: "DELETE" }), onSuccess: onDone });

  return (
    <div className="mt-3 space-y-4 rounded-lg border border-slate-200 bg-slate-50 p-4">
      <Field label="Name">
        <Input value={name} onChange={(e) => setName(e.target.value)} />
      </Field>
      <div className="flex flex-wrap gap-6">
        <Toggle checked={isAdmin} onChange={setIsAdmin} label="Admin" />
        {!isSelf && <Toggle checked={blocked} onChange={setBlocked} label="Blocked" hint="Signs them out and stops all access." />}
      </div>
      {!isAdmin && (
        <fieldset className="space-y-1 text-sm">
          <legend className="mb-1 font-medium">Chat apps they can use</legend>
          <AppChecklist apps={apps} selected={appIds} onChange={setAppIds} />
        </fieldset>
      )}
      <div className="flex flex-wrap gap-2">
        <Button onClick={() => save.mutate()} disabled={save.isPending}>
          Save
        </Button>
        {person.source === "local" && (
          <Button variant="secondary" onClick={() => reset.mutate()} disabled={reset.isPending}>
            {person.has_password ? "Password reset link" : "New invite link"}
          </Button>
        )}
        {!isSelf && (
          <Button variant="danger" className="ml-auto" onClick={() => confirm(`Remove ${person.email}? Their conversations are kept.`) && remove.mutate()}>
            Remove
          </Button>
        )}
      </div>
      {(save.error || reset.error || remove.error) && (
        <p className="text-sm text-red-600">{(save.error || reset.error || remove.error)?.message}</p>
      )}
    </div>
  );
}
