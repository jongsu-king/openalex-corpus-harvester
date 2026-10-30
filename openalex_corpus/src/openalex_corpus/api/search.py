"""Validated filters documented by OpenAlex, checked 2026-10-29.

https://help.openalex.org/api/filtering/
https://help.openalex.org/api/searching/
https://help.openalex.org/access/fulltext/
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# Deliberately conservative: unknown filters fail locally, never silently drop.
SUPPORTED_FILTERS = frozenset(
    """
publication_year publication_date from_publication_date to_publication_date
from_updated_date from_created_date type language doi ids.openalex ids.pmid ids.pmcid
author.id authorships.author.id authorships.author.orcid institutions.id
authorships.institutions.id authorships.institutions.ror authorships.institutions.country_code
authorships.is_corresponding primary_location.source.id primary_location.source.issn
primary_location.source.type primary_location.version primary_location.license
primary_location.is_oa best_oa_location.license best_oa_location.version
locations.source.id locations.source.issn locations.license locations.version locations.is_oa
topics.id topics.field.id topics.subfield.id topics.domain.id primary_topic.id
concepts.id keywords.id keywords.keyword open_access.is_oa open_access.oa_status
open_access.any_repository_has_fulltext has_content.pdf has_content.grobid_xml
has_fulltext has_abstract has_doi has_pmid has_pmcid has_references has_orcid
has_oa_accepted_or_published_version has_oa_submitted_version is_retracted is_paratext
cited_by_count referenced_works_count authors_count locations_count
title.search abstract.search title_and_abstract.search title_abstract_keywords.search
fulltext.search raw_affiliation_strings.search cites cited_by related_to repository journal
sustainable_development_goals.id indexed_in grants.funder grants.award_id
""".split()
)


def _filter_value(value: Any) -> str:
    if isinstance(value, bool):
        return str(value).lower()
    text = str(value)
    if not text.strip() or "\n" in text or "\r" in text:
        raise ValueError("Filter values must be nonempty single-line values")
    if '"' in text:
        raise ValueError("Literal quotes in filter values are unsupported; use query for phrases")
    if text.count("|") >= 100:
        raise ValueError("OpenAlex permits at most 100 OR values per filter")
    return '"' + text + '"' if "," in text else text


def normalize_identifier(value: str) -> str:
    value = value.strip()
    value = re.sub(r"^https?://(?:www\.)?openalex\.org/", "", value, flags=re.I)
    if re.fullmatch(r"W\d+", value, flags=re.I):
        return value.upper()
    value = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", value, flags=re.I)
    if re.fullmatch(r"10\.\d{4,9}/\S+", value, flags=re.I):
        return "https://doi.org/" + value.lower()
    if re.fullmatch(r"(?:pmid:\d+|pmcid:PMC\d+)", value, flags=re.I):
        prefix, identifier = value.split(":", 1)
        return prefix.lower() + ":" + identifier.upper()
    raise ValueError("Expected an OpenAlex W ID, DOI, pmid:NUMBER, or pmcid:PMCNUMBER")


class SearchSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str | None = None
    scope: Literal["all", "title", "title_and_abstract", "title_abstract_keywords"] = "all"
    exact: bool = False
    title: str | None = None
    abstract: str | None = None
    from_year: int | None = Field(default=None, ge=1000, le=9999)
    to_year: int | None = Field(default=None, ge=1000, le=9999)
    from_date: date | None = None
    to_date: date | None = None
    author: str | None = None
    institution: str | None = None
    source: str | None = None
    topic: str | None = None
    doi: str | None = None
    openalex_id: str | None = None
    oa: bool | None = None
    work_type: str | None = None
    min_citations: int | None = Field(default=None, ge=0)
    has_pdf: bool | None = None
    has_content: bool | None = None
    updated_since: date | None = None
    filters: dict[str, str | int | float | bool] = Field(default_factory=dict)

    @model_validator(mode="after")
    def valid_search(self) -> "SearchSpec":
        if self.from_year and self.to_year and self.from_year > self.to_year:
            raise ValueError("from_year must not exceed to_year")
        if self.from_date and self.to_date and self.from_date > self.to_date:
            raise ValueError("from_date must not exceed to_date")
        for name, prefix in (("author", "A"), ("institution", "I"), ("source", "S"), ("topic", "T")):
            value = getattr(self, name)
            if value and not re.fullmatch(r"(?:https?://openalex\.org/)?" + prefix + r"\d+", value, re.I):
                raise ValueError(f"{name} requires an OpenAlex {prefix} ID, not a name")
        unknown = set(self.filters) - SUPPORTED_FILTERS
        if unknown:
            raise ValueError("Unsupported/unverified OpenAlex filter(s): " + ", ".join(sorted(unknown)))
        return self

    def params(self) -> dict[str, Any]:
        filters: dict[str, Any] = dict(self.filters)
        mapping = {
            "abstract": "abstract.search",
            "author": "authorships.author.id",
            "institution": "authorships.institutions.id",
            "source": "primary_location.source.id",
            "topic": "topics.id",
            "oa": "open_access.is_oa",
            "work_type": "type",
            "has_pdf": "has_content.pdf",
            "has_content": "has_fulltext",
            "from_date": "from_publication_date",
            "to_date": "to_publication_date",
            "updated_since": "from_updated_date",
        }
        for field, key in mapping.items():
            value = getattr(self, field)
            if value is not None:
                if key in filters and str(filters[key]) != str(value):
                    raise ValueError(f"Conflicting values for {key}")
                filters[key] = value
        if self.from_year is not None or self.to_year is not None:
            if "publication_year" in filters:
                raise ValueError("Use year bounds or publication_year filter, not both")
            filters["publication_year"] = f"{self.from_year or ''}-{self.to_year or ''}"
        if self.min_citations is not None:
            filters["cited_by_count"] = f">{self.min_citations - 1}"
        if self.doi:
            identifier = normalize_identifier(self.doi)
            if not identifier.startswith("https://doi.org/"):
                raise ValueError("doi must be a DOI")
            filters["doi"] = identifier
        if self.openalex_id:
            identifier = normalize_identifier(self.openalex_id)
            if not identifier.startswith("W"):
                raise ValueError("openalex_id must be a Work ID")
            filters["ids.openalex"] = identifier
        params: dict[str, Any] = {"per_page": 100}
        if self.query:
            search_key = "search" + ("." + self.scope if self.scope != "all" else "")
            if self.exact:
                search_key += ".exact"
            params[search_key] = self.query
        if self.title:
            if self.query and self.scope == "title" and not self.exact:
                raise ValueError("Use query with scope=title or title, not both")
            params["search.title"] = self.title
        if filters:
            params["filter"] = ",".join(
                f"{key}:{_filter_value(value)}" for key, value in sorted(filters.items())
            )
        return params
