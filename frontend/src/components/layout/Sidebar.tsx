import { NavLink } from "react-router-dom";

import { cn } from "@/lib/utils";
import { NAV_GROUPS } from "@/lib/constants";

/**
 * Desktop sidebar (≥lg only). Grouped navigation; "Decide" (Home + Execution)
 * sits first to keep the behavioral surfaces foregrounded (§20.2).
 */
export function Sidebar() {
  return (
    <aside className="fixed inset-y-0 left-0 z-30 hidden w-64 flex-col border-r border-border bg-surface lg:flex">
      <div className="flex h-16 items-center gap-2 px-5">
        <span className="text-lg font-semibold tracking-tight text-text">
          Wealth<span className="text-accent">OS</span>
        </span>
      </div>
      <nav className="flex-1 overflow-y-auto px-3 py-2" aria-label="Primary">
        {NAV_GROUPS.map((group) => (
          <div key={group.label} className="mb-4">
            <p className="px-3 pb-1 text-xs font-medium uppercase tracking-wide text-muted">
              {group.label}
            </p>
            <ul className="flex flex-col gap-0.5">
              {group.items.map((item) => {
                const Icon = item.icon;
                return (
                  <li key={item.to}>
                    <NavLink
                      to={item.to}
                      end={item.to === "/"}
                      className={({ isActive }) =>
                        cn(
                          "flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors",
                          isActive
                            ? "bg-surface2 text-text"
                            : "text-muted hover:bg-surface2/60 hover:text-text",
                        )
                      }
                    >
                      <Icon className="h-5 w-5" />
                      {item.label}
                    </NavLink>
                  </li>
                );
              })}
            </ul>
          </div>
        ))}
      </nav>
      <div className="border-t border-border px-5 py-3">
        <p className="text-xs text-muted">Discipline beats intelligence.</p>
      </div>
    </aside>
  );
}
