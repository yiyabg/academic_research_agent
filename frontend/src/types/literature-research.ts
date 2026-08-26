export type RunState =
  | "QUEUED"
  | "DISCOVERING"
  | "NORMALIZING"
  | "ENRICHING_METRICS"
  | "DEDUPLICATING"
  | "HARD_FILTERING"
  | "RELEVANCE_SCORING"
  | "FULLTEXT_ACQUIRING"
  | "PARSING"
  | "SELECTING"
  | "ANALYZING"
  | "EVIDENCE_AUDITING"
  | "SYNTHESIZING"
  | "RENDERING"
  | "RELEASE_CHECKING"
  | "COMPLETED"
  | "AWAITING_RELAXATION_AUTHORIZATION"
  | "PARTIALLY_COMPLETED"
  | "FAILED_RETRYABLE"
  | "FAILED_TERMINAL"
  | "CANCEL_REQUESTED"
  | "PAUSED"
  | "CANCELLED";

export type ResearchExecutionMode = "validate_only" | "search_only" | "full_research";

export interface LocalLibraryStatus {
  configured: boolean;
  owner_id: string | null;
  status: string;
  source_root: string | null;
  indexed_papers: number;
  current_indexed_papers: number;
  missing_papers: number;
  quarantined_items: number;
  catalogued_papers: number;
  searchable_papers: number;
  stale_indexed_papers: number;
  missing_source_papers: number;
  latest_quarantine_items: number;
  last_sync_summary: Record<string, unknown>;
  latest_sync: { id: string; status: string; summary_json: Record<string, unknown>; error_message?: string | null; created_at: string; updated_at?: string | null } | null;
  quarantine: Array<{ item_kind: string; relative_path?: string | null; citekey?: string | null; detail: string }>;
}

export interface LocalPaper {
  id: string;
  citekey: string;
  doi?: string | null;
  title: string;
  authors: string[];
  publication_year?: number | null;
  bibtex_type: string;
  source_kind: "pdf" | "html";
  relative_source_path: string;
  evidence: Array<{
    page_number: number;
    chunk_index: number;
    text: string;
    score?: number | null;
    vector_score?: number | null;
    bm25_score?: number | null;
    rrf_score?: number | null;
    rerank_score?: number | null;
    mmr_score?: number | null;
    section_heading?: string | null;
    paragraph_index?: number | null;
    bbox?: number[] | null;
    parent_text?: string | null;
  }>;
}

export interface LocalPaperSearchResponse {
  items: LocalPaper[];
  total: number;
  retrieval_mode: "hybrid" | "metadata";
  candidate_chunks: number;
  candidate_papers: number;
  rejected_by_score: number;
  insufficient_evidence: boolean;
  retrieval_run_id?: string | null;
  trace?: Record<string, unknown>;
}

export type LocalPaperAnalysisStatus =
  | "QUEUED" | "RETRIEVING" | "ANALYZING" | "SYNTHESIZING" | "RENDERING"
  | "COMPLETED" | "PARTIAL" | "FAILED" | "CANCELLED";

export interface LocalPaperAnalysisJob {
  id: string;
  session_id: string;
  library_id: string;
  owner_id: string;
  project_id?: string | null;
  mode: string;
  status: LocalPaperAnalysisStatus;
  question: string;
  retrieval_run_id?: string | null;
  result: Record<string, unknown>;
  error_code?: string | null;
  error_message?: string | null;
  created_at: string;
  updated_at?: string | null;
}

export interface ResearchReadiness {
  status: "ready" | "not_ready";
  capabilities: {
    search_only: boolean;
    full_research: boolean;
  };
  llm?: {
    status: "healthy" | "unavailable";
    detail?: string;
    provider?: "openai" | "deepseek" | "openai_compatible";
    model?: string;
    error_type?: string;
  };
}

export interface ResearchProject {
  id: string;
  owner_id: string;
  organization_id: string | null;
  title: string;
  description: string;
  status: "ACTIVE" | "ARCHIVED";
  created_at: string;
}

export interface ProtocolIssue {
  code: string;
  message: string;
  field?: string;
  blocking: boolean;
}

export interface ResearchProtocolVersion {
  id: string;
  project_id: string;
  version: number;
  protocol_hash: string;
  status: "DRAFT" | "APPROVED" | "SUPERSEDED";
  protocol: {
    topic: string;
    topic_definition: string;
    research_questions: string[];
    topic_model: {
      must_have_facets: Array<{
        facet_id: string;
        name: string;
        description: string;
        minimum_score: number;
        weight: number;
      }>;
    };
    time_scope: { from: string; to: string; timezone: string };
    document_scope: { allowed_types: string[]; allowed_languages: string[] };
    quantity_policy: { target_count: number; quality_floor_locked: true; shortfall_action: string };
    llm_budget: {
      max_requests: number;
      max_input_tokens: number;
      max_output_tokens: number;
      max_total_tokens: number;
      max_cost_usd: string | null;
    };
    constraints: Array<{
      constraint_id: string;
      field: string;
      operator: string;
      value: unknown;
      severity: string;
    }>;
    issues: ProtocolIssue[];
    ambiguity_status: "resolved" | "needs_clarification";
    draft_advice_provenance?: {
      provider: "openai" | "deepseek" | "openai_compatible";
      model_identifier: string;
      prompt_version: string;
      schema_version: "ProtocolDraftAdvice@1";
      llm_usage: {
        total?: {
          requests?: number;
          input_tokens?: number;
          output_tokens?: number;
          total_tokens?: number;
          cost_usd?: string | null;
          cost_status?: string;
        };
      };
      memory_context?: {
        retrieval_mode: "semantic_plus_recent" | "postgres_fallback" | "none";
        project_memory_ids: string[];
        profile_id?: string | null;
        profile_version?: number | null;
        policy_versions: Record<string, number>;
        policy_hashes: Record<string, string>;
        approved_protocol_hash?: string | null;
        ignored_memory_keys: string[];
        retrieval_error_type?: string | null;
      } | null;
    } | null;
  };
}

export interface ShortfallReport {
  target_count: number;
  strict_count: number;
  loss_funnel: Record<string, number>;
  allowed_actions: string[];
}

export interface ResearchRun {
  id: string;
  project_id: string;
  owner_id: string;
  organization_id: string | null;
  protocol_version_id: string;
  state: RunState;
  state_version: number;
  execution_mode: ResearchExecutionMode;
  protocol_hash: string;
  target_count: number;
  strict_count: number;
  candidate_count: number;
  analyzed_count: number;
  progress: Record<string, unknown>;
  shortage_report?: ShortfallReport;
  failed_code?: string;
  created_at: string;
  updated_at?: string;
}

export type ResearchOrganizationRole = "OWNER" | "MEMBER";

export interface ResearchOrganization {
  id: string;
  name: string;
  slug: string;
  created_by: string;
  current_user_role: ResearchOrganizationRole;
  created_at: string;
  updated_at?: string;
}

export interface ResearchOrganizationMember {
  organization_id: string;
  user_id: string;
  email: string;
  full_name?: string;
  role: ResearchOrganizationRole;
  created_at: string;
}

export interface ConstraintDecision {
  constraint_id: string;
  field: string;
  operator: string;
  decision: "PASS" | "FAIL" | "UNKNOWN";
  reason_code: string;
  expected_value: unknown;
  observed_value: unknown;
  evidence_reference?: string;
}

export interface Candidate {
  work_id: string;
  version_id?: string;
  title: string;
  authors: string[];
  document_type: string;
  venue?: string;
  effective_publication_date?: string;
  doi?: string;
  source_url?: string;
  hard_eligible?: boolean;
  hard_fail_count: number;
  hard_unknown_count: number;
  relevance_decision?: "PASS" | "FAIL" | "REVIEW";
  relevance_score?: number;
  relevance_reasons: string[];
  relevance_facet_judgement?: {
    centrality: "CENTRAL" | "SUPPORTING" | "INCIDENTAL" | "UNRELATED";
    score: number;
    exclusion_triggered: boolean;
    facets: Array<{
      facet_id: string;
      status: "SUPPORTED" | "NOT_SUPPORTED" | "UNCERTAIN";
      evidence_ids: string[];
      rationale: string;
    }>;
  };
  constraints: ConstraintDecision[];
}

export interface CandidatePage {
  items: Candidate[];
  total: number;
  skip: number;
  limit: number;
}

export interface EvidenceLocator {
  evidence_id: string;
  block_id: string;
  page_number?: number;
  section_path: string[];
  quote: string;
  quote_start: number;
  quote_end: number;
  block_text_sha256: string;
  document_sha256: string;
}

export interface FigureArtifact {
  figure_id: string;
  label: string;
  caption: string;
  page_number?: number;
  evidence_ids: string[];
  document_sha256: string;
}

export interface PaperDetail {
  candidate: Candidate;
  versions: Array<{
    id: string;
    source: string;
    source_id: string;
    version_type: string;
    doi?: string;
  }>;
  analysis?: {
    sections: Array<{
      section_id: string;
      status: string;
      summary: string;
      evidence_coverage: number;
    }>;
    figures: Array<{
      figure_id: string;
      caption_summary: string;
      numeric_source: string;
      extracted_values: string[];
    }>;
    audit: {
      evidence_coverage: number;
      contradicted_count: number;
      unsupported_count: number;
      requires_human_review: boolean;
    };
  };
  evidence: EvidenceLocator[];
  figures: FigureArtifact[];
  analysis_attempt?: number;
}

export type ResearchRelevanceFeedbackDecision = "INCLUDE" | "EXCLUDE" | "REVIEW";

export interface ResearchFeedbackAccepted {
  feedback_id: string;
  project_memory_id?: string | null;
}

export type ResearchMemoryType =
  "QUERY_TERM" | "EXCLUSION_DECISION" | "CORRECTION" | "DISPLAY_PREFERENCE" | "ARTIFACT_NOTE";

export interface ResearchProjectMemory {
  id: string;
  project_id: string;
  memory_type: ResearchMemoryType;
  content: Record<string, unknown>;
  source: "USER_FEEDBACK" | "APPROVED_PROTOCOL" | "VERIFIED_SYSTEM_EVENT";
  source_id: string;
  confidence: number;
  valid_from: string;
  valid_to?: string | null;
  supersedes?: string | null;
  created_by: string;
  created_at: string;
}

export interface ResearchProfile {
  id: string;
  user_id: string;
  version: number;
  preferences: Record<string, unknown>;
  confirmation_note: string;
  confirmed_at: string;
  created_at: string;
}

export interface ResearchSessionMemory {
  session_id: string;
  user_id: string;
  project_id?: string | null;
  active_run_id?: string | null;
  draft_slots: Record<string, unknown>;
  missing_slots: string[];
  source_message_ids: string[];
  updated_at: string;
  expires_in_seconds: number;
}

export interface ResearchPolicyVersion {
  id: string;
  policy_key: string;
  version: number;
  content: Record<string, unknown>;
  content_hash: string;
  valid_from: string;
  valid_to?: string | null;
  status: string;
  created_at: string;
}

export type GoldDatasetStatus = "DRAFT" | "EXTERNAL_BENCHMARK" | "ADJUDICATED";

export interface GoldPaperCase {
  case_id: string;
  title: string;
  doi?: string | null;
  relevant: boolean;
  relevance_grade?: number | null;
  expected_date?: string | null;
  expected_venue?: string | null;
  allowed_quote_sha256?: string[];
  expected_numeric_values?: string[];
}

export interface GoldDatasetProvenance {
  source_name: string;
  source_url: string;
  license: string;
  annotator_count: number;
  judgment_method: string;
  completed_at: string;
  domain_coverage: string[];
  language_coverage: string[];
  limitations?: string[];
}

export interface EvaluationDatasetCreateInput {
  project_id: string;
  name: string;
  version: string;
  description?: string;
  cases: GoldPaperCase[];
  observations?: Array<{
    source: string;
    source_id: string;
    expected_cluster_id: string;
  }>;
  status?: GoldDatasetStatus;
  provenance?: GoldDatasetProvenance | null;
}

export interface EvaluationDataset {
  id: string;
  project_id: string;
  name: string;
  version: string;
  description: string;
  payload_hash: string;
  case_count: number;
  status: GoldDatasetStatus;
  provenance?: GoldDatasetProvenance | null;
  created_by: string;
  created_at: string;
}

export type ResearchMetricSnapshotStatus = "ACTIVE" | "SUPERSEDED" | "REVOKED";

export interface ResearchMetricSnapshot {
  id: string;
  source_name: string;
  source_version: string;
  metric_names: string[];
  effective_from: string;
  effective_to?: string | null;
  license_reference: string;
  authorized_scope: string;
  license_attested: boolean;
  status: ResearchMetricSnapshotStatus;
  imported_by: string;
  imported_at: string;
  payload_sha256: string;
  object_key: string;
  created_at: string;
}

export interface ResearchArtifact {
  id: string;
  generation: number;
  format:
    | "markdown"
    | "opml"
    | "bibtex"
    | "jsonl"
    | "csv"
    | "exclusions_csv"
    | "venue_metrics_csv"
    | "manifest";
  filename: string;
  content_type: string;
  sha256: string;
  size_bytes: number;
}

export interface ArtifactRegenerationAccepted {
  task_execution_id: string;
  run_id: string;
  status: string;
  created: boolean;
}

export interface ResearchRunEvent {
  sequence: number;
  event_type: string;
  stage: string;
  occurred_at: string;
  payload: Record<string, unknown>;
}

export interface EvaluationMetric {
  value?: number;
  threshold: number;
  sample_size: number;
  status: "PASS" | "FAIL" | "NOT_EVALUATED";
}

export interface EvaluationReport {
  id: string;
  dataset_id: string;
  run_id: string;
  metrics: Record<string, EvaluationMetric>;
  passed: boolean;
  failures: string[];
  evaluated_at: string;
}
