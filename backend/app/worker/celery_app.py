"""Celery application configuration."""

from celery import Celery

from app.core.config import settings

celery_app = Celery(
    "academic_research_agent",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Asia/Shanghai",
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    result_expires=3600,
    worker_prefetch_multiplier=1,
    worker_concurrency=4,
    task_routes={
        "app.worker.tasks.literature_research_tasks.publish_research_outbox": {
            "queue": "research-io"
        },
        "app.worker.tasks.literature_research_tasks.recover_stalled_research_runs": {
            "queue": "research-io"
        },
        "app.worker.tasks.literature_research_tasks.analyze_research_paper": {
            "queue": "paper-analysis"
        },
        "app.worker.tasks.rag_tasks.check_scheduled_syncs": {"queue": "research-io"},
        "app.worker.tasks.rag_tasks.sync_collection_task": {"queue": "research-io"},
        "app.worker.tasks.rag_tasks.sync_single_source_task": {"queue": "research-io"},
        "app.worker.tasks.rag_tasks.ingest_document_task": {"queue": "research-cpu"},
        "app.worker.tasks.local_paper_library_tasks.sync_local_paper_library": {
            "queue": "research-cpu"
        },
        "app.worker.tasks.local_paper_library_tasks.run_local_paper_analysis": {
            "queue": "research-llm"
        },
        "app.worker.tasks.local_paper_library_tasks.poll_local_paper_analysis_stage": {
            "queue": "research-llm"
        },
        "app.worker.tasks.local_paper_library_tasks.recover_local_paper_analysis_staged": {
            "queue": "research-llm"
        },
        "app.worker.tasks.local_paper_library_tasks.recover_local_paper_analysis_background": {
            "queue": "research-llm"
        },
        "app.worker.tasks.local_paper_library_tasks.check_scheduled_local_paper_syncs": {
            "queue": "research-cpu"
        },
    },
)

celery_app.autodiscover_tasks(["app.worker.tasks"])

celery_app.conf.beat_schedule = {
    "rag-sync-check": {
        "task": "app.worker.tasks.rag_tasks.check_scheduled_syncs",
        "schedule": 60.0,
    },
    "publish-research-outbox": {
        "task": "app.worker.tasks.literature_research_tasks.publish_research_outbox",
        "schedule": 1.0,
    },
    "recover-stalled-research-runs": {
        "task": "app.worker.tasks.literature_research_tasks.recover_stalled_research_runs",
        "schedule": 60.0,
    },
    "local-paper-incremental-sync": {
        "task": "app.worker.tasks.local_paper_library_tasks.check_scheduled_local_paper_syncs",
        "schedule": settings.LOCAL_PAPER_SYNC_INTERVAL_SECONDS,
    },
    "recover-local-paper-background-analysis": {
        "task": "app.worker.tasks.local_paper_library_tasks.recover_local_paper_analysis_background",
        "schedule": 60.0,
    },
    "recover-local-paper-staged-analysis": {
        "task": "app.worker.tasks.local_paper_library_tasks.recover_local_paper_analysis_staged",
        "schedule": 60.0,
    },
}
