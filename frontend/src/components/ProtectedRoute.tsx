import { Navigate, Outlet } from "react-router-dom";

import { Spinner } from "@/components/ui/Spinner";
import { useAuth } from "@/hooks/useAuth";

/**
 * Route guard. While the session check is in flight a spinner is shown; an
 * unauthenticated visitor is redirected to /login. Authenticated users get the
 * matched child route. Used as a layout route wrapping the AppShell.
 */
export function ProtectedRoute() {
  const { isLoading, isAuthenticated } = useAuth();

  if (isLoading) {
    return (
      <div className="flex min-h-[100dvh] items-center justify-center bg-bg">
        <Spinner size="lg" />
      </div>
    );
  }

  if (!isAuthenticated) {
    return <Navigate to="/login" replace />;
  }

  return <Outlet />;
}
