"use client";

import { create } from "zustand";

interface LiteratureResearchState {
  selectedWorkId: string | null;
  lastEventSequence: Record<string, number>;
  selectWork: (workId: string | null) => void;
  advanceSequence: (runId: string, sequence: number) => void;
}

export const useLiteratureResearchStore = create<LiteratureResearchState>((set) => ({
  selectedWorkId: null,
  lastEventSequence: {},
  selectWork: (selectedWorkId) => set({ selectedWorkId }),
  advanceSequence: (runId, sequence) =>
    set((state) => ({
      lastEventSequence: {
        ...state.lastEventSequence,
        [runId]: Math.max(state.lastEventSequence[runId] ?? 0, sequence),
      },
    })),
}));
