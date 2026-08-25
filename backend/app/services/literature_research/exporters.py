"""Deterministic multi-format renderers from one canonical report model."""

import csv
import hashlib
import io
import json
import re
import xml.etree.ElementTree as ET

from app.schemas.literature_research.release import (
    ArtifactFormat,
    CanonicalResearchReport,
    CatalogResearchReport,
    ExclusionAuditRow,
    MetricSnapshotAuditRow,
    RenderedArtifact,
    RunManifest,
)

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
EXCLUSIONS_CSV_HEADER = [
    "work_id",
    "version_id",
    "title",
    "document_type",
    "doi",
    "venue",
    "hard_eligible",
    "hard_fail_count",
    "hard_unknown_count",
    "relevance_decision",
    "relevance_score",
    "reason_codes",
]
VENUE_METRICS_CSV_HEADER = [
    "snapshot_id",
    "metric_fact_id",
    "work_id",
    "title",
    "venue",
    "constraint_id",
    "field",
    "observed_value_json",
    "metric_year",
    "decision",
    "reason_code",
    "source_name",
    "source_version",
    "effective_from",
    "effective_to",
    "license_reference",
    "authorized_scope",
    "license_attested",
    "snapshot_status",
    "payload_sha256",
    "evidence_reference",
]


def _artifact(format_: ArtifactFormat, filename: str, content_type: str, text: str):
    data = text.encode("utf-8")
    return RenderedArtifact(
        format=format_,
        filename=filename,
        content_type=content_type,
        data=data,
        sha256=hashlib.sha256(data).hexdigest(),
    )


def render_markdown(report: CanonicalResearchReport) -> RenderedArtifact:
    lines = [f"# {report.title}", "", f"- Protocol: `{report.protocol_hash}`"]
    lines.append(f"- Strict results: {report.strict_count}/{report.target_count}")
    lines.extend(["", "## 主题总览", "", report.synthesis.overview])
    for index, paper in enumerate(report.papers, start=1):
        lines.extend(
            [
                "",
                f"## {index}. {paper.title}",
                "",
                f"- DOI: {paper.doi or 'not_reported'}",
                f"- Venue: {paper.venue or 'not_reported'}",
                f"- Relevance: {paper.relevance_score:.4f}",
            ]
        )
        for section in paper.analysis.sections:
            lines.extend(["", f"### {section.section_id}", "", section.summary])
            for claim in section.claims:
                evidence = ", ".join(claim.evidence_ids)
                lines.append(f"- {claim.text} [{evidence}]")
    return _artifact(
        ArtifactFormat.MARKDOWN, "research_report.md", "text/markdown", "\n".join(lines) + "\n"
    )


def render_opml(report: CanonicalResearchReport) -> RenderedArtifact:
    root = ET.Element("opml", version="2.0")
    head = ET.SubElement(root, "head")
    ET.SubElement(head, "title").text = _CONTROL.sub("", report.title)
    body = ET.SubElement(root, "body")
    overview = ET.SubElement(body, "outline", text="1. 主题总览")
    ET.SubElement(overview, "outline", text=_CONTROL.sub("", report.synthesis.overview))
    for index, paper in enumerate(report.papers, start=1):
        node = ET.SubElement(body, "outline", text=f"{index + 1}. {paper.title}")
        info = ET.SubElement(node, "outline", text="基本信息")
        ET.SubElement(info, "outline", text=f"DOI: {paper.doi or 'not_reported'}")
        for section in paper.analysis.sections:
            section_node = ET.SubElement(node, "outline", text=section.section_id)
            ET.SubElement(section_node, "outline", text=_CONTROL.sub("", section.summary))
    xml = ET.tostring(root, encoding="unicode", xml_declaration=True)
    return _artifact(ArtifactFormat.OPML, "research_report.opml", "text/x-opml", xml)


def _bib_key(paper) -> str:
    author = re.sub(r"\W+", "", paper.authors[0].split()[-1]) if paper.authors else "Anon"
    return f"{author}{paper.year or 'ND'}_{str(paper.work_id).replace('-', '')[:8]}"


def render_bibtex(report: CanonicalResearchReport) -> RenderedArtifact:
    entries = []
    for paper in report.papers:
        fields = {
            "title": paper.title,
            "author": " and ".join(paper.authors),
            "year": str(paper.year or ""),
            "doi": paper.doi or "",
            "url": paper.source_url or "",
        }
        body = ",\n".join(
            f"  {key} = {{{value.replace('{', '').replace('}', '')}}}"
            for key, value in fields.items()
            if value
        )
        entries.append(f"@article{{{_bib_key(paper)},\n{body}\n}}")
    return _artifact(
        ArtifactFormat.BIBTEX, "references.bib", "application/x-bibtex", "\n\n".join(entries) + "\n"
    )


def render_jsonl(report: CanonicalResearchReport) -> RenderedArtifact:
    lines = [
        json.dumps(
            paper.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        for paper in report.papers
    ]
    text = ("\n".join(lines) + "\n") if lines else ""
    return _artifact(ArtifactFormat.JSONL, "papers.jsonl", "application/x-ndjson", text)


def render_csv(report: CanonicalResearchReport) -> RenderedArtifact:
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(["work_id", "title", "authors", "year", "doi", "venue", "relevance_score"])
    for paper in report.papers:
        writer.writerow(
            [
                paper.work_id,
                paper.title,
                "; ".join(paper.authors),
                paper.year or "",
                paper.doi or "",
                paper.venue or "",
                f"{paper.relevance_score:.4f}",
            ]
        )
    return _artifact(ArtifactFormat.CSV, "papers.csv", "text/csv", stream.getvalue())


def render_catalog_markdown(report: CatalogResearchReport) -> RenderedArtifact:
    """Render an explicit metadata-only catalog without implying PDF analysis."""
    lines = [
        f"# {report.title}",
        "",
        f"- Protocol: `{report.protocol_hash}`",
        f"- Strict results: {report.strict_count}/{report.target_count}",
        "- Scope: metadata, source provenance, hard constraints, and relevance ranking only.",
        "- Not performed: PDF acquisition, parsing, evidence audit, figure extraction, or deep analysis.",
        "",
        "## Ranked papers",
    ]
    for paper in report.papers:
        lines.extend(
            [
                "",
                f"## {paper.rank}. {paper.title}",
                "",
                f"- DOI: {paper.doi or 'not_reported'}",
                f"- Venue: {paper.venue or 'not_reported'}",
                f"- Type: {paper.document_type}",
                f"- Relevance: {paper.relevance_score:.4f}",
            ]
        )
    return _artifact(
        ArtifactFormat.MARKDOWN,
        "research_catalog.md",
        "text/markdown",
        "\n".join(lines) + "\n",
    )


def render_catalog_opml(report: CatalogResearchReport) -> RenderedArtifact:
    root = ET.Element("opml", version="2.0")
    head = ET.SubElement(root, "head")
    ET.SubElement(head, "title").text = _CONTROL.sub("", report.title)
    body = ET.SubElement(root, "body")
    notice = ET.SubElement(body, "outline", text="Metadata-only catalog; no PDF or deep analysis")
    notice.set("_note", f"Protocol {report.protocol_hash}; strict {report.strict_count}/{report.target_count}")
    for paper in report.papers:
        node = ET.SubElement(body, "outline", text=f"{paper.rank}. {_CONTROL.sub('', paper.title)}")
        node.set("_note", f"relevance={paper.relevance_score:.4f}; type={paper.document_type}")
        ET.SubElement(node, "outline", text=f"DOI: {paper.doi or 'not_reported'}")
        ET.SubElement(node, "outline", text=f"Venue: {_CONTROL.sub('', paper.venue or 'not_reported')}")
    xml = ET.tostring(root, encoding="unicode", xml_declaration=True)
    return _artifact(ArtifactFormat.OPML, "research_catalog.opml", "text/x-opml", xml)


def render_catalog_bibtex(report: CatalogResearchReport) -> RenderedArtifact:
    entries = []
    for paper in report.papers:
        fields = {
            "title": paper.title,
            "author": " and ".join(paper.authors),
            "year": str(paper.year or ""),
            "doi": paper.doi or "",
            "url": paper.source_url or "",
            "journal": paper.venue or "",
        }
        body = ",\n".join(
            f"  {key} = {{{value.replace('{', '').replace('}', '')}}}"
            for key, value in fields.items()
            if value
        )
        entries.append(f"@article{{{_bib_key(paper)},\n{body}\n}}")
    return _artifact(
        ArtifactFormat.BIBTEX,
        "references.bib",
        "application/x-bibtex",
        "\n\n".join(entries) + "\n",
    )


def render_catalog_csv(report: CatalogResearchReport) -> RenderedArtifact:
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(
        [
            "rank",
            "work_id",
            "version_id",
            "title",
            "authors",
            "year",
            "doi",
            "venue",
            "document_type",
            "relevance_score",
            "source_url",
        ]
    )
    for paper in report.papers:
        writer.writerow(
            [
                paper.rank,
                paper.work_id,
                paper.version_id,
                paper.title,
                "; ".join(paper.authors),
                paper.year or "",
                paper.doi or "",
                paper.venue or "",
                paper.document_type,
                f"{paper.relevance_score:.4f}",
                paper.source_url or "",
            ]
        )
    return _artifact(ArtifactFormat.CSV, "papers.csv", "text/csv", stream.getvalue())


def render_exclusions_csv(rows: list[ExclusionAuditRow]) -> RenderedArtifact:
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(EXCLUSIONS_CSV_HEADER)
    for row in sorted(rows, key=lambda item: str(item.work_id)):
        writer.writerow(
            [
                row.work_id,
                row.version_id or "",
                row.title,
                row.document_type,
                row.doi or "",
                row.venue or "",
                "" if row.hard_eligible is None else str(row.hard_eligible).lower(),
                row.hard_fail_count,
                row.hard_unknown_count,
                row.relevance_decision or "",
                "" if row.relevance_score is None else f"{row.relevance_score:.6f}",
                ";".join(sorted(set(row.reason_codes))),
            ]
        )
    return _artifact(
        ArtifactFormat.EXCLUSIONS_CSV,
        "exclusions.csv",
        "text/csv",
        stream.getvalue(),
    )


def render_venue_metrics_csv(rows: list[MetricSnapshotAuditRow]) -> RenderedArtifact:
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(VENUE_METRICS_CSV_HEADER)
    for row in sorted(
        rows,
        key=lambda item: (str(item.snapshot_id), str(item.work_id), item.constraint_id),
    ):
        writer.writerow(
            [
                row.snapshot_id,
                row.metric_fact_id,
                row.work_id,
                row.title,
                row.venue or "",
                row.constraint_id,
                row.field,
                json.dumps(
                    row.observed_value,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                row.metric_year,
                row.decision,
                row.reason_code,
                row.source_name,
                row.source_version,
                row.effective_from.isoformat(),
                row.effective_to.isoformat() if row.effective_to else "",
                row.license_reference,
                row.authorized_scope,
                str(row.license_attested).lower(),
                row.snapshot_status,
                row.payload_sha256,
                row.evidence_reference or "",
            ]
        )
    return _artifact(
        ArtifactFormat.VENUE_METRICS_CSV,
        "venue_metrics_snapshot.csv",
        "text/csv",
        stream.getvalue(),
    )


def render_manifest(manifest: RunManifest) -> RenderedArtifact:
    text = (
        json.dumps(manifest.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, indent=2)
        + "\n"
    )
    return _artifact(ArtifactFormat.MANIFEST, "run_manifest.json", "application/json", text)


def validate_artifacts(artifacts: list[RenderedArtifact]) -> list[str]:
    errors = []
    for artifact in artifacts:
        if hashlib.sha256(artifact.data).hexdigest() != artifact.sha256:
            errors.append(f"{artifact.filename}: hash mismatch")
        try:
            if artifact.format == ArtifactFormat.OPML:
                ET.fromstring(artifact.data)
            elif artifact.format == ArtifactFormat.JSONL:
                for line in artifact.data.decode().splitlines():
                    json.loads(line)
            elif artifact.format == ArtifactFormat.MANIFEST:
                json.loads(artifact.data)
            elif artifact.format == ArtifactFormat.EXCLUSIONS_CSV:
                rows = list(csv.reader(io.StringIO(artifact.data.decode("utf-8"))))
                if not rows or rows[0] != EXCLUSIONS_CSV_HEADER:
                    errors.append(f"{artifact.filename}: invalid audit header")
            elif artifact.format == ArtifactFormat.VENUE_METRICS_CSV:
                rows = list(csv.reader(io.StringIO(artifact.data.decode("utf-8"))))
                if not rows or rows[0] != VENUE_METRICS_CSV_HEADER:
                    errors.append(f"{artifact.filename}: invalid audit header")
        except (csv.Error, ET.ParseError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            errors.append(f"{artifact.filename}: {exc}")
    return errors
