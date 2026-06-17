/**
 * Portfolio store (DESIGN §7.1, §20.5). Valuation requires prices + FX (the
 * backend never invents them), so the query is disabled until the caller
 * supplies a `ValuationIn`. The UI only displays the returned figures.
 */

import { useQuery, type UseQueryResult } from "@tanstack/react-query";

import type { ApiError } from "@/api/client";
import { portfolio } from "@/api/endpoints";
import { queryKeys } from "@/lib/queryKeys";
import type { ValuationIn, ValuationOut } from "@/types/api";

export function usePortfolioValuation(
  input: ValuationIn | undefined,
): UseQueryResult<ValuationOut, ApiError> {
  return useQuery<ValuationOut, ApiError>({
    queryKey: queryKeys.portfolio.valuation(input),
    queryFn: () => portfolio.valuation(input as ValuationIn),
    enabled: input != null,
    staleTime: 30_000,
  });
}
