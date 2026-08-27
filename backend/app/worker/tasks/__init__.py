"""Background tasks."""

from app.worker.tasks.literature_research_tasks import (
    analyze_research_paper,
    execute_research_stage,
    finalize_research_analysis,
    publish_research_outbox,
    recover_stalled_research_runs,
    regenerate_research_artifacts,
)
from app.worker.tasks.local_paper_library_tasks import (
    check_scheduled_local_paper_syncs,
    run_local_paper_analysis,
    poll_local_paper_analysis_stage,
    recover_local_paper_analysis_background,
    recover_local_paper_analysis_staged,
    sync_local_paper_library,
)
from app.worker.tasks.rag_tasks import (
    check_scheduled_syncs,
    ingest_document_task,
    sync_collection_task,
    sync_single_source_task,
)

__all__ = [
    "analyze_research_paper",
    "check_scheduled_local_paper_syncs",
    "check_scheduled_syncs",
    "execute_research_stage",
    "finalize_research_analysis",
    "ingest_document_task",
    "publish_research_outbox",
    "recover_stalled_research_runs",
    "regenerate_research_artifacts",
    "run_local_paper_analysis",
    "poll_local_paper_analysis_stage",
    "recover_local_paper_analysis_background",
    "recover_local_paper_analysis_staged",
    "sync_collection_task",
    "sync_local_paper_library",
    "sync_single_source_task",
]
