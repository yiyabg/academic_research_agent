"""Tests for local paper query parser."""

import pytest

from app.services.literature_research.local_paper_query_parser import (
    LocalPaperQueryParser,
)


@pytest.fixture
def parser() -> LocalPaperQueryParser:
    return LocalPaperQueryParser()


class TestYearParsing:
    def test_exact_year_chinese(self, parser: LocalPaperQueryParser) -> None:
        result = parser.parse(query="2026年发表的 semantic communication")
        assert result.semantic_query == "semantic communication"
        assert result.effective_filters["year_from"] == 2026
        assert result.effective_filters["year_to"] == 2026
        assert result.filter_sources["year_from"] == "parsed"

    def test_exact_year_english(self, parser: LocalPaperQueryParser) -> None:
        result = parser.parse(query="papers published in 2025 about AI")
        assert "AI" in result.semantic_query
        assert result.effective_filters["year_from"] == 2025
        assert result.effective_filters["year_to"] == 2025

    def test_year_range(self, parser: LocalPaperQueryParser) -> None:
        result = parser.parse(query="2024-2026年的论文")
        assert result.effective_filters["year_from"] == 2024
        assert result.effective_filters["year_to"] == 2026

    def test_year_range_chinese_separator(self, parser: LocalPaperQueryParser) -> None:
        result = parser.parse(query="2024至2026年发表的文章")
        assert result.effective_filters["year_from"] == 2024
        assert result.effective_filters["year_to"] == 2026
        assert result.semantic_query == ""

    def test_year_from(self, parser: LocalPaperQueryParser) -> None:
        result = parser.parse(query="从2024年起的论文")
        assert result.effective_filters["year_from"] == 2024
        assert "year_to" not in result.effective_filters

    def test_year_to(self, parser: LocalPaperQueryParser) -> None:
        result = parser.parse(query="截至2026年的论文")
        assert result.effective_filters["year_to"] == 2026
        assert "year_from" not in result.effective_filters

    def test_year_forms_required_by_search_contract(self, parser: LocalPaperQueryParser) -> None:
        exact = parser.parse(query="发表于2026年的语义通信论文")
        assert exact.semantic_query == "语义通信论文"
        assert exact.effective_filters == {"year_from": 2026, "year_to": 2026}

        from_year = parser.parse(query="2024年及以后的论文")
        assert from_year.effective_filters == {"year_from": 2024}

        until_year = parser.parse(query="2026年以前的论文")
        assert until_year.effective_filters == {"year_to": 2026}

    def test_false_positive_6g(self, parser: LocalPaperQueryParser) -> None:
        result = parser.parse(query="6G networks and semantic communication")
        assert "year_from" not in result.effective_filters
        assert "6G" in result.semantic_query

    def test_false_positive_3d(self, parser: LocalPaperQueryParser) -> None:
        result = parser.parse(query="3D reconstruction using deep learning")
        assert "year_from" not in result.effective_filters
        assert "3D" in result.semantic_query

    def test_false_positive_gpt4(self, parser: LocalPaperQueryParser) -> None:
        result = parser.parse(query="GPT-4 based language models")
        assert "year_from" not in result.effective_filters
        assert "GPT" in result.semantic_query

    def test_false_positive_ieee(self, parser: LocalPaperQueryParser) -> None:
        result = parser.parse(query="IEEE 802.11 wireless standards")
        assert "year_from" not in result.effective_filters
        assert "IEEE 802.11" in result.semantic_query

    def test_real_year_is_not_lost_when_query_also_mentions_6g(
        self, parser: LocalPaperQueryParser
    ) -> None:
        result = parser.parse(query="6G 2026年发表的 semantic communication")
        assert result.effective_filters["year_from"] == 2026
        assert result.effective_filters["year_to"] == 2026
        assert "6G" in result.semantic_query


class TestDOIParsing:
    def test_doi_with_prefix(self, parser: LocalPaperQueryParser) -> None:
        result = parser.parse(query="doi:10.1234/abc.2023")
        assert result.effective_filters["doi"] == "10.1234/abc.2023"

    def test_doi_url(self, parser: LocalPaperQueryParser) -> None:
        result = parser.parse(query="https://doi.org/10.5678/xyz")
        assert result.effective_filters["doi"] == "10.5678/xyz"

    def test_doi_plain(self, parser: LocalPaperQueryParser) -> None:
        result = parser.parse(query="10.1109/JSAC.2023.1234567")
        assert result.effective_filters["doi"] == "10.1109/JSAC.2023.1234567"


class TestBibTeXTypeParsing:
    def test_chinese_journal(self, parser: LocalPaperQueryParser) -> None:
        result = parser.parse(query="期刊论文关于AI")
        assert result.effective_filters["bibtex_type"] == "article"

    def test_chinese_conference(self, parser: LocalPaperQueryParser) -> None:
        result = parser.parse(query="会议论文关于机器学习")
        assert result.effective_filters["bibtex_type"] == "inproceedings"

    def test_english_article(self, parser: LocalPaperQueryParser) -> None:
        result = parser.parse(query="article about deep learning")
        assert result.effective_filters["bibtex_type"] == "article"


class TestExplicitOverride:
    def test_explicit_year_overrides_parsed(self, parser: LocalPaperQueryParser) -> None:
        result = parser.parse(query="2026年的论文", year_from=2024, year_to=2025)
        assert result.effective_filters["year_from"] == 2024
        assert result.effective_filters["year_to"] == 2025
        assert result.filter_sources["year_from"] == "explicit"
        assert len(result.warnings) > 0  # Should warn about override

    def test_explicit_doi(self, parser: LocalPaperQueryParser) -> None:
        result = parser.parse(query="semantic communication", doi="10.1234/test")
        assert result.effective_filters["doi"] == "10.1234/test"
        assert result.filter_sources["doi"] == "explicit"

    def test_explicit_author(self, parser: LocalPaperQueryParser) -> None:
        result = parser.parse(query="deep learning", author="Zhang Wei")
        assert result.effective_filters["author"] == "Zhang Wei"
        assert result.filter_sources["author"] == "explicit"


class TestMarkedMetadataFilters:
    def test_only_high_confidence_markers_are_parsed(self, parser: LocalPaperQueryParser) -> None:
        result = parser.parse(
            query="作者: Zhang Wei; 期刊: IEEE TWC; 关键词: semantic communication, VLA"
        )
        assert result.effective_filters["author"] == "Zhang Wei"
        assert result.effective_filters["venue"] == "IEEE TWC"
        assert result.effective_filters["keywords"] == ["semantic communication", "VLA"]
        assert result.filter_sources["author"] == "parsed"

    def test_explicit_filters_win_over_marked_filters(self, parser: LocalPaperQueryParser) -> None:
        result = parser.parse(
            query="author: Zhang Wei; venue: IEEE TCCN; keywords: semantic communication, VLA",
            author="Li Lei",
            venue="IEEE JSAC",
            keywords=["agent"],
        )
        assert result.effective_filters["author"] == "Li Lei"
        assert result.effective_filters["venue"] == "IEEE JSAC"
        assert result.effective_filters["keywords"] == ["agent"]


class TestValidation:
    def test_invalid_year_range_parsed(self, parser: LocalPaperQueryParser) -> None:
        result = parser.parse(query="2026-2024年的论文")
        # Should not apply invalid range
        assert (
            "year_from" not in result.effective_filters or "year_to" not in result.effective_filters
        )

    def test_invalid_year_range_explicit(self, parser: LocalPaperQueryParser) -> None:
        result = parser.parse(query="论文", year_from=2026, year_to=2024)
        assert len(result.warnings) > 0
        assert any("无效" in w for w in result.warnings)


class TestSemanticQueryCleaning:
    def test_removes_year_expression(self, parser: LocalPaperQueryParser) -> None:
        result = parser.parse(query="2026年发表的 semantic communication 相关文章")
        assert "2026" not in result.semantic_query
        assert "semantic communication" in result.semantic_query

    def test_removes_doi(self, parser: LocalPaperQueryParser) -> None:
        result = parser.parse(query="查找 doi:10.1234/test 这篇论文")
        assert "10.1234" not in result.semantic_query
        assert "doi" not in result.semantic_query

    def test_cleans_punctuation(self, parser: LocalPaperQueryParser) -> None:
        result = parser.parse(query="2024年的  semantic   communication  论文")
        # Should not have leading/trailing spaces or excessive internal spaces
        assert result.semantic_query.strip() == result.semantic_query
        assert "  " not in result.semantic_query


class TestComplexQueries:
    def test_year_and_doi(self, parser: LocalPaperQueryParser) -> None:
        result = parser.parse(query="2026年发表的 10.1234/test semantic communication")
        assert result.effective_filters["year_from"] == 2026
        assert result.effective_filters["doi"] == "10.1234/test"
        assert "semantic communication" in result.semantic_query

    def test_year_range_and_type(self, parser: LocalPaperQueryParser) -> None:
        result = parser.parse(query="2024-2026年的期刊论文关于AI")
        assert result.effective_filters["year_from"] == 2024
        assert result.effective_filters["year_to"] == 2026
        assert result.effective_filters["bibtex_type"] == "article"
        assert "AI" in result.semantic_query

    def test_all_filters_explicit(self, parser: LocalPaperQueryParser) -> None:
        result = parser.parse(
            query="some topic",
            author="Zhang Wei",
            doi="10.1234/test",
            year_from=2024,
            year_to=2026,
            bibtex_type="article",
        )
        assert result.effective_filters["author"] == "Zhang Wei"
        assert result.effective_filters["doi"] == "10.1234/test"
        assert result.effective_filters["year_from"] == 2024
        assert result.effective_filters["year_to"] == 2026
        assert result.effective_filters["bibtex_type"] == "article"
        assert all(v == "explicit" for v in result.filter_sources.values())
