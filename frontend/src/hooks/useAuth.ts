/**
 * Authentication hook (DESIGN §9, §20.8). Cookie-based sessions are primary:
 * the browser carries the HttpOnly `wos_access` / `wos_refresh` cookies, so the
 * client only tracks the authenticated user (no token in JS). TanStack Query
 * caches `['auth','me']`; login/logout/refresh mutations keep it in sync.
 */

import { useCallback, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseMutationResult,
  type UseQueryResult,
} from "@tanstack/react-query";

import { ApiError, onAuthFailure } from "@/api/client";
import { auth } from "@/api/endpoints";
import type { LoginIn, LoginOut, OkOut, UserOut } from "@/types/api";

/** Query key for the authenticated user profile. */
export const AUTH_ME_KEY = ["auth", "me"] as const;

/** Return shape of {@link useAuth}. */
export interface UseAuthResult {
  user: UserOut | undefined;
  isLoading: boolean;
  isAuthenticated: boolean;
  isError: boolean;
  /** Underlying query for advanced callers (e.g. ProtectedRoute). */
  query: UseQueryResult<UserOut, ApiError>;
  login: UseMutationResult<LoginOut, ApiError, LoginIn>;
  logout: UseMutationResult<OkOut, ApiError, void>;
}

/**
 * Load and manage the current session. On an irrecoverable auth failure (the
 * client's refresh-on-401 gave up) the cached user is cleared so guards react
 * immediately while the client redirects to `/login`.
 */
export function useAuth(): UseAuthResult {
  const queryClient = useQueryClient();
  const navigate = useNavigate();

  const query = useQuery<UserOut, ApiError>({
    queryKey: AUTH_ME_KEY,
    queryFn: ({ signal }) => auth.me(signal),
    // A 401 means "not logged in" — surface it without noisy retries.
    retry: (failureCount, error) => {
      if (error instanceof ApiError && error.status === 401) return false;
      return failureCount < 1;
    },
    staleTime: 60_000,
  });

  useEffect(() => {
    const unsubscribe = onAuthFailure(() => {
      queryClient.setQueryData(AUTH_ME_KEY, null);
    });
    return unsubscribe;
  }, [queryClient]);

  const login = useMutation<LoginOut, ApiError, LoginIn>({
    mutationFn: (payload) => auth.login(payload),
    onSuccess: (result) => {
      queryClient.setQueryData(AUTH_ME_KEY, result.user);
    },
  });

  const logout = useMutation<OkOut, ApiError, void>({
    mutationFn: () => auth.logout(),
    onSettled: () => {
      // Clear every cached query — none of it belongs to the next session.
      queryClient.setQueryData(AUTH_ME_KEY, null);
      queryClient.clear();
      navigate("/login", { replace: true });
    },
  });

  const user = query.data ?? undefined;

  return {
    user,
    isLoading: query.isLoading,
    isAuthenticated: user !== undefined && user !== null,
    isError: query.isError,
    query,
    login,
    logout,
  };
}

/** Imperative redirect helpers for non-hook call sites. */
export function useAuthRedirects(): {
  goToLogin: () => void;
  goToHome: () => void;
} {
  const navigate = useNavigate();
  const goToLogin = useCallback(
    () => navigate("/login", { replace: true }),
    [navigate],
  );
  const goToHome = useCallback(() => navigate("/", { replace: true }), [navigate]);
  return { goToLogin, goToHome };
}
