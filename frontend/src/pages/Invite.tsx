import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useNavigate, useParams } from "react-router";
import { ApiError, api } from "../api";
import { Button, Input } from "../components/ui";

/** Where an invited person (or someone resetting their password) sets a password. */
export function Invite() {
  const { token = "" } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const { data, error: loadError, isLoading } = useQuery({
    queryKey: ["invite", token],
    queryFn: () => api<{ email: string; name: string; reset: boolean }>(`/api/invites/${token}`),
    retry: false,
  });

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (password !== confirm) {
      setError("The passwords don't match.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await api(`/api/invites/${token}`, { method: "POST", body: JSON.stringify({ password }) });
      queryClient.removeQueries({ queryKey: ["me"] });
      navigate("/", { replace: true });
    } catch (err) {
      setError(err instanceof ApiError ? (err.status === 422 ? "Use at least 12 characters." : err.message) : "Something went wrong.");
    } finally {
      setBusy(false);
    }
  }

  if (isLoading) return null;
  return (
    <div className="flex min-h-screen items-center justify-center px-4">
      {loadError || !data ? (
        <div className="max-w-sm text-center text-slate-600">This link is invalid or has expired. Ask an admin for a new one.</div>
      ) : (
        <form onSubmit={submit} className="w-full max-w-sm space-y-4 rounded-lg border border-slate-200 bg-white p-8 shadow-sm">
          <div>
            <h1 className="text-xl font-semibold">{data.reset ? "Choose a new password" : "Welcome to Chat Builder"}</h1>
            <p className="mt-1 text-sm text-slate-500">
              {data.name ? `${data.name}, set` : "Set"} a password for {data.email}.
            </p>
          </div>
          <label className="block space-y-1 text-sm">
            <span className="font-medium">Password (at least 12 characters)</span>
            <Input type="password" autoComplete="new-password" required minLength={12} value={password} onChange={(e) => setPassword(e.target.value)} />
          </label>
          <label className="block space-y-1 text-sm">
            <span className="font-medium">Confirm password</span>
            <Input type="password" autoComplete="new-password" required value={confirm} onChange={(e) => setConfirm(e.target.value)} />
          </label>
          {error && <p className="text-sm text-red-600">{error}</p>}
          <Button type="submit" disabled={busy} className="w-full">
            {busy ? "Saving..." : "Set password and sign in"}
          </Button>
        </form>
      )}
    </div>
  );
}
