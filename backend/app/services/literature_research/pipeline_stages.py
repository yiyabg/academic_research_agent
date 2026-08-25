"""Concrete deterministic handlers for research workflow states."""

from collections.abc import Awaitable, Callable

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.literature_research import LiteratureResearchExperts
from app.clients.scholarly import UnpaywallClient
from app.clients.scholarly.base import ScholarlySourceError
from app.core.config import settings
from app.db.models.literature_research.discovery import ResearchWork, ResearchWorkVersion
from app.db.models.literature_research.protocol import ResearchProtocolVersion
from app.db.models.literature_research.run import ResearchRun
from app.domain.literature_research.normalization import normalize_doi
from app.repositories.literature_research import analysis as analysis_repository
from app.repositories.literature_research import discovery as discovery_repository
from app.repositories.literature_research import evidence as evidence_repository
from app.repositories.literature_research import quality as quality_repository
from app.repositories.literature_research import run as run_repository
from app.schemas.literature_research.analysis import (
    AuditedPaperAnalysis,
    PaperAnalysisTask,
    SynthesisOutput,
)
from app.schemas.literature_research.evidence import (
    AcquiredFullText,
    EvidenceLocator,
    FullTextAcquisitionDecision,
    FullTextCandidate,
    FullTextSource,
    LicenseDecision,
)
from app.schemas.literature_research.protocol import ResearchProtocol
from app.schemas.literature_research.release import (
    CanonicalResearchReport,
    CatalogPaper,
    CatalogResearchReport,
    ReleaseSnapshot,
    ReportPaper,
)
from app.schemas.literature_research.run import ExecutionMode, RunState
from app.services.literature_research.analysis_orchestrator import AnalysisOrchestrator
from app.services.literature_research.artifact_service import ArtifactService
from app.services.literature_research.audit_exports import (
    collect_exclusion_audit_rows,
    collect_metric_snapshot_audit_rows,
)
from app.services.literature_research.catalog_artifact_service import CatalogArtifactService
from app.services.literature_research.discovery import DiscoveryService
from app.services.literature_research.document_parser import ResearchDocumentParser
from app.services.literature_research.document_safety import UnsafeDocumentError
from app.services.literature_research.evidence_locator import locate_quote
from app.services.literature_research.figure_artifact_service import FigureArtifactService
from app.services.literature_research.fulltext_acquisition import FullTextAcquisitionService
from app.services.literature_research.fulltext_policy import LawfulFullTextPolicy
from app.services.literature_research.llm_usage import (
    aggregate_usage_snapshots,
    attach_usage,
    collect_llm_usage,
)
from app.services.literature_research.object_store import get_research_object_store
from app.services.literature_research.quality_evaluation import QualityEvaluationService
from app.services.literature_research.query_planner import QueryPlannerService
from app.services.literature_research.release_gate import evaluate_release
from app.services.literature_research.relevance import (
    CrossEncoderScoreModel,
    EmbeddingCosineScoreModel,
    RelevanceScoringService,
)
from app.services.literature_research.relevance_judge import (
    RELEVANCE_PROMPT_VERSION,
    RelevanceFacetJudge,
    RelevanceJudgementTask,
    apply_facet_judgement,
)
from app.services.literature_research.vector_index import (
    ResearchVectorIndex,
    get_research_embedding_provider,
)
from app.services.llm_provider import selected_llm_model_identifier, selected_llm_provider

StageHandler = Callable[[ResearchRun], Awaitable[dict[str, object]]]


class ResearchPipelineStages:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def protocol(self, run: ResearchRun) -> ResearchProtocol:
        row = await self.db.get(ResearchProtocolVersion, run.protocol_version_id)
        if row is None:
            raise RuntimeError("Approved protocol version disappeared")
        return ResearchProtocol.model_validate(row.protocol_json)

    def handlers(self) -> dict[RunState, StageHandler]:
        return {
            RunState.QUEUED: self.start,
            RunState.DISCOVERING: self.discover,
            RunState.NORMALIZING: self.normalized_checkpoint,
            RunState.ENRICHING_METRICS: self.enrich_metrics,
            RunState.DEDUPLICATING: self.deduplicated_checkpoint,
            RunState.HARD_FILTERING: self.hard_filter_checkpoint,
            RunState.RELEVANCE_SCORING: self.score_relevance,
            RunState.FULLTEXT_ACQUIRING: self.acquire_fulltext,
            RunState.PARSING: self.parse_fulltext,
            RunState.SELECTING: self.selection_checkpoint,
            RunState.ANALYZING: self.analyze,
            RunState.EVIDENCE_AUDITING: self.audit_checkpoint,
            RunState.SYNTHESIZING: self.synthesize,
            RunState.RENDERING: self.render,
            RunState.RELEASE_CHECKING: self.release_check,
        }

    async def start(self, run: ResearchRun) -> dict[str, object]:
        return {"run_id": str(run.id), "started": True}

    async def discover(self, run: ResearchRun) -> dict[str, object]:
        protocol = await self.protocol(run)
        service = DiscoveryService()
        try:
            outcome = await service.execute(
                self.db,
                organization_id=run.organization_id,
                project_id=run.project_id,
                run_id=run.id,
                plan=QueryPlannerService().plan(protocol),
            )
        finally:
            await service.aclose()
        await run_repository.set_counts_and_progress(
            self.db,
            run=run,
            candidate_count=outcome.work_count,
        )
        return outcome.model_dump(mode="json")

    async def normalized_checkpoint(self, run: ResearchRun) -> dict[str, object]:
        return {
            "canonical_work_count": run.candidate_count,
            "normalization_persisted": True,
        }

    async def enrich_metrics(self, run: ResearchRun) -> dict[str, object]:
        protocol = await self.protocol(run)
        outcome = await QualityEvaluationService(self.db).evaluate_run(
            run_id=run.id,
            protocol=protocol,
            protocol_hash=run.protocol_hash,
        )
        await run_repository.set_counts_and_progress(
            self.db,
            run=run,
            candidate_count=outcome.candidate_count,
            strict_count=outcome.eligible_count,
        )
        return outcome.model_dump(mode="json")

    async def deduplicated_checkpoint(self, run: ResearchRun) -> dict[str, object]:
        return {"canonical_work_count": run.candidate_count, "version_families_persisted": True}

    async def hard_filter_checkpoint(self, run: ResearchRun) -> dict[str, object]:
        return {
            "candidate_count": run.candidate_count,
            "hard_constraint_pass_count": run.strict_count,
            "constraint_ledger_persisted": True,
        }

    async def score_relevance(self, run: ResearchRun) -> dict[str, object]:
        protocol = await self.protocol(run)
        rows = await evidence_repository.list_eligible_work_documents(self.db, run_id=run.id)
        documents = [
            (
                work.id,
                "\n".join(value for value in (work.canonical_title, work.abstract or "") if value),
            )
            for work, _, _ in rows
        ]
        embedding, _, embedding_version = get_research_embedding_provider()
        scorer = RelevanceScoringService(
            semantic_model=EmbeddingCosineScoreModel(embedding, embedding_version),
            cross_encoder=CrossEncoderScoreModel(
                settings.CROSS_ENCODER_MODEL, str(settings.MODELS_CACHE_DIR)
            ),
        )
        scores = await scorer.score(
            query=protocol.topic,
            topic_model=protocol.topic_model,
            documents=documents,
        )
        relevance_usage: dict[str, object] | None = None
        if run.execution_mode == ExecutionMode.FULL_RESEARCH.value:
            work_by_id = {work.id: work for work, _, _ in rows}
            local_passes = [item for item in scores if item.decision.value == "PASS"]
            experts = LiteratureResearchExperts()
            with collect_llm_usage(protocol.llm_budget) as usage:
                try:
                    judgements = await RelevanceFacetJudge(experts.relevance).judge(
                        protocol=protocol,
                        tasks=[
                            RelevanceJudgementTask(
                                work_id=item.work_id,
                                title=work_by_id[item.work_id].canonical_title,
                                abstract=work_by_id[item.work_id].abstract or "",
                            )
                            for item in local_passes
                        ],
                    )
                except Exception as exc:
                    attach_usage(exc, usage.snapshot())
                    raise
            relevance_usage = usage.snapshot()
            judgement_by_work = {item.work_id: item for item in judgements}
            scores = [
                apply_facet_judgement(
                    score=item,
                    judgement=judgement_by_work[item.work_id],
                    protocol=protocol,
                    model_identifier=selected_llm_model_identifier(),
                )
                if item.work_id in judgement_by_work
                else item
                for item in scores
            ]
        await evidence_repository.persist_relevance(self.db, run_id=run.id, scores=scores)
        passed = sum(item.decision.value == "PASS" for item in scores)
        progress: dict[str, object] = {}
        if relevance_usage is not None:
            progress = {
                "relevance_llm_usage": relevance_usage,
                "relevance_llm_model_identifier": selected_llm_model_identifier(),
                "relevance_prompt_version": RELEVANCE_PROMPT_VERSION,
            }
        await run_repository.set_counts_and_progress(
            self.db, run=run, strict_count=passed, progress=progress
        )
        result: dict[str, object] = {
            "scored_count": len(scores),
            "relevance_pass_count": passed,
            "model_versions": {
                "embedding": embedding_version,
                "cross_encoder": settings.CROSS_ENCODER_MODEL,
            },
        }
        if relevance_usage is not None:
            result["relevance_llm_usage"] = relevance_usage
            result["facet_judged_count"] = len(judgements)
        return result

    async def acquire_fulltext(self, run: ResearchRun) -> dict[str, object]:
        rows = await evidence_repository.list_relevant_versions(self.db, run_id=run.id)
        rows = rows[: settings.RESEARCH_DISCOVERY_DOI_CANDIDATE_LIMIT]
        resolver = UnpaywallClient()
        acquisition = FullTextAcquisitionService()
        acquired_count = 0
        unpaywall_cache: dict[str, FullTextCandidate | None] = {}
        unpaywall_failed_dois: set[str] = set()
        unpaywall_lookup_count = 0
        unpaywall_failure_count = 0
        fulltext_fetch_failure_count = 0
        try:
            for _, version in rows:
                candidates: list[FullTextCandidate] = []
                if version.source == "arxiv" and version.open_access_pdf_url:
                    candidates.append(
                        FullTextCandidate(
                            version_id=version.id,
                            source=FullTextSource.ARXIV,
                            url=version.open_access_pdf_url,
                            license_decision=LicenseDecision.ALLOWED,
                            license_reference=f"arxiv-record:{version.arxiv_id or version.source_id}",
                            content_type="application/pdf",
                            is_open_access=True,
                        )
                    )
                normalized_doi = normalize_doi(version.doi)
                if normalized_doi and settings.CROSSREF_MAILTO:
                    if normalized_doi not in unpaywall_cache:
                        unpaywall_lookup_count += 1
                        try:
                            unpaywall_cache[normalized_doi] = await resolver.candidate(
                                version_id=version.id, doi=normalized_doi
                            )
                        except (ScholarlySourceError, ValueError):
                            # One unavailable or malformed DOI record must not
                            # discard lawful full texts already found for the run.
                            unpaywall_cache[normalized_doi] = None
                            unpaywall_failed_dois.add(normalized_doi)
                            unpaywall_failure_count += 1
                    candidate = unpaywall_cache[normalized_doi]
                    if candidate is not None:
                        candidates.append(
                            candidate.model_copy(update={"version_id": version.id})
                        )
                if not candidates:
                    decision = FullTextAcquisitionDecision(
                        version_id=version.id,
                        allowed=False,
                        reason_code=(
                            "UNPAYWALL_LOOKUP_FAILED"
                            if normalized_doi in unpaywall_failed_dois
                            else "NO_FULLTEXT_CANDIDATE"
                        ),
                    )
                    acquired = None
                else:
                    decision = LawfulFullTextPolicy().select(candidates)
                    try:
                        acquired = (
                            await acquisition.acquire(
                                decision,
                                organization_id=run.organization_id,
                                project_id=run.project_id,
                                run_id=run.id,
                            )
                            if decision.allowed
                            else None
                        )
                    except UnsafeDocumentError as exc:
                        rejected_decision = decision.model_copy(
                            update={"allowed": False, "reason_code": "MALWARE_DETECTED"}
                        )
                        await evidence_repository.persist_acquisition(
                            self.db,
                            run_id=run.id,
                            decision=rejected_decision,
                            acquired=None,
                            scan_status=exc.result.status,
                            scan_engine=exc.result.engine,
                            scan_signature=exc.result.signature,
                            resolved_ips=exc.resolved_ips,
                            redirect_chain=exc.redirect_chain,
                        )
                        continue
                    except (httpx.HTTPError, ValueError):
                        # Repository links routinely expire, reject bots, or
                        # return mislabeled content. Record the per-paper
                        # rejection and continue with the bounded DOI batch.
                        rejected_decision = decision.model_copy(
                            update={
                                "allowed": False,
                                "reason_code": "FULLTEXT_FETCH_OR_VALIDATION_FAILED",
                            }
                        )
                        await evidence_repository.persist_acquisition(
                            self.db,
                            run_id=run.id,
                            decision=rejected_decision,
                            acquired=None,
                        )
                        fulltext_fetch_failure_count += 1
                        continue
                await evidence_repository.persist_acquisition(
                    self.db,
                    run_id=run.id,
                    decision=decision,
                    acquired=acquired,
                )
                acquired_count += int(acquired is not None)
        finally:
            await resolver.aclose()
            await acquisition.aclose()
        return {
            "requested_count": len(rows),
            "unique_doi_lookup_count": unpaywall_lookup_count,
            "unpaywall_failure_count": unpaywall_failure_count,
            "fulltext_fetch_failure_count": fulltext_fetch_failure_count,
            "acquired_count": acquired_count,
        }

    async def parse_fulltext(self, run: ResearchRun) -> dict[str, object]:
        rows = await evidence_repository.list_acquired_fulltexts(self.db, run_id=run.id)
        store = get_research_object_store()
        parser = ResearchDocumentParser(store)
        vector_index = ResearchVectorIndex()
        block_count = 0
        pass_count = 0
        low_confidence_count = 0
        ocr_page_count = 0
        for acquisition, work in rows:
            object_key = acquisition.object_key
            if object_key is None:
                raise RuntimeError("Acquired full-text row is missing its object key")
            payload = await store.get(object_key)
            content_type = acquisition.content_type or (
                "application/pdf" if object_key.endswith(".pdf") else "text/html"
            )
            acquired = AcquiredFullText(
                version_id=acquisition.version_id,
                source=FullTextSource(acquisition.source),
                url=acquisition.url,
                license_reference=acquisition.license_reference or "",
                content_type=content_type,
                size_bytes=len(payload),
                object_key=object_key,
                document_sha256=acquisition.document_sha256 or "",
                resolved_ips=acquisition.resolved_ips_json,
                redirect_chain=acquisition.redirect_chain_json,
                malware_scan_status=acquisition.malware_scan_status,
                malware_scan_engine=acquisition.malware_scan_engine,
                malware_signature=acquisition.malware_signature,
            )
            parsed = await parser.parse_with_quality(acquired)
            blocks = parsed.blocks
            await evidence_repository.persist_blocks(
                self.db,
                run_id=run.id,
                version_id=acquisition.version_id,
                blocks=blocks,
            )
            await evidence_repository.persist_parsing_result(
                self.db,
                run_id=run.id,
                version_id=acquisition.version_id,
                quality=parsed.quality,
            )
            block_count += len(blocks)
            ocr_page_count += parsed.quality.ocr_page_count
            if parsed.quality.status != "PASSED":
                low_confidence_count += 1
                continue
            pass_count += 1
            for block in blocks:
                quote = block.text[:4000]
                locator = locate_quote(
                    work_id=work.id,
                    version_id=acquisition.version_id,
                    block=block,
                    quote=quote,
                    document_sha256=acquisition.document_sha256 or "",
                )
                await evidence_repository.persist_evidence(self.db, run_id=run.id, locator=locator)
            await vector_index.upsert_blocks(
                organization_id=run.organization_id,
                project_id=run.project_id,
                run_id=run.id,
                work_id=work.id,
                version_id=acquisition.version_id,
                blocks=blocks,
            )
        return {
            "parsed_document_count": len(rows),
            "parsed_block_count": block_count,
            "parsing_pass_count": pass_count,
            "parsing_low_confidence_count": low_confidence_count,
            "ocr_page_count": ocr_page_count,
        }

    async def selection_checkpoint(self, run: ResearchRun) -> dict[str, object]:
        catalog_mode = run.execution_mode == ExecutionMode.SEARCH_ONLY.value
        rows = (
            await evidence_repository.list_catalog_report_rows(self.db, run_id=run.id)
            if catalog_mode
            else await evidence_repository.list_analysis_ready_versions(self.db, run_id=run.id)
        )
        selected_rows = rows[: run.target_count] if catalog_mode else rows
        selection: list[dict[str, object]] = []
        if catalog_mode:
            for rank, (work, version, _venue, relevance) in enumerate(selected_rows, start=1):
                score = next(
                    (
                        value
                        for value in (
                            relevance.cross_encoder_score,
                            relevance.semantic_score,
                            relevance.lexical_score,
                        )
                        if value is not None
                    ),
                    0.0,
                )
                selection.append(
                    {
                        "rank": rank,
                        "work_id": str(work.id),
                        "version_id": str(version.id),
                        "relevance_score": score,
                    }
                )
        await run_repository.set_counts_and_progress(
            self.db,
            run=run,
            strict_count=len(selected_rows),
            progress=(
                {
                    "catalog_scope": "metadata_only",
                    "catalog_eligible_count": len(rows),
                    "catalog_selection": selection,
                }
                if catalog_mode
                else None
            ),
        )
        query_count = int(run.progress_json.get("query_count", 0))
        successful = int(run.progress_json.get("successful_query_count", 0))
        exhausted = int(run.progress_json.get("exhausted_query_count", 0))
        result: dict[str, object] = {
            "strict_count": len(selected_rows),
            "target_count": run.target_count,
            "all_query_families_executed": query_count > 0 and successful == query_count,
            "all_sources_paginated_to_stop_rule": query_count > 0 and exhausted == query_count,
        }
        if catalog_mode:
            result["catalog_eligible_count"] = len(rows)
            result["catalog_selected_count"] = len(selected_rows)
        return result

    async def render_catalog_generation(
        self, run: ResearchRun, *, generation: int
    ) -> dict[str, object]:
        """Render the frozen search-only selection without implying PDF analysis."""
        raw_selection = run.progress_json.get("catalog_selection")
        if not isinstance(raw_selection, list):
            raise RuntimeError("Search-only run is missing its frozen catalog selection")
        rows = await evidence_repository.list_catalog_report_rows(self.db, run_id=run.id)
        by_identity = {(str(work.id), str(version.id)): (work, version, venue, relevance)
                       for work, version, venue, relevance in rows}
        papers: list[CatalogPaper] = []
        for item in raw_selection:
            if not isinstance(item, dict):
                raise RuntimeError("Search-only catalog selection contains an invalid entry")
            work_id = item.get("work_id")
            version_id = item.get("version_id")
            rank = item.get("rank")
            score = item.get("relevance_score")
            if not isinstance(work_id, str) or not isinstance(version_id, str):
                raise RuntimeError("Search-only catalog selection is missing work identity")
            if not isinstance(rank, int) or not isinstance(score, (float, int)):
                raise RuntimeError("Search-only catalog selection is missing rank or score")
            row = by_identity.get((work_id, version_id))
            if row is None:
                raise RuntimeError("Frozen catalog selection no longer satisfies strict metadata gates")
            work, version, venue, _relevance = row
            papers.append(
                CatalogPaper(
                    work_id=work.id,
                    version_id=version.id,
                    rank=rank,
                    title=work.canonical_title,
                    authors=[str(author.get("name", "")) for author in work.authors_json],
                    year=(
                        version.effective_publication_date.year
                        if version.effective_publication_date
                        else None
                    ),
                    doi=version.doi,
                    source_url=version.canonical_url,
                    document_type=work.document_type,
                    venue=venue.name if venue else None,
                    relevance_score=float(score),
                )
            )
        report = CatalogResearchReport(
            run_id=run.id,
            project_id=run.project_id,
            protocol_hash=run.protocol_hash,
            title=(await self.protocol(run)).topic,
            target_count=run.target_count,
            strict_count=len(papers),
            shortfall_disclosed=len(papers) < run.target_count,
            papers=papers,
        )
        artifacts = await CatalogArtifactService(self.db).render_all(
            report,
            organization_id=run.organization_id,
            generation=generation,
        )
        errors = await CatalogArtifactService(self.db).validate_persisted(
            run.id, generation=generation
        )
        if errors:
            raise RuntimeError(f"Catalog artifact persistence validation failed: {'; '.join(errors)}")
        return {
            "artifact_count": len(artifacts),
            "catalog_artifact_count": len(artifacts),
            "catalog_scope": "metadata_only",
            "rendered_paper_count": len(papers),
            "output_generation": generation,
        }

    @staticmethod
    def _orchestrator(experts: LiteratureResearchExperts) -> AnalysisOrchestrator:
        return AnalysisOrchestrator(
            analysis_expert=experts.analysis,
            figure_expert=experts.figure,
            audit_expert=experts.audit,
            synthesis_expert=experts.synthesis,
        )

    async def _build_analysis_task(
        self,
        *,
        run: ResearchRun,
        protocol: ResearchProtocol,
        work: ResearchWork,
        version: ResearchWorkVersion,
    ) -> PaperAnalysisTask:
        evidence_rows = await evidence_repository.list_evidence(
            self.db, run_id=run.id, work_id=work.id
        )
        if not evidence_rows:
            raise RuntimeError(f"Analysis-ready work {work.id} has no parsed full-text evidence")
        evidence = [EvidenceLocator.model_validate(item) for item in evidence_rows]
        acquisition = await evidence_repository.get_clean_acquisition(
            self.db, run_id=run.id, version_id=version.id
        )
        if acquisition is None or acquisition.object_key is None:
            raise RuntimeError(f"Analysis-ready work {work.id} lost its clean full text")
        store = get_research_object_store()
        payload = await store.get(acquisition.object_key)
        acquired = AcquiredFullText(
            version_id=acquisition.version_id,
            source=FullTextSource(acquisition.source),
            url=acquisition.url,
            license_reference=acquisition.license_reference or "",
            content_type=acquisition.content_type or "application/pdf",
            size_bytes=len(payload),
            object_key=acquisition.object_key,
            document_sha256=acquisition.document_sha256 or "",
            resolved_ips=acquisition.resolved_ips_json,
            redirect_chain=acquisition.redirect_chain_json,
            malware_scan_status=acquisition.malware_scan_status,
            malware_scan_engine=acquisition.malware_scan_engine,
            malware_signature=acquisition.malware_signature,
        )
        figures = await FigureArtifactService(store).extract(
            work_id=work.id,
            acquired=acquired,
            evidence=evidence,
        )
        await evidence_repository.persist_figure_artifacts(
            self.db,
            run_id=run.id,
            work_id=work.id,
            version_id=version.id,
            artifacts=figures,
        )
        return PaperAnalysisTask(
            work_id=work.id,
            metadata={
                "title": work.canonical_title,
                "document_type": work.document_type,
                "doi": version.doi,
            },
            evidence=[
                {
                    "evidence_id": item.evidence_id,
                    "quote": item.quote,
                    "page_number": item.page_number,
                    "section_path": item.section_path,
                    "bbox": item.bbox,
                    "extraction_method": item.extraction_method,
                }
                for item in evidence
            ],
            section_ids=[item.section_id for item in protocol.analysis_template],
            figures=figures,
        )

    async def analyze_work_initial(
        self, *, run: ResearchRun, work: ResearchWork, version: ResearchWorkVersion
    ) -> dict[str, object]:
        """Analyze and persist exactly one initial paper shard."""
        protocol = await self.protocol(run)
        task = await self._build_analysis_task(
            run=run,
            protocol=protocol,
            work=work,
            version=version,
        )
        experts = LiteratureResearchExperts()
        with collect_llm_usage(protocol.llm_budget) as usage:
            try:
                analysis = (await self._orchestrator(experts).analyze_papers([task]))[0]
            except Exception as exc:
                attach_usage(exc, usage.snapshot())
                raise
        row = await analysis_repository.persist_analysis(
            self.db,
            run_id=run.id,
            analysis=analysis,
            model_versions={
                "analysis": selected_llm_model_identifier(),
                "audit": selected_llm_model_identifier(),
            },
        )
        return {
            "analysis_id": str(row.id),
            "work_id": str(work.id),
            "provider": selected_llm_provider(),
            "model": settings.AI_MODEL,
            "llm_usage": usage.snapshot(),
        }

    async def analyze(self, run: ResearchRun) -> dict[str, object]:
        """Evaluate the durable shard barrier; never call an expert in the coordinator."""
        counts = await analysis_repository.summarize_initial_analysis_tasks(
            self.db, run_id=run.id
        )
        terminal = counts["succeeded"] + counts["failed_terminal"] + counts["blocked"]
        if terminal != counts["total"]:
            raise RuntimeError("Paper-analysis barrier is not complete")
        tasks = await analysis_repository.list_initial_analysis_tasks(self.db, run_id=run.id)
        snapshots: list[dict[str, object]] = []
        for task in tasks:
            output_usage = (
                task.output_json.get("llm_usage")
                if isinstance(task.output_json, dict)
                else None
            )
            if isinstance(output_usage, dict):
                snapshots.append(output_usage)
            if not isinstance(task.error_json, dict):
                continue
            history = task.error_json.get("attempt_history", [])
            if not isinstance(history, list):
                continue
            for attempt in history:
                if isinstance(attempt, dict) and isinstance(attempt.get("llm_usage"), dict):
                    snapshots.append(attempt["llm_usage"])
        analysis_llm_usage = aggregate_usage_snapshots(snapshots)
        await run_repository.set_counts_and_progress(
            self.db,
            run=run,
            analyzed_count=counts["succeeded"],
            progress={
                "analysis_shards_succeeded": counts["succeeded"],
                "analysis_shards_failed_terminal": counts["failed_terminal"],
                "analysis_shards_blocked": counts["blocked"],
                "analysis_llm_usage": analysis_llm_usage,
            },
        )
        return {
            "analysis_shard_total": counts["total"],
            "analyzed_count": counts["succeeded"],
            "analysis_shards_succeeded": counts["succeeded"],
            "analysis_shards_failed_terminal": counts["failed_terminal"],
            "analysis_shards_blocked": counts["blocked"],
            "analysis_failed_terminal_count": counts["failed_terminal"],
            "analysis_blocked_count": counts["blocked"],
            "analysis_barrier_complete": True,
            "analysis_llm_usage": analysis_llm_usage,
            "provider": selected_llm_provider(),
            "model": settings.AI_MODEL,
        }

    async def reanalyze_work(
        self, *, run: ResearchRun, work: ResearchWork, version: ResearchWorkVersion
    ) -> dict[str, object]:
        """Create an immutable new attempt for exactly one already-selected paper."""
        protocol = await self.protocol(run)
        task = await self._build_analysis_task(
            run=run, protocol=protocol, work=work, version=version
        )
        with collect_llm_usage(protocol.llm_budget) as usage:
            try:
                result = (
                    await self._orchestrator(LiteratureResearchExperts()).analyze_papers([task])
                )[0]
            except Exception as exc:
                attach_usage(exc, usage.snapshot())
                raise
        previous = await analysis_repository.get_latest_analysis(
            self.db, run_id=run.id, work_id=work.id
        )
        attempt = (previous.attempt if previous else 0) + 1
        row = await analysis_repository.persist_analysis(
            self.db,
            run_id=run.id,
            analysis=result,
            model_versions={
                "analysis": selected_llm_model_identifier(),
                "audit": selected_llm_model_identifier(),
            },
            attempt=attempt,
            trigger="USER_REANALYSIS",
            requested_by=run.owner_id,
            supersedes_analysis_id=previous.id if previous else None,
        )
        await run_repository.set_counts_and_progress(
            self.db,
            run=run,
            progress={
                "latest_reanalysis_work_id": str(work.id),
                "latest_reanalysis_attempt": attempt,
                "artifacts_require_regeneration": True,
            },
        )
        return {
            "analysis_id": str(row.id),
            "work_id": str(work.id),
            "attempt": attempt,
            "llm_usage": usage.snapshot(),
        }

    async def audit_checkpoint(self, run: ResearchRun) -> dict[str, object]:
        analyses = await analysis_repository.list_analyses(self.db, run_id=run.id)
        return {
            "audited_count": len(analyses),
            "contradicted_claim_count": sum(item.contradicted_count for item in analyses),
            "unsupported_claim_count": sum(item.unsupported_count for item in analyses),
        }

    async def synthesize_generation(
        self, run: ResearchRun, *, generation: int
    ) -> dict[str, object]:
        protocol = await self.protocol(run)
        rows = await analysis_repository.list_analyses(self.db, run_id=run.id)
        analyses = [AuditedPaperAnalysis.model_validate(item.analysis_json) for item in rows]
        experts = LiteratureResearchExperts()
        with collect_llm_usage(protocol.llm_budget) as usage:
            try:
                synthesis = await self._orchestrator(experts).synthesize(analyses)
            except Exception as exc:
                attach_usage(exc, usage.snapshot())
                raise
        synthesis_usage = usage.snapshot()
        await analysis_repository.persist_synthesis(
            self.db,
            run_id=run.id,
            synthesis=synthesis,
            model_version=selected_llm_model_identifier(),
            generation=generation,
        )
        await run_repository.set_counts_and_progress(
            self.db,
            run=run,
            progress={"synthesis_llm_usage": synthesis_usage},
        )
        return {
            "synthesized_work_count": len(synthesis.included_work_ids),
            "output_generation": generation,
            "synthesis_llm_usage": synthesis_usage,
        }

    async def synthesize(self, run: ResearchRun) -> dict[str, object]:
        return await self.synthesize_generation(run, generation=1)

    async def render_generation(self, run: ResearchRun, *, generation: int) -> dict[str, object]:
        synthesis_row = await analysis_repository.get_synthesis(
            self.db, run_id=run.id, generation=generation
        )
        if synthesis_row is None:
            raise RuntimeError("Cannot render before synthesis is persisted")
        synthesis = SynthesisOutput.model_validate(synthesis_row.synthesis_json)
        analysis_rows = await analysis_repository.list_analyses(self.db, run_id=run.id)
        analyses = {
            item.work_id: AuditedPaperAnalysis.model_validate(item.analysis_json)
            for item in analysis_rows
        }
        report_rows = await evidence_repository.list_relevant_report_rows(self.db, run_id=run.id)
        papers = []
        for work, version, venue, relevance in report_rows:
            analysis = analyses.get(work.id)
            if analysis is None:
                continue
            papers.append(
                ReportPaper(
                    work_id=work.id,
                    version_id=version.id,
                    title=work.canonical_title,
                    authors=[str(item.get("name", "")) for item in work.authors_json],
                    year=(
                        version.effective_publication_date.year
                        if version.effective_publication_date
                        else None
                    ),
                    doi=version.doi,
                    source_url=version.canonical_url,
                    document_type=work.document_type,
                    venue=venue.name if venue else None,
                    relevance_score=(relevance.cross_encoder_score or relevance.lexical_score),
                    hard_constraints_passed=True,
                    analysis=analysis,
                )
            )
        report = CanonicalResearchReport(
            run_id=run.id,
            project_id=run.project_id,
            protocol_hash=run.protocol_hash,
            title=(await self.protocol(run)).topic,
            target_count=run.target_count,
            strict_count=len(papers),
            shortfall_disclosed=len(papers) < run.target_count,
            synthesis=synthesis,
            papers=papers,
        )
        source_snapshot_hashes = await discovery_repository.list_source_snapshot_hashes(
            self.db, run_id=run.id
        )
        metric_snapshot_ids = await quality_repository.list_used_metric_snapshot_ids(
            self.db, run_id=run.id
        )
        included_work_ids = {paper.work_id for paper in papers}
        exclusion_rows = await collect_exclusion_audit_rows(
            self.db,
            run_id=run.id,
            included_work_ids=included_work_ids,
        )
        metric_snapshot_rows = await collect_metric_snapshot_audit_rows(
            self.db, run_id=run.id
        )
        artifacts = await ArtifactService(self.db).render_all(
            report,
            organization_id=run.organization_id,
            source_snapshot_hashes=source_snapshot_hashes,
            metric_snapshot_ids=metric_snapshot_ids,
            exclusion_rows=exclusion_rows,
            metric_snapshot_rows=metric_snapshot_rows,
            model_versions={
                "relevance": str(
                    run.progress_json.get("relevance_llm_model_identifier") or "not-used"
                ),
                "analysis": selected_llm_model_identifier(),
                "synthesis": selected_llm_model_identifier(),
            },
            llm_usage=aggregate_usage_snapshots(
                [
                    item
                    for item in (
                        run.progress_json.get("relevance_llm_usage"),
                        run.progress_json.get("analysis_llm_usage"),
                        run.progress_json.get("synthesis_llm_usage"),
                    )
                    if isinstance(item, dict)
                ]
            ),
            generation=generation,
        )
        await run_repository.set_counts_and_progress(self.db, run=run, strict_count=len(papers))
        return {
            "artifact_count": len(artifacts),
            "rendered_paper_count": len(papers),
            "output_generation": generation,
        }

    async def render(self, run: ResearchRun) -> dict[str, object]:
        if run.execution_mode == ExecutionMode.SEARCH_ONLY.value:
            return await self.render_catalog_generation(run, generation=1)
        return await self.render_generation(run, generation=1)

    async def release_check(
        self, run: ResearchRun, *, generation: int | None = None
    ) -> dict[str, object]:
        protocol_row = await self.db.get(ResearchProtocolVersion, run.protocol_version_id)
        if protocol_row is None:
            raise RuntimeError("Approved protocol version disappeared")
        analyses = await analysis_repository.list_analyses(self.db, run_id=run.id)
        analysis_work_ids = {item.work_id for item in analyses}
        report_rows = await evidence_repository.list_relevant_report_rows(self.db, run_id=run.id)
        relevance_scores = [
            (
                item.cross_encoder_score
                if item.cross_encoder_score is not None
                else item.lexical_score
            )
            for work, *_, item in report_rows
            if work.id in analysis_work_ids
        ]
        if generation is None:
            synthesis = await analysis_repository.get_synthesis(self.db, run_id=run.id)
            if synthesis is None:
                raise RuntimeError("Cannot release before synthesis is persisted")
            generation = synthesis.generation
        artifact_errors = await ArtifactService(self.db).validate_persisted(
            run.id, generation=generation
        )
        snapshot = ReleaseSnapshot(
            protocol_hash=run.protocol_hash,
            approved_protocol_hash=protocol_row.protocol_hash,
            constraint_violation_count=await analysis_repository.count_ineligible_analyses(
                self.db, run_id=run.id
            ),
            duplicate_cluster_conflicts=await analysis_repository.count_duplicate_conflicts(
                self.db, run_id=run.id
            ),
            min_relevance_score=min(relevance_scores, default=1.0),
            min_evidence_coverage=min((item.evidence_coverage for item in analyses), default=1.0),
            contradicted_claim_count=sum(item.contradicted_count for item in analyses),
            unsupported_claim_count=sum(item.unsupported_count for item in analyses),
            artifact_validation_errors=artifact_errors,
            document_safety_failure_count=(
                await evidence_repository.count_unsafe_or_unscanned_fulltexts(
                    self.db, run_id=run.id, work_ids=analysis_work_ids
                )
            ),
            figure_audit_failure_count=(
                await evidence_repository.count_incomplete_figure_artifacts(
                    self.db, run_id=run.id, work_ids=analysis_work_ids
                )
            ),
            target_count=run.target_count,
            strict_count=run.strict_count,
            shortfall_disclosed=run.strict_count < run.target_count,
        )
        decision = evaluate_release(snapshot)
        await analysis_repository.persist_release_check(
            self.db,
            run_id=run.id,
            snapshot=snapshot,
            decision=decision,
            generation=generation,
        )
        return {
            "release_allowed": decision.allowed,
            "release_partial": decision.partial,
            "release_blockers": [item.value for item in decision.blockers],
            "output_generation": generation,
        }

    async def regenerate_outputs(self, run: ResearchRun) -> dict[str, object]:
        """Create a new immutable synthesis/artifact generation after reanalysis."""
        generation = await analysis_repository.next_output_generation(self.db, run_id=run.id)
        synthesis = await self.synthesize_generation(run, generation=generation)
        rendered = await self.render_generation(run, generation=generation)
        released = await self.release_check(run, generation=generation)
        await run_repository.set_counts_and_progress(
            self.db,
            run=run,
            progress={
                "latest_output_generation": generation,
                "artifacts_require_regeneration": not bool(released["release_allowed"]),
            },
        )
        return {**synthesis, **rendered, **released}
