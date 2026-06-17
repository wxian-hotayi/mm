/**
 * Execution store (DESIGN §19.6, §20.4). Window schedule + execution plans. The
 * plan (shares + amounts + required transfer) is computed by the backend; the UI
 * only renders and triggers backend operations (generate/approve/execute/skip).
 */

import { useQuery, type UseQueryResult } from "@tanstack/react-query";

import type { ApiError } from "@/api/client";
import { execution } from "@/api/endpoints";
import { queryKeys } from "@/lib/queryKeys";
import type { ExecutionPlanOut, ExecutionPlanStatus, WindowsOut } from "@/types/api";

export function useExecutionWindows(
  asOf?: string,
): UseQueryResult<WindowsOut, ApiError> {
  return useQuery<WindowsOut, ApiError>({
    queryKey: queryKeys.execution.windows(asOf),
    queryFn: () => execution.windows(asOf),
    staleTime: 60_000,
  });
}

export function useExecutionPlans(
  status?: ExecutionPlanStatus,
): UseQueryResult<ExecutionPlanOut[], ApiError> {
  return useQuery<ExecutionPlanOut[], ApiError>({
    queryKey: queryKeys.execution.plans(status),
    queryFn: () => execution.listPlans(status),
    staleTime: 30_000,
  });
}
