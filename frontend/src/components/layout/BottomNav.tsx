import { NavLink } from "react-router-dom";

import { cn } from "@/lib/utils";
import { LAYOUT_ICONS, NAV_ITEMS } from "@/lib/constants";
import { useUiStore } from "@/stores/ui";

const BOTTOM_ITEMS = NAV_ITEMS.filter((item) => item.bottomNav);
const MoreIcon = LAYOUT_ICONS.more;

/**
 * Mobile bottom navigation (hidden on ≥lg). Foregrounds Home + Execution (§20.2).
 * The "More" tab opens the secondary-nav drawer. Fixed to the bottom with safe-area
 * padding; the AppShell reserves space so content is never covered.
 */
export function BottomNav() {
  const openMoreDrawer = useUiStore((s) => s.openMoreDrawer);

  return (
    <nav
      className="fixed inset-x-0 bottom-0 z-30 border-t border-border bg-surface/95 backdrop-blur lg:hidden"
      aria-label="Primary"
    >
      <div className="mx-auto flex max-w-screen-sm items-stretch justify-around px-1 pb-[env(safe-area-inset-bottom)]">
        {BOTTOM_ITEMS.map((item) => {
          const Icon = item.icon;
          return (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.to === "/"}
              className={({ isActive }) =>
                cn(
                  "flex min-h-14 flex-1 flex-col items-center justify-center gap-0.5 px-1 py-2 text-[11px] font-medium",
                  isActive ? "text-accent" : "text-muted",
                )
              }
            >
              <Icon className="h-5 w-5" />
              <span className="truncate">{item.label}</span>
            </NavLink>
          );
        })}
        <button
          type="button"
          onClick={openMoreDrawer}
          className="flex min-h-14 flex-1 flex-col items-center justify-center gap-0.5 px-1 py-2 text-[11px] font-medium text-muted"
        >
          <MoreIcon className="h-5 w-5" />
          <span>More</span>
        </button>
      </div>
    </nav>
  );
}
