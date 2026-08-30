import { NavLink } from "react-router-dom";
import { MessageSquare, Key, Cpu, BarChart2, Server } from "lucide-react";
import { SettingsModal } from "./SettingsModal";

const NAV = [
  { to: "/chat", label: "Chat", icon: MessageSquare },
  { to: "/keys", label: "Keys", icon: Key },
  { to: "/adapters", label: "Adapters", icon: Cpu },
  { to: "/usage", label: "Usage", icon: BarChart2 },
  { to: "/workers", label: "Workers", icon: Server },
];

export function TopNav() {
  return (
    <header className="border-b border-slate-800 bg-slate-900/80 backdrop-blur-sm sticky top-0 z-40">
      <div className="mx-auto flex h-14 max-w-screen-xl items-center gap-2 px-4">
        {/* Logo */}
        <div className="flex items-center gap-2 mr-4">
          <div className="h-7 w-7 rounded-lg bg-sky-500 flex items-center justify-center text-white font-bold text-sm">I</div>
          <span className="font-semibold text-slate-100 hidden sm:block">Inferra</span>
        </div>

        {/* Nav links */}
        <nav className="flex items-center gap-1 flex-1">
          {NAV.map(({ to, label, icon: Icon }) => (
            <NavLink
              key={to}
              to={to}
              className={({ isActive }) =>
                `flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm transition-colors ${
                  isActive
                    ? "bg-sky-600/20 text-sky-400 font-medium"
                    : "text-slate-400 hover:bg-slate-800 hover:text-slate-200"
                }`
              }
            >
              <Icon className="h-4 w-4" />
              <span className="hidden sm:inline">{label}</span>
            </NavLink>
          ))}
        </nav>

        {/* Settings */}
        <SettingsModal />
      </div>
    </header>
  );
}
