/**
 * Static UI metadata (DESIGN §10, §20). Behavior-first navigation foregrounds
 * Home + Execution; the rest are secondary (§20.2). ACTION_STATUS meta encodes
 * the calm/amber/deliberate treatments the Dashboard hero mirrors from the
 * backend signal (§20.3) — the UI never decides the status itself.
 */

import {
  AlertTriangle,
  ArrowLeftRight,
  CheckCircle2,
  Coins,
  Home,
  LayoutGrid,
  type LucideIcon,
  PiggyBank,
  Receipt,
  Settings as SettingsIcon,
  Target,
  Wallet,
} from "lucide-react";

import type {
  ActionStatusValue,
  TransactionType,
  WealthCycleState,
} from "@/types/api";

/** A primary navigation destination. */
export interface NavItem {
  to: string;
  label: string;
  icon: LucideIcon;
  /** Surfaced in the mobile bottom nav (max 5). */
  bottomNav: boolean;
}

/**
 * The Phase 3 page set (§20.2), in behavior-first order. Bottom nav exposes
 * Home / Execution / Portfolio / Cash / More (the More tab opens a drawer with
 * the remaining items).
 */
export const NAV_ITEMS: readonly NavItem[] = [
  { to: "/", label: "Home", icon: Home, bottomNav: true },
  { to: "/execution", label: "Execution", icon: ArrowLeftRight, bottomNav: true },
  { to: "/portfolio", label: "Portfolio", icon: PiggyBank, bottomNav: true },
  { to: "/cash", label: "Cash Buffer", icon: Wallet, bottomNav: true },
  { to: "/transactions", label: "Transactions", icon: Receipt, bottomNav: false },
  { to: "/networth", label: "Net Worth", icon: Target, bottomNav: false },
  { to: "/settings", label: "Settings", icon: SettingsIcon, bottomNav: false },
] as const;

/** Grouped sidebar sections for the desktop layout (≥lg). */
export interface NavGroup {
  label: string;
  items: readonly NavItem[];
}

export const NAV_GROUPS: readonly NavGroup[] = [
  {
    label: "Decide",
    items: [
      { to: "/", label: "Home", icon: Home, bottomNav: true },
      {
        to: "/execution",
        label: "Execution Center",
        icon: ArrowLeftRight,
        bottomNav: true,
      },
    ],
  },
  {
    label: "Wealth",
    items: [
      { to: "/portfolio", label: "Portfolio", icon: PiggyBank, bottomNav: true },
      { to: "/cash", label: "Cash Buffer", icon: Wallet, bottomNav: true },
      { to: "/networth", label: "Net Worth", icon: Target, bottomNav: false },
    ],
  },
  {
    label: "Records",
    items: [
      {
        to: "/transactions",
        label: "Transactions",
        icon: Receipt,
        bottomNav: false,
      },
    ],
  },
  {
    label: "System",
    items: [
      { to: "/settings", label: "Settings", icon: SettingsIcon, bottomNav: false },
    ],
  },
] as const;

/** The "More" drawer items shown on mobile (everything not in the bottom nav). */
export const MORE_NAV_ITEMS: readonly NavItem[] = NAV_ITEMS.filter(
  (item) => !item.bottomNav,
);

/** Visual tone keys used by the Badge / Stat components. */
export type ToneKey = "neutral" | "accent" | "gain" | "loss" | "warn";

/** Transaction-type display metadata. */
export interface TransactionTypeMeta {
  label: string;
  /** Direction of cash impact, for color hints in the ledger list. */
  cashEffect: "in" | "out" | "neutral";
  tone: ToneKey;
}

export const TRANSACTION_TYPE_META: Record<
  TransactionType,
  TransactionTypeMeta
> = {
  DEPOSIT: { label: "Deposit", cashEffect: "in", tone: "gain" },
  WITHDRAWAL: { label: "Withdrawal", cashEffect: "out", tone: "loss" },
  BUY: { label: "Buy", cashEffect: "out", tone: "accent" },
  SELL: { label: "Sell", cashEffect: "in", tone: "warn" },
  DIVIDEND: { label: "Dividend", cashEffect: "in", tone: "gain" },
  FEE: { label: "Fee", cashEffect: "out", tone: "loss" },
};

/** Ordered list of transaction types for filter chips / select options. */
export const TRANSACTION_TYPES: readonly TransactionType[] = [
  "DEPOSIT",
  "WITHDRAWAL",
  "BUY",
  "SELL",
  "DIVIDEND",
  "FEE",
] as const;

/**
 * Action Status display metadata (§20.3). DO_NOTHING is the calm default
 * success state; REVIEW_REQUIRED is amber attention; REBALANCE_NOW is a
 * deliberate (not alarming) call-to-act. The UI MIRRORS the backend status —
 * it never computes which one applies.
 */
export interface ActionStatusMeta {
  /** Engine value (matches the API `status`). */
  status: ActionStatusValue;
  /** Fallback label; the API `label` field is authoritative when present. */
  label: string;
  icon: LucideIcon;
  tone: ToneKey;
  /** Tailwind classes for the hero surface (border + soft background tint). */
  surfaceClass: string;
  /** Tailwind classes for the headline / icon accent color. */
  accentClass: string;
}

export const ACTION_STATUS_META: Record<ActionStatusValue, ActionStatusMeta> = {
  DO_NOTHING: {
    status: "DO_NOTHING",
    label: "Do Nothing",
    icon: CheckCircle2,
    tone: "gain",
    surfaceClass: "border-gain/40 bg-gain/10",
    accentClass: "text-gain",
  },
  REVIEW_REQUIRED: {
    status: "REVIEW_REQUIRED",
    label: "Review",
    icon: AlertTriangle,
    tone: "warn",
    surfaceClass: "border-warn/40 bg-warn/10",
    accentClass: "text-warn",
  },
  REBALANCE_NOW: {
    status: "REBALANCE_NOW",
    label: "Rebalance Now",
    icon: ArrowLeftRight,
    tone: "accent",
    surfaceClass: "border-accent/50 bg-accent/10",
    accentClass: "text-accent",
  },
};

/** Wealth Operating Cycle state labels (§19.2). */
export const CYCLE_STATE_LABELS: Record<WealthCycleState, string> = {
  ACCUMULATION: "Accumulating",
  READY_TO_DEPLOY: "Ready to Deploy",
  DEPLOYMENT: "Deployment Window",
  REBALANCE_WINDOW: "Rebalance Window",
};

/** Deployment readiness labels (§19.1). */
export const READINESS_LABELS: Record<"READY" | "ACCUMULATING", string> = {
  READY: "Ready",
  ACCUMULATING: "Accumulating",
};

/** Icons reused by layout chrome. */
export const LAYOUT_ICONS = {
  more: LayoutGrid,
  coins: Coins,
} as const;
