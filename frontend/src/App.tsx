import { Suspense, lazy } from "react";
import { Route, Routes } from "react-router-dom";

import { PlaceholderPage } from "@/components/PlaceholderPage";
import { ProtectedRoute } from "@/components/ProtectedRoute";
import { AppShell } from "@/components/layout/AppShell";
import { Spinner } from "@/components/ui/Spinner";

/**
 * Application routes (DESIGN §20.2). Public `/login`; everything else is guarded
 * by {@link ProtectedRoute} and rendered inside the {@link AppShell}.
 *
 * PAGES STAGE (Phase 3B): each real page is lazy-loaded with {@link lazy} and
 * wrapped in a {@link Suspense} fallback so route chunks are emitted separately
 * and the shell stays responsive while a page loads. `transactions` and
 * `networth` remain placeholders (deferred — out of Phase 3B scope); the
 * catch-all `*` keeps the NotFound placeholder. Layout and guards are untouched.
 */
const Login = lazy(() => import("@/pages/Login"));
const Dashboard = lazy(() => import("@/pages/Dashboard"));
const ExecutionCenter = lazy(() => import("@/pages/ExecutionCenter"));
const Portfolio = lazy(() => import("@/pages/Portfolio"));
const CashBuffer = lazy(() => import("@/pages/CashBuffer"));
const Settings = lazy(() => import("@/pages/Settings"));

/** Centered spinner shown while a lazy route chunk loads. */
function RouteFallback() {
  return (
    <div className="flex min-h-[40vh] items-center justify-center">
      <Spinner size="lg" />
    </div>
  );
}

export default function App() {
  return (
    <Suspense fallback={<RouteFallback />}>
      <Routes>
        <Route path="/login" element={<Login />} />

        <Route element={<ProtectedRoute />}>
          <Route element={<AppShell />}>
            <Route index element={<Dashboard />} />
            <Route path="execution" element={<ExecutionCenter />} />
            <Route path="portfolio" element={<Portfolio />} />
            <Route path="cash" element={<CashBuffer />} />
            <Route
              path="transactions"
              element={
                <PlaceholderPage
                  title="Transactions"
                  note="Record-keeping ledger of broker activity."
                />
              }
            />
            <Route
              path="networth"
              element={
                <PlaceholderPage
                  title="Net Worth"
                  note="Reporting aggregate; portfolio is a subset."
                />
              }
            />
            <Route path="settings" element={<Settings />} />
            <Route
              path="*"
              element={
                <PlaceholderPage title="Not Found" note="No such page." />
              }
            />
          </Route>
        </Route>
      </Routes>
    </Suspense>
  );
}
