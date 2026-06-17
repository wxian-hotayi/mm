/**
 * Net Worth store (DESIGN §19.4, §20.3). Reporting aggregate (portfolio ⊂ net
 * worth). When prices/FX are absent the backend reports investment as 0 — the UI
 * shows exactly what the API returns and never fills the gap itself.
 */

import { useQuery, type UseQueryResult } from "@tanstack/react-query";

import type { ApiError } from "@/api/client";
import { networth, type PricingQuery } from "@/api/endpoints";
import { queryKeys } from "@/lib/queryKeys";
import type { NetWorthBreakdownOut, NetWorthSummaryOut } from "@/types/api";

export function useNetWorthSummary(
  pricing?: PricingQuery,
): UseQueryResult<NetWorthSummaryOut, ApiError> {
  return useQuery<NetWorthSummaryOut, ApiError>({
    queryKey: queryKeys.networth.summary(pricing),
    queryFn: () => networth.summary(pricing),
    staleTime: 30_000,
  });
}

export function useNetWorthBreakdown(
  pricing?: PricingQuery,
): UseQueryResult<NetWorthBreakdownOut, ApiError> {
  return useQuery<NetWorthBreakdownOut, ApiError>({
    queryKey: queryKeys.networth.breakdown(pricing),
    queryFn: () => networth.breakdown(pricing),
    staleTime: 30_000,
  });
}
