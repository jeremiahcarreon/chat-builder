import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Copy, ExternalLink, RefreshCw } from "lucide-react";
import { useEffect, useState } from "react";
import { api, type EmbedInfo, type EmbedSettings } from "../api";
import { Button, Card, Field, Textarea, Toggle } from "../components/ui";

const MODES: { id: EmbedSettings["auth"]; title: string; detail: string }[] = [
  {
    id: "signed",
    title: "Signed in by your site",
    detail: "Your site's server signs a short token naming the person (username and email). No second login; people are added to Chat Builder automatically.",
  },
  {
    id: "login",
    title: "Chat Builder sign-in",
    detail: "People sign in inside the widget with accounts you create on the People page.",
  },
  {
    id: "public",
    title: "Public",
    detail: "Anyone on the allowed sites can chat anonymously. Don't use this for internal data.",
  },
];

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <Button
      variant="secondary"
      className="shrink-0"
      onClick={() => {
        navigator.clipboard?.writeText(text);
        setCopied(true);
        window.setTimeout(() => setCopied(false), 1500);
      }}
    >
      {copied ? <Check size={14} /> : <Copy size={14} />} {copied ? "Copied" : "Copy"}
    </Button>
  );
}

function Code({ children, wrap = false }: { children: string; wrap?: boolean }) {
  return (
    <div className="relative">
      <pre
        className={`rounded-md bg-slate-900 p-3 pr-24 text-xs leading-relaxed text-slate-100 ${wrap ? "whitespace-pre-wrap break-all" : "overflow-x-auto"}`}
      >
        {children}
      </pre>
      <div className="absolute right-2 top-2">
        <CopyButton text={children} />
      </div>
    </div>
  );
}

export function DeployTab({ appId }: { appId: string }) {
  const queryClient = useQueryClient();
  const { data } = useQuery({ queryKey: ["embed", appId], queryFn: () => api<EmbedInfo>(`/api/apps/${appId}/embed`) });
  const [form, setForm] = useState<EmbedSettings | null>(null);
  const [domains, setDomains] = useState("");
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    if (data) {
      setForm({ enabled: data.enabled, auth: data.auth, allowed_domains: data.allowed_domains, color: data.color });
      setDomains(data.allowed_domains.join("\n"));
    }
  }, [data]);

  const save = useMutation({
    mutationFn: () =>
      api<EmbedInfo>(`/api/apps/${appId}/embed`, {
        method: "PUT",
        body: JSON.stringify({ ...form, allowed_domains: domains.split(/\s+/).filter(Boolean) }),
      }),
    onSuccess: (next) => {
      queryClient.setQueryData(["embed", appId], next);
      setSaved(true);
    },
  });

  if (!data || !form) return <p className="text-sm text-slate-500">Loading...</p>;
  const set = <K extends keyof EmbedSettings>(key: K, value: EmbedSettings[K]) => {
    setSaved(false);
    setForm({ ...form, [key]: value });
  };

  return (
    <div className="space-y-6">
      <Card title="Chat widget" description="Add this chat to any website as a button in the bottom-right corner that opens a chat window.">
        <div className="space-y-5">
          <Toggle checked={form.enabled} onChange={(v) => set("enabled", v)} label="Allow this app to be embedded" />

          <fieldset className="space-y-2">
            <legend className="mb-1 text-sm font-medium">How people sign in</legend>
            {MODES.map((mode) => (
              <label
                key={mode.id}
                className={`flex cursor-pointer gap-3 rounded-lg border p-3 text-sm ${form.auth === mode.id ? "border-slate-900 bg-slate-50" : "border-slate-200"}`}
              >
                <input type="radio" name="auth" className="mt-0.5 accent-slate-900" checked={form.auth === mode.id} onChange={() => set("auth", mode.id)} />
                <span>
                  <span className="font-medium">{mode.title}</span>
                  <span className="block text-slate-500">{mode.detail}</span>
                </span>
              </label>
            ))}
          </fieldset>

          <Field label="Allowed sites" hint="One address per line, like https://erp.example.com. Use https://*.example.com for all subdomains. The widget won't open anywhere else.">
            <Textarea
              rows={3}
              placeholder="https://erp.example.com"
              value={domains}
              onChange={(e) => {
                setSaved(false);
                setDomains(e.target.value);
              }}
            />
          </Field>

          <label className="flex items-center gap-3 text-sm">
            <input type="color" value={form.color} onChange={(e) => set("color", e.target.value)} className="h-9 w-12 cursor-pointer rounded border border-slate-300" />
            <span className="font-medium">Button and header color</span>
          </label>

          <div className="flex items-center gap-3">
            <Button onClick={() => save.mutate()} disabled={save.isPending}>
              {save.isPending ? "Saving..." : "Save"}
            </Button>
            {saved && <span className="text-sm text-emerald-700">Saved</span>}
            {save.error && <span className="text-sm text-red-600">{save.error.message}</span>}
          </div>
        </div>
      </Card>

      {data.enabled && (
        <>
          <Card title="Add it to your site" description="Paste this before the closing </body> tag on the pages where the chat should appear.">
            <div className="space-y-3">
              {!data.public_url_configured && (
                <p className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900">
                  This address comes from how you opened Chat Builder. Set <code>PUBLIC_URL</code> in Chat Builder's .env to its
                  public https address (for example https://chat.example.com) so snippets and links always use it.
                </p>
              )}
              <Code wrap>{data.auth === "signed" ? data.snippet.replace("SIGNED_TOKEN", "{{ $chatToken }}") : data.snippet}</Code>
              <a
                href={`/embed/preview/${appId}`}
                target="_blank"
                rel="noreferrer"
                className="inline-flex items-center gap-1.5 text-sm text-slate-700 underline"
              >
                Try it on a preview page <ExternalLink size={13} />
              </a>
            </div>
          </Card>
          {data.auth === "signed" && <SignedSetup appId={appId} hasSecret={data.has_secret} />}
        </>
      )}
      {data && <ApiAccess appId={appId} base={data.script_url.split("/embed/")[0]} />}
    </div>
  );
}

interface ApiKey {
  id: string;
  name: string;
  prefix: string;
  created_at: string;
  last_used_at: string | null;
  key?: string;
}

/** API keys for other systems (like the ERP), with examples of calling the app. */
function ApiAccess({ appId, base }: { appId: string; base: string }) {
  const queryClient = useQueryClient();
  const [name, setName] = useState("");
  const [created, setCreated] = useState<ApiKey | null>(null);
  const { data: keys } = useQuery({ queryKey: ["api-keys", appId], queryFn: () => api<ApiKey[]>(`/api/apps/${appId}/api-keys`) });
  const create = useMutation({
    mutationFn: () => api<ApiKey>(`/api/apps/${appId}/api-keys`, { method: "POST", body: JSON.stringify({ name }) }),
    onSuccess: (key) => {
      setCreated(key);
      setName("");
      queryClient.invalidateQueries({ queryKey: ["api-keys", appId] });
    },
  });
  const remove = useMutation({
    mutationFn: (id: string) => api(`/api/apps/${appId}/api-keys/${id}`, { method: "DELETE" }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["api-keys", appId] }),
  });

  const answerUrl = `${base}/api/v1/apps/${appId}/answer`;
  const curl = `curl -X POST ${answerUrl} \\
  -H "Authorization: Bearer $CHAT_BUILDER_KEY" \\
  -H "Content-Type: application/json" \\
  -d '{"message": "Where is my order?", "thread_id": "ebay-12345", "context": {"ebay_buyer": "sample_buyer"}}'`;
  const response = `{
  "answer": "Yes. EC797 (2 row) has a 1.5 inch core...",
  "blocked": false,
  "reason": null,
  "message_id": 812,
  "conversation_id": "…",
  "thread_id": "ebay-12345",
  "model": "anthropic/claude-haiku-5-5",
  "effort": "low",
  "lookups": ["Querying Catalog"]
}`;
  const laravel = `// app/Jobs/DraftEbayReply.php: queued whenever an eBay message arrives
$response = Http::withToken(config('services.chat_builder.key'))
    ->timeout(120)                      // answers that query data can take a while
    ->retry(2, 5000, throw: false)
    ->post('${answerUrl}', [
        'message'   => $message->body,
        'thread_id' => 'ebay-' . $message->thread_id,   // follow-ups share context
        'context'   => ['ebay_buyer' => $message->buyer_username],  // who is asking
    ]);

if ($response->successful() && ! $response->json('blocked')) {
    $message->update([
        'draft_reply'      => $response->json('answer'),
        'draft_message_id' => $response->json('message_id'),
    ]);
}

// When staff send the reply, report what they did with the draft:
Http::withToken(config('services.chat_builder.key'))
    ->post('${base}/api/v1/apps/${appId}/messages/' . $message->draft_message_id . '/outcome', [
        'outcome'    => $sentText === $message->draft_reply ? 'sent' : 'edited',  // or 'discarded'
        'final_text' => $sentText,
    ]);`;

  const teach = `// When staff wrote the reply themselves (no draft, or they threw the draft away), let the app learn from it:
Http::withToken(config('services.chat_builder.key'))
    ->post('${base}/api/v1/apps/${appId}/lessons', [
        'customer_message' => $message->body,
        'reply'            => $sentText,
        'draft'            => $message->draft_reply,   // optional: what the app had suggested
        'context'          => ['ebay_buyer' => $message->buyer_username],
    ]);`;

  return (
    <Card
      title="API"
      description="Let another system ask this app questions, like your ERP drafting replies to eBay messages for staff to check and send. Answers use the same data, guardrails and model choice as the chat."
    >
      <div className="space-y-5 text-sm">
        <div className="space-y-2">
          <div className="font-medium">API keys</div>
          {keys && keys.length > 0 ? (
            <ul className="divide-y divide-slate-200 rounded-md border border-slate-200">
              {keys.map((k) => (
                <li key={k.id} className="flex items-center gap-3 px-3 py-2">
                  <span className="font-medium">{k.name}</span>
                  <code className="text-xs text-slate-500">{k.prefix}…</code>
                  <span className="ml-auto text-xs text-slate-500">
                    {k.last_used_at ? `Last used ${new Date(k.last_used_at).toLocaleString()}` : "Never used"}
                  </span>
                  <button
                    onClick={() => confirm(`Delete the key "${k.name}"? Anything using it stops working immediately.`) && remove.mutate(k.id)}
                    className="text-xs text-red-700 hover:underline"
                  >
                    Delete
                  </button>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-slate-500">No keys yet.</p>
          )}
          {created?.key && (
            <div className="space-y-1 rounded-md border border-amber-200 bg-amber-50 p-3">
              <p className="text-amber-900">
                Copy this key now and keep it on your server. It won't be shown again.
              </p>
              <div className="flex gap-2">
                <code className="flex-1 overflow-x-auto rounded-md border border-slate-300 bg-white px-3 py-2">{created.key}</code>
                <CopyButton text={created.key} />
              </div>
            </div>
          )}
          <form
            className="flex gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              if (name.trim()) create.mutate();
            }}
          >
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              maxLength={200}
              placeholder="Key name, like ERP eBay drafts"
              className="flex-1 rounded-md border border-slate-300 px-3 py-2 text-sm outline-none focus:border-slate-500"
            />
            <Button type="submit" variant="secondary" disabled={!name.trim() || create.isPending}>
              Create key
            </Button>
          </form>
          {create.error && <p className="text-red-600">{create.error.message}</p>}
        </div>

        <div className="space-y-2">
          <div className="font-medium">Ask a question</div>
          <p className="text-slate-500">
            Send the same <code>thread_id</code> for follow-up messages in one conversation, so the answer has context.
            Messages the guardrails refuse come back with <code>"blocked": true</code> and a <code>reason</code>:{" "}
            <code>off_topic</code>, <code>abuse</code>, <code>rate_limited</code>, or <code>no_reply</code> when the classifier
            checks whether a reply is expected and the sender isn't waiting for one. The optional <code>context</code>{" "}
            says who is asking: the model sees it, and it's sent to the app's MCP servers (in the request's{" "}
            <code>_meta</code> as <code>chat-builder/context</code>) so they can return only that person's records.
          </p>
          <Code>{curl}</Code>
          <Code>{response}</Code>
        </div>

        <div className="space-y-2">
          <div className="font-medium">Laravel background job</div>
          <Code>{laravel}</Code>
          <p className="text-slate-500">
            Reporting the outcome is optional but worth it: the Quality tab then shows how often drafts go out unchanged,
            and an edited reply becomes the reference answer when you turn it into a test. Each key counts toward the app's
            messages-per-minute limit on its own; raise that limit in Settings if the ERP sends bursts.
          </p>
        </div>

        <div className="space-y-2">
          <div className="font-medium">Learn from what staff send</div>
          <p className="text-slate-500">
            An <code>edited</code> outcome with <code>final_text</code> teaches the app automatically. For replies staff wrote
            without a draft, send the exchange to <code>/lessons</code>. Either way a small model turns it into a short lesson
            with names, usernames, order and tracking numbers, stores, emails and phone numbers removed, kept under the app's
            "Learned from sent replies" data source. Similar future questions see the matching lessons; review or delete them
            in the Data sources tab.
          </p>
          <Code>{teach}</Code>
        </div>
      </div>
    </Card>
  );
}

function SignedSetup({ appId, hasSecret }: { appId: string; hasSecret: boolean }) {
  const [secret, setSecret] = useState<string | null>(null);
  const reveal = useMutation({
    mutationFn: () => api<{ secret: string }>(`/api/apps/${appId}/embed/secret`),
    onSuccess: (r) => setSecret(r.secret),
  });
  const rotate = useMutation({
    mutationFn: () => api<{ secret: string }>(`/api/apps/${appId}/embed/secret`, { method: "POST" }),
    onSuccess: (r) => setSecret(r.secret),
  });

  const laravelToken = `// composer require firebase/php-jwt
use Firebase\\JWT\\JWT;

// After the user has logged in (for example in a view composer or controller):
$user = auth()->user();
$chatToken = JWT::encode([
    'sub'   => $user->username,           // required: your stable username
    'email' => $user->email,              // required
    'name'  => $user->name,               // optional, shown in Chat Builder
    'aud'   => '${appId}',
    'iat'   => time(),
    'exp'   => time() + 3600,             // at most 24 hours
], config('services.chat_builder.secret'), 'HS256');`;

  const laravelConfig = `// config/services.php
'chat_builder' => [
    'secret' => env('CHAT_BUILDER_SECRET'),
],

// .env
CHAT_BUILDER_SECRET=your-secret-key`;

  return (
    <Card
      title="Signing people in from your site"
      description="Your site's server signs a short-lived token for the logged-in person. Keep the secret on the server only; never put it in a page or JavaScript."
    >
      <div className="space-y-5 text-sm">
        <div>
          <div className="mb-1 font-medium">Secret key</div>
          {secret ? (
            <div className="flex gap-2">
              <code className="flex-1 overflow-x-auto rounded-md border border-slate-300 bg-white px-3 py-2">{secret}</code>
              <CopyButton text={secret} />
            </div>
          ) : (
            <Button variant="secondary" onClick={() => reveal.mutate()} disabled={!hasSecret || reveal.isPending}>
              Show secret key
            </Button>
          )}
          <button
            onClick={() => confirm("Create a new secret? Tokens signed with the current one will stop working immediately.") && rotate.mutate()}
            className="mt-2 inline-flex items-center gap-1 text-xs text-slate-500 underline"
          >
            <RefreshCw size={12} /> Replace the secret
          </button>
        </div>

        <div className="space-y-2">
          <div className="font-medium">Laravel</div>
          <p className="text-slate-500">1. Store the secret in your configuration:</p>
          <Code>{laravelConfig}</Code>
          <p className="text-slate-500">2. Create the token for the logged-in person and pass it to your layout as $chatToken:</p>
          <Code>{laravelToken}</Code>
          <p className="text-slate-500">3. Add the snippet above to the layout, where <code>{"{{ $chatToken }}"}</code> outputs the token.</p>
        </div>

        <div className="space-y-1 text-slate-500">
          <div className="font-medium text-slate-700">Other platforms</div>
          <p>
            Any JWT library works: sign with HS256 using the secret, with claims <code>sub</code> (username), <code>email</code>,
            optional <code>name</code>, <code>aud</code> = <code>{appId}</code>, <code>iat</code> and <code>exp</code> (at most 24
            hours after <code>iat</code>). If your page gets the token later, call <code>ChatBuilder.identify(token)</code>.
          </p>
        </div>
      </div>
    </Card>
  );
}
