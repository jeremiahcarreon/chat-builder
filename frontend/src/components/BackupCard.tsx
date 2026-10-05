import { Download, Loader2 } from "lucide-react";
import { useState, type FormEvent } from "react";
import { Button, Card, Field, Input } from "./ui";

/** Downloads an encrypted backup of the whole install, for moving to a new server. */
export function BackupCard() {
  const [passphrase, setPassphrase] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (passphrase !== confirm) {
      setError("The passphrases don't match.");
      return;
    }
    setBusy(true);
    setError(null);
    setDone(false);
    try {
      const response = await fetch("/api/system/backup", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ passphrase }),
      });
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(typeof body.detail === "string" ? body.detail : "The backup failed.");
      }
      const name = /filename="?([^"]+)"?/.exec(response.headers.get("content-disposition") ?? "")?.[1] ?? "chat-builder.cbbackup";
      const url = URL.createObjectURL(await response.blob());
      const link = Object.assign(document.createElement("a"), { href: url, download: name });
      link.click();
      URL.revokeObjectURL(url);
      setPassphrase("");
      setConfirm("");
      setDone(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "The backup failed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card
      title="Back up and move"
      description="One encrypted file with everything in this install: apps, conversations, people, API keys, documents and their search index. Restore it on another server with: chat-builder restore FILE"
    >
      <form onSubmit={submit} className="space-y-4">
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Passphrase" hint="At least 12 characters. Needed to restore; it can't be recovered.">
            <Input type="password" autoComplete="new-password" minLength={12} required value={passphrase} onChange={(e) => setPassphrase(e.target.value)} />
          </Field>
          <Field label="Repeat the passphrase">
            <Input type="password" autoComplete="new-password" required value={confirm} onChange={(e) => setConfirm(e.target.value)} />
          </Field>
        </div>
        <div className="flex items-center gap-3">
          <Button type="submit" disabled={busy}>
            {busy ? <Loader2 size={14} className="animate-spin" /> : <Download size={14} />}
            {busy ? "Creating backup..." : "Download full backup"}
          </Button>
          {done && <span className="text-sm text-emerald-700">Downloaded. Keep the file and its passphrase safe.</span>}
          {error && <span className="text-sm text-red-600">{error}</span>}
        </div>
      </form>
    </Card>
  );
}
