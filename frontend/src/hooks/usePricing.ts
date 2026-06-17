/**
 * Convenience hook over the manual prices store (DESIGN §20.1 manual entry).
 * Pages feed `pricing` into useActionStatus/useNetWorthSummary/useCycleState and
 * `valuationInput` into usePortfolioValuation. `hasPricing` gates "enter prices"
 * empty states. The UI never invents prices — it passes through what the user set.
 */

import type { PricingQuery } from "@/api/endpoints";
import {
  hasUsablePricing,
  toPricingQuery,
  toValuationInput,
  usePricesStore,
} from "@/stores/prices";
import type { ValuationIn } from "@/types/api";

export interface UsePricingResult {
  prices: Record<string, number>;
  fxRate: number | null;
  updatedAt: string | null;
  hasPricing: boolean;
  pricing: PricingQuery | undefined;
  valuationInput: ValuationIn | undefined;
  setPricing: (prices: Record<string, number>, fxRate: number) => void;
  clear: () => void;
}

export function usePricing(): UsePricingResult {
  const prices = usePricesStore((s) => s.prices);
  const fxRate = usePricesStore((s) => s.fxRate);
  const updatedAt = usePricesStore((s) => s.updatedAt);
  const setPricing = usePricesStore((s) => s.setPricing);
  const clear = usePricesStore((s) => s.clear);

  const state = { prices, fxRate };
  return {
    prices,
    fxRate,
    updatedAt,
    hasPricing: hasUsablePricing(state),
    pricing: toPricingQuery(state),
    valuationInput: toValuationInput(state),
    setPricing,
    clear,
  };
}
