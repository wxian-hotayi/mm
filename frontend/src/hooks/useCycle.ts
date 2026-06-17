/**
 * Wealth Operating Cycle store (DESIGN §19.2). The continuous derived state
 * {ACCUMULATION, READY_TO_DEPLOY, DEPLOYMENT, REBALANCE_WINDOW} — read-only.
 */

import { useQuery, type UseQueryResult } from "@tanstack/react-query";

import type { ApiError } from "@/api/client";
import { cycle, type PricingQuery } from "@/api/endpoints";
import { queryKeys } from "@/lib/queryKeys";
import type { CycleStateOut } from "@/types/api";

export function useCycleState(
  pricing?: PricingQuery,
): UseQueryResult<CycleStateOut, ApiError> {
  return useQuery<CycleStateOut, ApiError>({
    queryKey: queryKeys.cycle(pricing),
    queryFn: () => cycle.state(pricing),
    staleTime: 30_000,
  });
}
