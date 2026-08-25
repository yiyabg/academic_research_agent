"use client";

import { create } from "zustand";
import { persist } from "zustand/middleware";

interface ResearchOrganizationState {
  activeOrganizationId: string | null;
  setActiveOrganizationId: (id: string | null) => void;
}

export const useResearchOrganizationStore = create<ResearchOrganizationState>()(
  persist(
    (set) => ({
      activeOrganizationId: null,
      setActiveOrganizationId: (activeOrganizationId) => set({ activeOrganizationId }),
    }),
    { name: "research-organization", version: 1 },
  ),
);
