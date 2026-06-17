/**
 * Centralised TanStack Query keys (DESIGN §5). One source of truth so hooks and
 * mutation invalidations never drift. Pricing/param objects are embedded in the
 * key; Query hashes them structurally.
 */

import type { PricingQuery } from "@/api/endpoints";

type Maybe = unknown;

export const queryKeys = {
  auth: { me: ["auth", "me"] as const },
  actionStatus: (pricing?: PricingQuery) =>
    ["action-status", (pricing ?? null) as Maybe] as const,
  cycle: (pricing?: PricingQuery) =>
    ["cycle", "state", (pricing ?? null) as Maybe] as const,
  portfolio: {
    valuation: (input?: Maybe) =>
      ["portfolio", "valuation", (input ?? null) as Maybe] as const,
  },
  cash: {
    summary: (asOf?: string) =>
      ["cash", "summary", (asOf ?? null) as Maybe] as const,
    accounts: (includeArchived?: boolean) =>
      ["cash", "accounts", includeArchived ?? false] as const,
    movements: (params?: Maybe) =>
      ["cash", "movements", (params ?? null) as Maybe] as const,
  },
  networth: {
    summary: (pricing?: PricingQuery) =>
      ["networth", "summary", (pricing ?? null) as Maybe] as const,
    breakdown: (pricing?: PricingQuery) =>
      ["networth", "breakdown", (pricing ?? null) as Maybe] as const,
  },
  execution: {
    windows: (asOf?: string) =>
      ["execution", "windows", (asOf ?? null) as Maybe] as const,
    plans: (status?: string) =>
      ["execution", "plans", (status ?? null) as Maybe] as const,
  },
  ips: {
    policy: (pricing?: PricingQuery) =>
      ["ips", "policy", (pricing ?? null) as Maybe] as const,
    compliance: (pricing?: PricingQuery) =>
      ["ips", "compliance", (pricing ?? null) as Maybe] as const,
  },
  transactions: (params?: Maybe) =>
    ["transactions", (params ?? null) as Maybe] as const,
  deployment: (status?: string) =>
    ["deployment", "queue", (status ?? null) as Maybe] as const,
  behavior: (params?: Maybe) =>
    ["behavior", (params ?? null) as Maybe] as const,
} as const;

/** Domain key prefixes for bulk invalidation after mutations. */
export const queryRoots = {
  actionStatus: ["action-status"],
  cycle: ["cycle"],
  portfolio: ["portfolio"],
  cash: ["cash"],
  networth: ["networth"],
  execution: ["execution"],
  ips: ["ips"],
  transactions: ["transactions"],
  deployment: ["deployment"],
  behavior: ["behavior"],
} as const;
