import { BarChart3, Database, LogOut, MessagesSquare, Settings, Users } from "lucide-react";
import { NavLink, Outlet } from "react-router";
import { useQuery } from "@tanstack/react-query";
import { api, type UpdateStatus } from "../api";
import { useCurrentUser, useLogout } from "../auth";
import { UpdateBanner } from "./UpdateBanner";

const nav = [
  { to: "/apps", label: "Chat Apps", icon: MessagesSquare },
  { to: "/sources", label: "Data Sources", icon: Database },
  { to: "/usage", label: "Usage", icon: BarChart3 },
  { to: "/people", label: "People", icon: Users },
  { to: "/settings", label: "Settings", icon: Settings },
];

export function Layout() {
  const { data: user } = useCurrentUser();
  const logout = useLogout();
  const { data: updates } = useQuery({
    queryKey: ["updates"],
    queryFn: () => api<UpdateStatus>("/api/system/updates"),
  });

  return (
    <div className="flex min-h-screen">
      <aside className="flex w-60 flex-col border-r border-slate-200 bg-white">
        <div className="px-5 py-5 text-lg font-semibold">Chat Builder</div>
        <nav className="flex-1 space-y-1 px-3">
          {nav.map(({ to, label, icon: Icon }) => (
            <NavLink
              key={to}
              to={to}
              className={({ isActive }) =>
                `flex items-center gap-3 rounded-md px-3 py-2 text-sm ${
                  isActive ? "bg-slate-100 font-medium text-slate-900" : "text-slate-600 hover:bg-slate-50"
                }`
              }
            >
              <Icon size={16} />
              {label}
            </NavLink>
          ))}
        </nav>
        <div className="border-t border-slate-200 p-3">
          <div className="truncate px-3 pb-2 text-xs text-slate-500">{user?.email}</div>
          {updates && <div className="px-3 pb-2 text-xs text-slate-400">Version {updates.current}</div>}
          <button
            onClick={logout}
            className="flex w-full items-center gap-3 rounded-md px-3 py-2 text-sm text-slate-600 hover:bg-slate-50"
          >
            <LogOut size={16} />
            Log out
          </button>
        </div>
      </aside>
      <main className="flex-1 overflow-y-auto">
        <UpdateBanner />
        <div className="mx-auto max-w-4xl px-8 py-8">
          <Outlet />
        </div>
      </main>
    </div>
  );
}
