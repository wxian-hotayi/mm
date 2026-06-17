/**
 * Action Status — the primary system signal (DESIGN §19.3, §20.3). This hook is
 * the read-only "execution status store": it mirrors the backend decision and
 * never computes which status applies. {data,isLoading,error} = API state.
 */

import { useQuery, type UseQueryResult } from "@tanstack/react-query";

import type { ApiError } from "@/api/client";
import { actionStatus, type PricingQuery } from "@/api/endpoints";
import { queryKeys } from "@/lib/queryKeys";
import type { ActionStatusOut } from "@/types/api";

export function useActionStatus(
  pricing?: PricingQuery,
): UseQueryResult<ActionStatusOut, ApiError> {
  return useQuery<ActionStatusOut, ApiError>({
    queryKey: queryKeys.actionStatus(pricing),
    queryFn: () => actionStatus.get(pricing),
    staleTime: 30_000,
  });
}
