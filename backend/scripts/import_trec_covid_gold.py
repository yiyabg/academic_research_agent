"""Build an API-ready gold dataset JSON from official TREC-COVID files."""

import argparse
import json
from datetime import datetime
from pathlib import Path
from uuid import UUID

from app.services.literature_research.trec_covid_gold import (
    build_trec_covid_dataset_from_files,
    parse_qrels,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--qrels", type=Path, required=True)
    parser.add_argument("--topic-id", required=True)
    parser.add_argument("--inspect-only", action="store_true")
    parser.add_argument("--metadata-csv", type=Path)
    parser.add_argument("--project-id", type=UUID)
    parser.add_argument("--license-reference")
    parser.add_argument("--completed-at", type=datetime.fromisoformat)
    parser.add_argument("--minimum-mapped-cases", type=int, default=100)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    qrels_payload = args.qrels.read_text(encoding="utf-8")
    if args.inspect_only:
        rows = parse_qrels(qrels_payload, topic_id=args.topic_id)
        counts = {grade: sum(row.grade == grade for row in rows) for grade in (-1, 0, 1, 2)}
        print(json.dumps({"topic_id": args.topic_id, "rows": len(rows), "grades": counts}))
        return

    missing_arguments = [
        name
        for name, value in {
            "--metadata-csv": args.metadata_csv,
            "--project-id": args.project_id,
            "--license-reference": args.license_reference,
            "--completed-at": args.completed_at,
            "--output": args.output,
        }.items()
        if value is None
    ]
    if missing_arguments:
        parser.error(f"dataset generation requires: {', '.join(missing_arguments)}")
    dataset, report = build_trec_covid_dataset_from_files(
        project_id=args.project_id,
        topic_id=args.topic_id,
        qrels_path=args.qrels,
        metadata_csv_path=args.metadata_csv,
        license_reference=args.license_reference,
        completed_at=args.completed_at,
        minimum_mapped_cases=args.minimum_mapped_cases,
    )
    args.output.write_text(
        json.dumps(dataset.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report.__dict__, ensure_ascii=False, default=list))


if __name__ == "__main__":
    main()
