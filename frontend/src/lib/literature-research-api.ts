import { apiClient } from "@/lib/api-client";
import { createClientId } from "@/lib/client-id";
import type {
  ArtifactRegenerationAccepted,
  CandidatePage,
  EvaluationReport,
  EvaluationDataset,
  EvaluationDatasetCreateInput,
  PaperDetail,
  ResearchArtifact,
  ResearchOrganization,
  ResearchOrganizationMember,
  ResearchProject,
  ResearchProtocolVersion,
  ResearchFeedbackAccepted,
  ResearchMetricSnapshot,
  ResearchPolicyVersion,
  ResearchProfile,
  ResearchProjectMemory,
  ResearchRelevanceFeedbackDecision,
  ResearchReadiness,
  ResearchRun,
  ResearchRunEvent,
  ResearchSessionMemory,
  LocalLibraryStatus,
  LocalPaperSearchResponse,
} from "@/types/literature-research";

const base = "/research";

function organizationOptions(organizationId?: string | null) {
  return organizationId ? { headers: { "X-Research-Organization-ID": organizationId } } : undefined;
}

export const literatureResearchApi = {
  readiness: () => apiClient.get<ResearchReadiness>("/health/ready"),
  localLibraryStatus: () => apiClient.get<LocalLibraryStatus>(`${base}/local-library/status`),
  syncLocalLibrary: () => apiClient.post<{ sync_run_id: string; status: string }>(`${base}/local-library/sync`),
  searchLocalLibrary: (body: Record<string, unknown>) =>
    apiClient.post<LocalPaperSearchResponse>(`${base}/local-library/search`, body),
  askLocalLibrary: (body: { question: string; limit?: number; paper_ids: string[]; query_context?: string }) =>
    apiClient.post<{ answer: string; generated_by_llm: boolean; citations: Array<{ paper_id: string; citekey: string; title: string; doi?: string | null; authors: string[]; publication_year?: number | null; page_number: number; text: string }> }>(`${base}/local-library/ask`, body),
  analyzePapersMindmap: (body: Record<string, unknown>) =>
    apiClient.post<Blob>(`${base}/local-library/mindmap`, body),
  listOrganizations: () => apiClient.get<ResearchOrganization[]>(`${base}/organizations`),
  createOrganization: (body: { name: string; slug: string }) =>
    apiClient.post<ResearchOrganization>(`${base}/organizations`, body),
  organizationMembers: (organizationId: string) =>
    apiClient.get<ResearchOrganizationMember[]>(`${base}/organizations/${organizationId}/members`),
  addOrganizationMember: (organizationId: string, email: string) =>
    apiClient.post<ResearchOrganizationMember>(`${base}/organizations/${organizationId}/members`, {
      email,
    }),
  removeOrganizationMember: (organizationId: string, userId: string) =>
    apiClient.delete<void>(`${base}/organizations/${organizationId}/members/${userId}`),
  listProjects: (organizationId?: string | null) =>
    apiClient.get<ResearchProject[]>(`${base}/projects`, organizationOptions(organizationId)),
  createProject: (
    body: { title: string; description: string; organization_id?: string },
    organizationId?: string | null,
  ) =>
    apiClient.post<ResearchProject>(`${base}/projects`, body, organizationOptions(organizationId)),
  listRuns: (organizationId?: string | null) =>
    apiClient.get<ResearchRun[]>(`${base}/runs`, organizationOptions(organizationId)),
  getRun: (runId: string) => apiClient.get<ResearchRun>(`${base}/runs/${runId}`),
  compileProtocol: (projectId: string, body: Record<string, unknown>) =>
    apiClient.post<ResearchProtocolVersion>(
      `${base}/projects/${projectId}/protocols:compile`,
      body,
    ),
  adviseAndCompileProtocol: (projectId: string, body: Record<string, unknown>) =>
    apiClient.post<ResearchProtocolVersion>(
      `${base}/projects/${projectId}/protocols:advise-and-compile`,
      body,
    ),
  approveProtocol: (projectId: string, version: number, protocolHash: string) =>
    apiClient.post<ResearchProtocolVersion>(
      `${base}/projects/${projectId}/protocols/${version}:approve`,
      { protocol_hash: protocolHash },
    ),
  createRun: (body: Record<string, unknown>, organizationId?: string | null) =>
    apiClient.post<ResearchRun>(`${base}/runs`, body, organizationOptions(organizationId)),
  candidates: (runId: string, skip = 0, limit = 100) =>
    apiClient.get<CandidatePage>(`${base}/runs/${runId}/candidates`, {
      params: { skip: String(skip), limit: String(limit) },
    }),
  paper: (runId: string, workId: string) =>
    apiClient.get<PaperDetail>(`${base}/runs/${runId}/papers/${workId}`),
  events: (runId: string, afterSequence = 0) =>
    apiClient.get<ResearchRunEvent[]>(`${base}/runs/${runId}/events`, {
      params: { after_sequence: String(afterSequence) },
    }),
  artifacts: (runId: string) =>
    apiClient.get<ResearchArtifact[]>(`${base}/runs/${runId}/artifacts`),
  shortfallAction: (runId: string, action: string) =>
    apiClient.post<ResearchRun>(`${base}/runs/${runId}/shortage-actions`, { action }),
  cancel: (runId: string) => apiClient.post<ResearchRun>(`${base}/runs/${runId}:cancel`),
  pause: (runId: string) => apiClient.post<ResearchRun>(`${base}/runs/${runId}:pause`),
  resume: (runId: string) => apiClient.post<ResearchRun>(`${base}/runs/${runId}:resume`),
  reanalyze: (runId: string, workId: string) =>
    apiClient.post(`${base}/runs/${runId}/papers/${workId}:reanalyze`, {
      client_request_id: createClientId("reanalyze"),
    }),
  submitRelevanceFeedback: (
    runId: string,
    workId: string,
    decision: ResearchRelevanceFeedbackDecision,
  ) =>
    apiClient.post<ResearchFeedbackAccepted>(`${base}/runs/${runId}/feedback`, {
      work_id: workId,
      feedback_type: "RELEVANCE_CORRECTION",
      payload: { decision },
    }),
  regenerateArtifacts: (runId: string) =>
    apiClient.post<ArtifactRegenerationAccepted>(`${base}/runs/${runId}/artifacts:regenerate`, {
      client_request_id: createClientId("artifacts"),
    }),
  evaluations: (runId: string) =>
    apiClient.get<EvaluationReport[]>(`${base}/runs/${runId}/evaluations`),
  evaluationDatasets: (projectId: string) =>
    apiClient.get<EvaluationDataset[]>(`${base}/projects/${projectId}/evaluation-datasets`),
  createEvaluationDataset: (projectId: string, body: EvaluationDatasetCreateInput) =>
    apiClient.post<EvaluationDataset>(`${base}/projects/${projectId}/evaluation-datasets`, body),
  evaluateRun: (runId: string, datasetId: string) =>
    apiClient.post<EvaluationReport>(`${base}/runs/${runId}/evaluations/${datasetId}`),
  profile: () => apiClient.get<ResearchProfile | null>(`${base}/me/profile`),
  confirmProfile: (body: { preferences: Record<string, unknown>; confirmation_note: string }) =>
    apiClient.post<ResearchProfile>(`${base}/me/profile`, body),
  sessionMemory: (sessionId: string) =>
    apiClient.get<ResearchSessionMemory | null>(`${base}/sessions/${sessionId}/memory`),
  saveSessionMemory: (
    sessionId: string,
    body: {
      project_id?: string;
      draft_slots: Record<string, unknown>;
      missing_slots: string[];
      source_message_ids: string[];
    },
  ) => apiClient.put<ResearchSessionMemory>(`${base}/sessions/${sessionId}/memory`, body),
  projectMemories: (projectId: string) =>
    apiClient.get<ResearchProjectMemory[]>(`${base}/projects/${projectId}/memories`),
  createProjectMemory: (
    projectId: string,
    body: {
      memory_type: ResearchProjectMemory["memory_type"];
      content: Record<string, unknown>;
      source: "USER_FEEDBACK";
      source_id: string;
      confidence: number;
      valid_from: string;
    },
  ) => apiClient.post<ResearchProjectMemory>(`${base}/projects/${projectId}/memories`, body),
  policyVersions: () => apiClient.get<ResearchPolicyVersion[]>(`${base}/policies`),
  createPolicyVersion: (body: {
    policy_key: string;
    content: Record<string, unknown>;
    valid_from: string;
    valid_to?: string;
  }) => apiClient.post<ResearchPolicyVersion>(`${base}/admin/policies`, body),
  listMetricSnapshots: () =>
    apiClient.get<ResearchMetricSnapshot[]>(`${base}/admin/metric-snapshots`),
  importMetricSnapshot: (form: FormData) =>
    apiClient.post<ResearchMetricSnapshot>(`${base}/admin/metric-snapshots`, form),
};

export function artifactDownloadUrl(runId: string, artifactId: string): string {
  return `/api/research/runs/${runId}/artifacts/${artifactId}`;
}
