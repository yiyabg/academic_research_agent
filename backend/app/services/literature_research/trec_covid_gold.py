"""Strict importer for the public, human-assessed TREC-COVID Complete benchmark."""

import csv
import io
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import TextIO
from uuid import UUID

from app.schemas.literature_research.evaluation import (
    EvaluationDatasetCreate,
    GoldDatasetProvenance,
    GoldDatasetStatus,
    GoldPaperCase,
    GoldSourceObservation,
)

TREC_COVID_QRELS_URL = "https://ir.nist.gov/covidSubmit/data/qrels-covid_d5_j0.5-5.txt"


@dataclass(frozen=True)
class TrecQrel:
    topic_id: str
    iteration: str
    cord_uid: str
    grade: int


@dataclass(frozen=True)
class TrecImportReport:
    topic_id: str
    qrel_count: int
    mapped_count: int
    missing_document_ids: tuple[str, ...]
    unassessable_document_ids: tuple[str, ...]
    grade_counts: dict[int, int]


def parse_qrels(payload: str, *, topic_id: str) -> list[TrecQrel]:
    """Parse one topic from the four-column NIST qrels format."""
    result: dict[str, TrecQrel] = {}
    for line_number, raw_line in enumerate(payload.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        columns = line.split()
        if len(columns) != 4:
            raise ValueError(f"invalid qrels row {line_number}: expected four columns")
        row_topic, iteration, cord_uid, raw_grade = columns
        if row_topic != topic_id:
            continue
        try:
            grade = int(raw_grade)
        except ValueError as exc:
            raise ValueError(f"invalid qrels grade on row {line_number}") from exc
        if grade not in {-1, 0, 1, 2}:
            raise ValueError(f"unsupported TREC-COVID grade {grade} on row {line_number}")
        if cord_uid in result:
            raise ValueError(f"duplicate qrels document id for topic {topic_id}: {cord_uid}")
        result[cord_uid] = TrecQrel(row_topic, iteration, cord_uid, grade)
    if not result:
        raise ValueError(f"topic {topic_id} has no qrels")
    return list(result.values())


def _optional(value: str | None) -> str | None:
    normalized = (value or "").strip()
    return normalized or None


def _date(value: str | None) -> date | None:
    raw = _optional(value)
    if raw is None:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        if len(raw) == 4 and raw.isdigit():
            return date(int(raw), 1, 1)
    return None


def _build_trec_covid_dataset(
    *,
    project_id: UUID,
    topic_id: str,
    qrels: list[TrecQrel],
    metadata_reader: csv.DictReader,
    license_reference: str,
    completed_at: datetime,
    minimum_mapped_cases: int = 100,
) -> tuple[EvaluationDatasetCreate, TrecImportReport]:
    """Build a dataset from parsed qrels and a streaming metadata reader."""
    required_columns = {"cord_uid", "title"}
    if not metadata_reader.fieldnames or not required_columns.issubset(metadata_reader.fieldnames):
        raise ValueError("CORD-19 metadata CSV must contain cord_uid and title columns")
    wanted_ids = {row.cord_uid for row in qrels if row.grade >= 0}
    metadata = {
        row["cord_uid"].strip(): row
        for row in metadata_reader
        if row.get("cord_uid", "").strip() in wanted_ids and row.get("title", "").strip()
    }

    cases: list[GoldPaperCase] = []
    observations: list[GoldSourceObservation] = []
    missing: list[str] = []
    unassessable: list[str] = []
    grade_counts = {-1: 0, 0: 0, 1: 0, 2: 0}
    for qrel in qrels:
        if qrel.grade == -1:
            grade_counts[-1] += 1
            unassessable.append(qrel.cord_uid)
            continue
        row = metadata.get(qrel.cord_uid)
        if row is None:
            missing.append(qrel.cord_uid)
            continue
        grade_counts[qrel.grade] += 1
        case_id = f"trec-covid-{topic_id}-{qrel.cord_uid}"
        cases.append(
            GoldPaperCase(
                case_id=case_id,
                title=row["title"].strip(),
                doi=_optional(row.get("doi")),
                relevant=qrel.grade > 0,
                relevance_grade=qrel.grade,
                expected_date=_date(row.get("publish_time")),
                expected_venue=_optional(row.get("journal")),
            )
        )
        observations.append(
            GoldSourceObservation(
                source="trec-covid-complete",
                source_id=qrel.cord_uid,
                expected_cluster_id=case_id,
            )
        )
    if len(cases) < minimum_mapped_cases:
        raise ValueError(
            f"only {len(cases)} qrels map to metadata; at least {minimum_mapped_cases} required"
        )

    dataset = EvaluationDatasetCreate(
        project_id=project_id,
        name=f"TREC-COVID Complete topic {topic_id}",
        version="round-5-complete",
        description=(
            "Public human-assessed retrieval benchmark imported from NIST qrels and the "
            "matching July 16, 2020 CORD-19 metadata release."
        ),
        status=GoldDatasetStatus.EXTERNAL_BENCHMARK,
        provenance=GoldDatasetProvenance(
            source_name="NIST TREC-COVID Complete",
            source_url=TREC_COVID_QRELS_URL,
            license=license_reference,
            annotator_count=67,
            judgment_method=(
                "Pooled retrieval results manually graded 0/1/2 by NLM MeSH indexers, "
                "medical students, and assessors holding medical or biomedical science degrees."
            ),
            completed_at=completed_at,
            domain_coverage=["biomedicine", "COVID-19 information retrieval"],
            language_coverage=["en"],
            limitations=[
                "A pooled test collection is incomplete outside judged topic-document pairs.",
                "A topic-document pair was generally assigned to one expert, not independently "
                "double-annotated and third-party adjudicated.",
                "This benchmark does not satisfy cross-domain or multilingual acceptance alone.",
            ],
        ),
        cases=cases,
        observations=observations,
    )
    return dataset, TrecImportReport(
        topic_id=topic_id,
        qrel_count=len(qrels),
        mapped_count=len(cases),
        missing_document_ids=tuple(missing),
        unassessable_document_ids=tuple(unassessable),
        grade_counts=grade_counts,
    )


def build_trec_covid_dataset(
    *,
    project_id: UUID,
    topic_id: str,
    qrels_payload: str,
    metadata_csv_payload: str,
    license_reference: str,
    completed_at: datetime,
    minimum_mapped_cases: int = 100,
) -> tuple[EvaluationDatasetCreate, TrecImportReport]:
    """Map in-memory NIST qrels and CORD-19 metadata without inventing documents."""
    if not license_reference.strip():
        raise ValueError("an explicit qrels/CORD-19 license or data-use reference is required")
    return _build_trec_covid_dataset(
        project_id=project_id,
        topic_id=topic_id,
        qrels=parse_qrels(qrels_payload, topic_id=topic_id),
        metadata_reader=csv.DictReader(io.StringIO(metadata_csv_payload)),
        license_reference=license_reference,
        completed_at=completed_at,
        minimum_mapped_cases=minimum_mapped_cases,
    )


def build_trec_covid_dataset_from_files(
    *,
    project_id: UUID,
    topic_id: str,
    qrels_path: Path,
    metadata_csv_path: Path,
    license_reference: str,
    completed_at: datetime,
    minimum_mapped_cases: int = 100,
) -> tuple[EvaluationDatasetCreate, TrecImportReport]:
    """Stream the 257 MB historical metadata file and retain only judged document rows."""
    if not license_reference.strip():
        raise ValueError("an explicit qrels/CORD-19 license or data-use reference is required")
    qrels = parse_qrels(qrels_path.read_text(encoding="utf-8"), topic_id=topic_id)
    metadata_file: TextIO
    with metadata_csv_path.open(encoding="utf-8", newline="") as metadata_file:
        return _build_trec_covid_dataset(
            project_id=project_id,
            topic_id=topic_id,
            qrels=qrels,
            metadata_reader=csv.DictReader(metadata_file),
            license_reference=license_reference,
            completed_at=completed_at,
            minimum_mapped_cases=minimum_mapped_cases,
        )
