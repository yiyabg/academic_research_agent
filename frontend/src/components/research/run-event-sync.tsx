"use client";

import { useCallback, useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";

import { useWebSocket } from "@/hooks/use-websocket";
import { qk } from "@/lib/query-keys";
import { WS_URL } from "@/lib/constants";
import { useAuthStore, useLiteratureResearchStore } from "@/stores";
import type { ResearchRunEvent } from "@/types/literature-research";

export function RunEventSync({ runId }: { runId: string }) {
  const queryClient = useQueryClient();
  const token = useAuthStore((state) => state.accessToken);
  const advanceSequence = useLiteratureResearchStore((state) => state.advanceSequence);
  const onMessage = useCallback(
    (message: MessageEvent) => {
      const envelope = JSON.parse(String(message.data)) as { type: string; data: ResearchRunEvent };
      if (envelope.type !== "research_run_event") return;
      advanceSequence(runId, envelope.data.sequence);
      void queryClient.invalidateQueries({ queryKey: qk.literatureResearch.run(runId) });
      void queryClient.invalidateQueries({ queryKey: qk.literatureResearch.candidates(runId) });
      if (envelope.data.event_type === "ARTIFACT_READY" || envelope.data.stage === "RELEASE_CHECKING") {
        void queryClient.invalidateQueries({ queryKey: qk.literatureResearch.artifacts(runId) });
      }
    },
    [advanceSequence, queryClient, runId],
  );
  const { connect, disconnect } = useWebSocket({
    url: `${WS_URL}/api/v1/research/runs/${runId}/stream`,
    protocols: token ? [`access_token.${token}`, "research"] : undefined,
    onMessage,
  });
  useEffect(() => {
    connect();
    return disconnect;
  }, [connect, disconnect]);
  return null;
}
