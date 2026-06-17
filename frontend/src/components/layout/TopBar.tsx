import { useState } from "react";
import { LogOut, User as UserIcon } from "lucide-react";

import { cn } from "@/lib/utils";
import { useAuth } from "@/hooks/useAuth";

/**
 * Top app bar. Brand on small screens (the sidebar carries it on ≥lg) and a
 * compact user menu with sign-out. Sticky; never causes horizontal overflow.
 */
export function TopBar() {
  const { user, logout } = useAuth();
  const [menuOpen, setMenuOpen] = useState(false);
  const initial = (user?.username ?? user?.email ?? "?").charAt(0).toUpperCase();

  return (
    <header className="sticky top-0 z-20 flex h-16 items-center justify-between gap-3 border-b border-border bg-bg/90 px-4 backdrop-blur lg:px-6">
      <span className="text-lg font-semibold tracking-tight text-text lg:hidden">
        Wealth<span className="text-accent">OS</span>
      </span>
      <div className="ml-auto">
        <button
          type="button"
          onClick={() => setMenuOpen((v) => !v)}
          aria-haspopup="menu"
          aria-expanded={menuOpen}
          className="flex h-10 items-center gap-2 rounded-full border border-border bg-surface pl-1 pr-3 text-sm text-text"
        >
          <span className="flex h-8 w-8 items-center justify-center rounded-full bg-accent/15 text-accent">
            {user ? initial : <UserIcon className="h-4 w-4" />}
          </span>
          <span className="hidden max-w-[10rem] truncate sm:inline">
            {user?.username ?? user?.email ?? "Account"}
          </span>
        </button>
        {menuOpen ? (
          <>
            <div
              className="fixed inset-0 z-20"
              onClick={() => setMenuOpen(false)}
              aria-hidden
            />
            <div
              role="menu"
              className={cn(
                "absolute right-4 z-30 mt-2 w-48 overflow-hidden rounded-xl border border-border bg-surface shadow-pop animate-scale-in",
              )}
            >
              {user ? (
                <div className="border-b border-border px-3 py-2">
                  <p className="truncate text-sm font-medium text-text">
                    {user.username}
                  </p>
                  <p className="truncate text-xs text-muted">{user.email}</p>
                </div>
              ) : null}
              <button
                type="button"
                role="menuitem"
                onClick={() => {
                  setMenuOpen(false);
                  logout.mutate();
                }}
                className="flex w-full items-center gap-2 px-3 py-2.5 text-left text-sm text-text hover:bg-surface2"
              >
                <LogOut className="h-4 w-4 text-muted" />
                Sign out
              </button>
            </div>
          </>
        ) : null}
      </div>
    </header>
  );
}
