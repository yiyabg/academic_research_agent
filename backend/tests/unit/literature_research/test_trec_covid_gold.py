"""TREC-COVID external human benchmark importer tests."""

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from app.schemas.literature_research.evaluation import GoldDatasetStatus
from app.services.literature_research.trec_covid_gold import (
    build_trec_covid_dataset,
    build_trec_covid_dataset_from_files,
    parse_qrels,
)

QRELS = """1 5 doc-a 2
1 5 doc-b 1
1 5 doc-c 0
1 5 unavailable -1
2 5 other-topic 2
"""

METADATA = """cord_uid,title,doi,publish_time,journal
doc-a,Fully relevant paper,10.1/a,2020-05-01,Journal A
doc-b,Partially relevant paper,,2020,Journal B
doc-c,Irrelevant paper,10.1/c,invalid,
"""


def test_parse_qrels_preserves_manual_grades() -> None:
    rows = parse_qrels(QRELS, topic_id="1")
    assert [(row.cord_uid, row.grade) for row in rows] == [
        ("doc-a", 2),
        ("doc-b", 1),
        ("doc-c", 0),
        ("unavailable", -1),
    ]


def test_build_external_benchmark_is_graded_and_declares_limitations() -> None:
    dataset, report = build_trec_covid_dataset(
        project_id=uuid4(),
        topic_id="1",
        qrels_payload=QRELS,
        metadata_csv_payload=METADATA,
        license_reference="NIST and CORD-19 data-use terms verified by operator",
        completed_at=datetime(2020, 7, 16, tzinfo=UTC),
        minimum_mapped_cases=3,
    )
    assert dataset.status == GoldDatasetStatus.EXTERNAL_BENCHMARK
    assert [case.relevance_grade for case in dataset.cases] == [2, 1, 0]
    assert dataset.provenance is not None
    assert dataset.provenance.limitations
    assert report.grade_counts == {-1: 1, 0: 1, 1: 1, 2: 1}
    assert report.unassessable_document_ids == ("unavailable",)


def test_importer_fails_when_metadata_mapping_is_too_small() -> None:
    with pytest.raises(ValueError, match="at least 4 required"):
        build_trec_covid_dataset(
            project_id=uuid4(),
            topic_id="1",
            qrels_payload=QRELS,
            metadata_csv_payload=METADATA,
            license_reference="verified terms",
            completed_at=datetime(2020, 7, 16, tzinfo=UTC),
            minimum_mapped_cases=4,
        )


def test_file_importer_streams_official_sized_metadata_contract(tmp_path: Path) -> None:
    qrels_path = tmp_path / "qrels.txt"
    metadata_path = tmp_path / "metadata.csv"
    qrels_path.write_text(QRELS, encoding="utf-8")
    metadata_path.write_text(METADATA, encoding="utf-8")
    dataset, report = build_trec_covid_dataset_from_files(
        project_id=uuid4(),
        topic_id="1",
        qrels_path=qrels_path,
        metadata_csv_path=metadata_path,
        license_reference="verified terms",
        completed_at=datetime(2020, 7, 16, tzinfo=UTC),
        minimum_mapped_cases=3,
    )
    assert len(dataset.cases) == 3
    assert report.mapped_count == 3
