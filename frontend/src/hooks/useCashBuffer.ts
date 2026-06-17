/**
 * Cash Buffer store (DESIGN §19.1, §20.6). Surfaces the operational cash system:
 * account balances, deployable surplus and readiness — all derived server-side.
 */

import { useQuery, type UseQueryResult } from "@tanstack/react-query";

import type { ApiError } from "@/api/client";
import { cash } from "@/api/endpoints";
import { queryKeys } from "@/lib/queryKeys";
import type { CashAccountOut, CashSummaryOut } from "@/types/api";

export function useCashSummary(
  asOf?: string,
): UseQueryResult<CashSummaryOut, ApiError> {
  return useQuery<CashSummaryOut, ApiError>({
    queryKey: queryKeys.cash.summary(asOf),
    queryFn: () => cash.summary(asOf),
    staleTime: 30_000,
  });
}

export function useCashAccounts(
  includeArchived = false,
): UseQueryResult<CashAccountOut[], ApiError> {
  return useQuery<CashAccountOut[], ApiError>({
    queryKey: queryKeys.cash.accounts(includeArchived),
    queryFn: () => cash.listAccounts(includeArchived),
    staleTime: 30_000,
  });
}
