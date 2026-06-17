/**
 * Toast notification store (zustand). Lightweight, dependency-free; the
 * `Toaster` component subscribes and renders. Toasts communicate outcomes of
 * backend operations — they never assert investment decisions (DESIGN §20.0).
 */

import { create } from "zustand";

export type ToastVariant = "default" | "success" | "warn" | "error";

export interface Toast {
  id: string;
  title: string;
  description?: string;
  variant: ToastVariant;
  /** Auto-dismiss after this many ms; 0 disables auto-dismiss. */
  duration: number;
}

export interface ToastInput {
  title: string;
  description?: string;
  variant?: ToastVariant;
  duration?: number;
}

interface ToastState {
  toasts: Toast[];
  push: (input: ToastInput) => string;
  dismiss: (id: string) => void;
  clear: () => void;
}

const DEFAULT_DURATION = 4500;

function makeId(): string {
  return `toast_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
}

export const useToastStore = create<ToastState>((set) => ({
  toasts: [],
  push: (input) => {
    const id = makeId();
    const toast: Toast = {
      id,
      title: input.title,
      description: input.description,
      variant: input.variant ?? "default",
      duration: input.duration ?? DEFAULT_DURATION,
    };
    set((state) => ({ toasts: [...state.toasts, toast] }));
    return id;
  },
  dismiss: (id) =>
    set((state) => ({ toasts: state.toasts.filter((t) => t.id !== id) })),
  clear: () => set({ toasts: [] }),
}));

/**
 * Imperative helper for non-component code (e.g. mutation error handlers).
 * Mirrors the common `toast.success(...)` ergonomics.
 */
export const toast = {
  show: (input: ToastInput) => useToastStore.getState().push(input),
  success: (title: string, description?: string) =>
    useToastStore.getState().push({ title, description, variant: "success" }),
  warn: (title: string, description?: string) =>
    useToastStore.getState().push({ title, description, variant: "warn" }),
  error: (title: string, description?: string) =>
    useToastStore.getState().push({ title, description, variant: "error" }),
  dismiss: (id: string) => useToastStore.getState().dismiss(id),
} as const;
