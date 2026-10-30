from copy import deepcopy

import pytest

from openalex_corpus.metadata import normalize_doi, normalize_work, restore_abstract, work_id


def test_canonical_identifiers():
    assert work_id("https://openalex.org/W123") == "W123"
    assert work_id("w123") == "W123"
    assert normalize_doi(" HTTPS://DX.DOI.ORG/10.1000/AbC ") == "10.1000/abc"
    assert normalize_doi("doi:10.1000/A") == "10.1000/a"
    assert normalize_doi(None) is None
    with pytest.raises(ValueError):
        work_id("A123")


def test_abstract_restored_using_positions_not_dictionary_order():
    result = restore_abstract({"paper": [2], "A": [0], "scientific": [1], "A.": [3]})
    assert result["text"] == "A scientific paper A."
    assert result["status"] == "present"
    assert restore_abstract({"one": [0, 2], "two": [1]})["text"] == "one two one"


@pytest.mark.parametrize(
    "index,issue",
    [
        ({"a": [0], "b": [2]}, "missing_abstract_positions"),
        ({"a": [0], "b": [0]}, "conflicting_tokens_at_position"),
        ({"a": [-1]}, "invalid_position"),
        ({"a": [True]}, "invalid_position"),
        ({"a": "1"}, "invalid_token_or_positions"),
    ],
)
def test_bad_abstract_does_not_invent_or_hide_gaps(index, issue):
    result = restore_abstract(index)
    assert result["status"] == "parse_failed"
    assert result["text"] is None
    assert issue in result["issues"]


def test_no_abstract_and_no_references_have_explicit_missing_state():
    normalized = normalize_work(
        {"id": "https://openalex.org/W1", "abstract_inverted_index": None, "referenced_works": []}
    )
    assert normalized["abstract_text"] is None
    assert normalized["field_status"]["abstract_text"]["status"] == "not_provided_by_source"
    assert normalized["field_status"]["abstract_inverted_index"]["missing_reason"] == "source_null"
    assert normalized["field_status"]["doi"]["missing_reason"] == "source_field_absent"
    assert normalized["referenced_works"] == []
    assert normalized["field_status"]["referenced_works"]["status"] == "present"


def test_raw_unknown_null_and_empty_fields_preserved_without_mutation():
    raw = {
        "id": "https://openalex.org/W1",
        "doi": None,
        "title": "",
        "unknown_future_field": {"null": None, "empty": [], "exact": "µ 𝜈 10−9"},
        "authorships": [],
    }
    original = deepcopy(raw)
    normalized = normalize_work(raw)
    assert raw == original
    assert normalized["unknown_future_field"] == raw["unknown_future_field"]
    assert normalized["title"] == ""
    assert normalized["doi"] is None
    assert normalized["authorships"] == []
    normalized["unknown_future_field"]["exact"] = "mutated"
    assert raw == original


def test_title_and_display_name_are_not_conflated():
    result = normalize_work({"id": "W1", "title": None, "display_name": "Observed name"})
    assert result["title"] is None
    assert result["display_name"] == "Observed name"
    assert result["field_status"]["title"]["status"] == "not_provided_by_source"


def test_nested_field_status_tracks_author_affiliation_and_location_nulls():
    raw = {
        "id": "W1",
        "authorships": [
            {
                "author": {"id": "https://openalex.org/A1", "orcid": None},
                "institutions": [{"id": "https://openalex.org/I1", "ror": None}],
            }
        ],
        "locations": [{"license": None, "version": "acceptedVersion"}],
    }
    states = normalize_work(raw)["field_status"]
    assert states["authorships[0].author.orcid"]["missing_reason"] == "source_null"
    assert states["authorships[0].author.display_name"]["missing_reason"] == "source_field_absent"
    assert states["authorships[0].institutions[0].ror"]["status"] == "not_provided_by_source"
    assert states["locations[0].license"]["missing_reason"] == "source_null"
    assert states["locations[0].version"]["status"] == "present"
    assert states["locations[0].version"]["source"] == "openalex"
    assert states["locations[0].version"]["source_field"] == "locations[0].version"
