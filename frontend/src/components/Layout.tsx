import { Database, LogOut, MessagesSquare, BarChart3, Settings } from "lucide-react";
import { NavLink, Outlet } from "react-router";
import { useCurrentUser, useLogout } from "../auth";

const nav = [
  { to: "/apps", label: "Chat Apps", icon: MessagesSquare },
  { to: "/sources", label: "Data Sources", icon: Database },
  { to: "/usage", label: "Usage", icon: BarChart3 },
  { to: "/settings", label: "Settings", icon: Settings },
];

export function Layout() {
  const { data: user } = useCurrentUser();
  const logout = useLogout();

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
        <div className="mx-auto max-w-4xl px-8 py-8">
          <Outlet />
        </div>
      </main>
    </div>
  );
}
