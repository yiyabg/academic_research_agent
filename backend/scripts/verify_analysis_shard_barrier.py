"""Verify the paper-analysis barrier against the deployed PostgreSQL schema.

The probe uses an existing non-analysis run inside one explicit transaction and
always rolls the transaction back, so no fixture rows or state changes survive.
"""

import argparse
import asyncio
import json
from uuid import uuid4

from sqlalchemy import exists, select

from app.db.models.literature_research.project import ResearchProject
from app.db.models.literature_research.protocol import ResearchProtocolVersion
from app.db.models.literature_research.run import ResearchRun, ResearchTaskExecution
from app.db.models.user import User
from app.db.session import async_session_maker
from app.schemas.literature_research.run import RunState
from app.services.literature_research.pipeline_stages import ResearchPipelineStages
from app.services.literature_research.workflow import ResearchWorkflowService


async def verify() -> dict[str, object]:
    async with async_session_maker() as db:
        transaction = await db.begin()
        try:
            run = await db.scalar(
                select(ResearchRun)
                .where(
                    ~exists().where(
                        ResearchTaskExecution.run_id == ResearchRun.id,
                        ResearchTaskExecution.stage == "ANALYZE_PAPER",
                    )
                )
                .order_by(ResearchRun.created_at.asc())
                .limit(1)
                .with_for_update()
            )
            fixture_created = run is None
            if run is None:
                suffix = uuid4().hex
                user = User(email=f"rollback-probe-{suffix}@example.invalid")
                db.add(user)
                await db.flush()
                project = ResearchProject(
                    owner_id=user.id,
                    title="Rollback-only analysis shard probe",
                    description="Never committed",
                )
                db.add(project)
                await db.flush()
                protocol = ResearchProtocolVersion(
                    project_id=project.id,
                    version=1,
                    protocol_json={},
                    protocol_hash=f"sha256:{uuid4().hex}{uuid4().hex}",
                    status="APPROVED",
                    approved_by=user.id,
                )
                db.add(protocol)
                await db.flush()
                run = ResearchRun(
                    project_id=project.id,
                    protocol_version_id=protocol.id,
                    owner_id=user.id,
                    state=RunState.ANALYZING.value,
                    execution_mode="full_research",
                    client_request_id=f"rollback-probe-{suffix}",
                    protocol_hash=protocol.protocol_hash,
                    target_count=3,
                    strict_count=3,
                    progress_json={"stage": RunState.ANALYZING.value},
                )
                db.add(run)
                await db.flush()

            original_state = run.state
            original_version = run.state_version
            run.state = RunState.ANALYZING.value
            run.finished_at = None
            run.progress_json = {**run.progress_json, "stage": RunState.ANALYZING.value}
            statuses = ["SUCCEEDED", "FAILED_TERMINAL", "PENDING"]
            shards = []
            for index, status in enumerate(statuses, start=1):
                shard = ResearchTaskExecution(
                    run_id=run.id,
                    stage="ANALYZE_PAPER",
                    shard_key=f"rollback-probe-{uuid4()}",
                    input_hash=f"sha256:{uuid4().hex}{uuid4().hex}",
                    status=status,
                    attempt_count=index,
                )
                db.add(shard)
                shards.append(shard)
            await db.flush()

            stages = ResearchPipelineStages(db)
            incomplete_blocked = False
            try:
                await stages.analyze(run)
            except RuntimeError as exc:
                incomplete_blocked = "barrier is not complete" in str(exc)
            if not incomplete_blocked:
                raise AssertionError("A PENDING paper shard did not block the analysis barrier")

            shards[-1].status = "FAILED_TERMINAL"
            await db.flush()
            transitioned = await ResearchWorkflowService(
                db,
                stage_handlers=stages.handlers(),
            ).execute_stage(run.id, RunState.ANALYZING)
            if transitioned.state != RunState.EVIDENCE_AUDITING.value:
                raise AssertionError(f"Unexpected barrier successor: {transitioned.state}")
            if transitioned.analyzed_count != 1:
                raise AssertionError(
                    f"Barrier counted {transitioned.analyzed_count} successes instead of 1"
                )
            return {
                "run_id": str(run.id),
                "original_state": original_state,
                "original_state_version": original_version,
                "fixture_created": fixture_created,
                "incomplete_barrier_blocked": incomplete_blocked,
                "terminal_barrier_state": transitioned.state,
                "succeeded": 1,
                "failed_terminal": 2,
                "transaction_rolled_back": True,
            }
        finally:
            await transaction.rollback()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.parse_args()
    report = asyncio.run(verify())
    print("research_analysis_shard_barrier_ok", json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
