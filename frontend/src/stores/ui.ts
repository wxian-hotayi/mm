/**
 * Transient UI state store (zustand): the mobile "More" navigation drawer.
 * Server state lives in TanStack Query — this store holds view-only flags.
 */

import { create } from "zustand";

interface UiState {
  /** Whether the mobile "More" bottom-sheet drawer is open. */
  moreDrawerOpen: boolean;
  openMoreDrawer: () => void;
  closeMoreDrawer: () => void;
  toggleMoreDrawer: () => void;
}

export const useUiStore = create<UiState>((set) => ({
  moreDrawerOpen: false,
  openMoreDrawer: () => set({ moreDrawerOpen: true }),
  closeMoreDrawer: () => set({ moreDrawerOpen: false }),
  toggleMoreDrawer: () =>
    set((state) => ({ moreDrawerOpen: !state.moreDrawerOpen })),
}));
