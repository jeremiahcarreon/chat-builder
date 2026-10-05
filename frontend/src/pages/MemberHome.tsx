import { useQuery } from "@tanstack/react-query";
import { LogOut, MessagesSquare } from "lucide-react";
import { Link } from "react-router";
import { api, type MyApp } from "../api";
import { useCurrentUser, useLogout } from "../auth";

/** What members see: the chat apps they've been given. */
export function MemberHome() {
  const { data: user } = useCurrentUser();
  const logout = useLogout();
  const { data: apps, isLoading } = useQuery({ queryKey: ["my-apps"], queryFn: () => api<MyApp[]>("/api/my/apps") });

  return (
    <div className="mx-auto max-w-3xl px-6 py-10">
      <div className="mb-8 flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold">Your chat apps</h1>
          <p className="text-sm text-slate-500">Signed in as {user?.name || user?.email}</p>
        </div>
        <button onClick={logout} className="flex items-center gap-2 text-sm text-slate-600 hover:text-slate-900">
          <LogOut size={16} /> Log out
        </button>
      </div>
      {isLoading ? null : !apps?.length ? (
        <p className="rounded-lg border border-dashed border-slate-300 bg-white p-8 text-center text-slate-500">
          You don't have access to any chat apps yet. Ask an admin to add you.
        </p>
      ) : (
        <div className="grid gap-4 sm:grid-cols-2">
          {apps.map((app) => (
            <Link key={app.id} to={`/chat/${app.id}`} className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm hover:border-slate-400">
              <div className="flex items-center gap-2 font-semibold">
                <MessagesSquare size={16} className="text-slate-500" /> {app.name}
              </div>
              {app.description && <p className="mt-1 text-sm text-slate-500">{app.description}</p>}
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
