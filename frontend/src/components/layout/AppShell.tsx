import { Outlet } from "react-router-dom";

import { BottomNav } from "@/components/layout/BottomNav";
import { MoreDrawer } from "@/components/layout/MoreDrawer";
import { Sidebar } from "@/components/layout/Sidebar";
import { TopBar } from "@/components/layout/TopBar";

/**
 * Unified app layout (DESIGN §20.7). Sidebar on ≥lg, mobile bottom nav + More
 * drawer below lg. The content column is width-capped and `overflow-x-hidden`
 * so there is NEVER horizontal scrolling; bottom padding clears the mobile nav.
 * Renders the matched route via <Outlet/>.
 */
export function AppShell() {
  return (
    <div className="min-h-[100dvh] bg-bg text-text">
      <Sidebar />
      <div className="flex min-h-[100dvh] flex-col lg:pl-64">
        <TopBar />
        <main className="w-full flex-1 overflow-x-hidden">
          <div className="mx-auto w-full max-w-screen-sm px-4 py-5 pb-24 lg:max-w-5xl lg:px-6 lg:pb-10">
            <Outlet />
          </div>
        </main>
      </div>
      <BottomNav />
      <MoreDrawer />
    </div>
  );
}
