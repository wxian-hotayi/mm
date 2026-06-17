/**
 * Manual market-price store (zustand, persisted to localStorage).
 *
 * Phase 3 has no automatic market-data feed (that arrives in Phase 4); the user
 * enters their broker's latest USD prices + USD→MYR rate manually — the same
 * "manual update" the original spec calls for, NOT a live quotes/news feed
 * (which is forbidden, DESIGN §20.1). These values feed the pricing-aware
 * endpoints (valuation / net worth / action-status / cycle) so holdings can be
 * valued; absent them, the backend reports investment as 0 (documented).
 */

import { create } from "zustand";
import { persist } from "zustand/middleware";

import type { PricingQuery } from "@/api/endpoints";
import type { ValuationIn } from "@/types/api";

interface PricesState {
  /** USD price per symbol, e.g. { VOO: 500.25, QQQ: 480.1 }. */
  prices: Record<string, number>;
  /** USD→MYR rate. */
  fxRate: number | null;
  /** ISO timestamp the user last updated prices (display only). */
  updatedAt: string | null;
  setPricing: (prices: Record<string, number>, fxRate: number) => void;
  clear: () => void;
}

export const usePricesStore = create<PricesState>()(
  persist(
    (set) => ({
      prices: {},
      fxRate: null,
      updatedAt: null,
      setPricing: (prices, fxRate) =>
        set({ prices, fxRate, updatedAt: new Date().toISOString() }),
      clear: () => set({ prices: {}, fxRate: null, updatedAt: null }),
    }),
    { name: "wos-prices" },
  ),
);

/** True when at least one price and an FX rate are set. */
export function hasUsablePricing(state: Pick<PricesState, "prices" | "fxRate">): boolean {
  return state.fxRate != null && Object.keys(state.prices).length > 0;
}

/** A `PricingQuery` for read endpoints, or undefined when nothing is set. */
export function toPricingQuery(
  state: Pick<PricesState, "prices" | "fxRate">,
): PricingQuery | undefined {
  if (!hasUsablePricing(state)) return undefined;
  return { prices: state.prices, fx_rate: state.fxRate as number };
}

/** A `ValuationIn` body for `/portfolio/valuation`, or undefined when unset. */
export function toValuationInput(
  state: Pick<PricesState, "prices" | "fxRate">,
): ValuationIn | undefined {
  if (!hasUsablePricing(state)) return undefined;
  return { prices: state.prices, fx_rate: state.fxRate as number };
}
