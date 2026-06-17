/**
 * Login (DESIGN §20.8, §9). The single public surface: a centered brand card on
 * a full-height dark background. Cookie-based sign-in via `useAuth().login`; on
 * success the cached `['auth','me']` user updates and we redirect to the page the
 * visitor was guarded away from (defaults to "/").
 *
 * Behavioral Interface Layer (§20.0/§20.1): no market data, no signals, no
 * recommendations — only authentication. The UI never decides anything here.
 */

import { useEffect, useState, type FormEvent } from "react";
import { useLocation, useNavigate } from "react-router-dom";

import { Button } from "@/components/ui/Button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/Card";
import { Input } from "@/components/ui/Input";
import { Label } from "@/components/ui/Label";
import { Switch } from "@/components/ui/Switch";
import { useAuth } from "@/hooks/useAuth";

/** Router state shape set by guards that redirect to /login. */
interface LoginLocationState {
  from?: { pathname?: string };
}

export default function Login() {
  const navigate = useNavigate();
  const location = useLocation();
  const { isAuthenticated, login } = useAuth();

  const [identifier, setIdentifier] = useState("");
  const [password, setPassword] = useState("");
  const [remember, setRemember] = useState(false);

  // Where to land after sign-in: the originally requested page, else Home.
  const state = location.state as LoginLocationState | null;
  const redirectTo = state?.from?.pathname ?? "/";

  // Redirect once authenticated (covers both fresh login and an already-valid
  // session that loads while sitting on /login).
  useEffect(() => {
    if (isAuthenticated) {
      navigate(redirectTo, { replace: true });
    }
  }, [isAuthenticated, navigate, redirectTo]);

  const trimmedIdentifier = identifier.trim();
  const canSubmit =
    trimmedIdentifier.length > 0 && password.length > 0 && !login.isPending;

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!canSubmit) return;
    login.mutate({ identifier: trimmedIdentifier, password, remember });
  }

  const errorDetail = login.error?.detail;

  return (
    <main className="flex min-h-[100dvh] w-full flex-col items-center justify-center overflow-x-hidden bg-bg px-4 py-10">
      <div className="flex w-full max-w-sm flex-col gap-8">
        <header className="flex flex-col items-center gap-2 text-center">
          <h1 className="text-3xl font-semibold tracking-tight text-text">
            WealthOS
          </h1>
          <p className="text-sm text-muted">Discipline beats intelligence.</p>
        </header>

        <Card>
          <CardHeader>
            <CardTitle>Sign in</CardTitle>
            <CardDescription>
              Enter your email or username to continue.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <form className="flex flex-col gap-5" onSubmit={handleSubmit} noValidate>
              <div className="flex flex-col gap-2">
                <Label htmlFor="identifier">Email or username</Label>
                <Input
                  id="identifier"
                  name="identifier"
                  type="text"
                  autoComplete="username"
                  autoCapitalize="none"
                  autoCorrect="off"
                  spellCheck={false}
                  placeholder="you@example.com"
                  value={identifier}
                  onChange={(event) => setIdentifier(event.target.value)}
                  invalid={login.isError}
                  disabled={login.isPending}
                  required
                />
              </div>

              <div className="flex flex-col gap-2">
                <Label htmlFor="password">Password</Label>
                <Input
                  id="password"
                  name="password"
                  type="password"
                  autoComplete="current-password"
                  placeholder="••••••••"
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  invalid={login.isError}
                  disabled={login.isPending}
                  required
                />
              </div>

              <div className="flex items-center justify-between gap-3">
                <Label htmlFor="remember" className="cursor-pointer">
                  Remember me
                </Label>
                <Switch
                  id="remember"
                  checked={remember}
                  onCheckedChange={setRemember}
                  disabled={login.isPending}
                  aria-label="Remember me"
                />
              </div>

              {errorDetail ? (
                <p
                  role="alert"
                  className="rounded-xl border border-loss/40 bg-loss/10 px-3 py-2 text-sm text-loss"
                >
                  {errorDetail}
                </p>
              ) : null}

              <Button
                type="submit"
                className="w-full"
                loading={login.isPending}
                disabled={!canSubmit}
              >
                Sign in
              </Button>
            </form>
          </CardContent>
        </Card>
      </div>
    </main>
  );
}
