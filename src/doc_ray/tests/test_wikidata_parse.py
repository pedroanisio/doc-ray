"""Pure tests for the Wikidata response parsers (no network)."""

from __future__ import annotations

from doc_ray.adapters import wikidata


def test_parse_search():
    data = {
        "search": [
            {
                "id": "Q188817",
                "label": "Volodymyr Zelensky",
                "description": "president of Ukraine",
                "match": {"language": "en", "text": "Zelensky"},
            }
        ]
    }
    entries = wikidata.parse_search(data)
    assert len(entries) == 1
    assert entries[0].kb_id == "Q188817"
    assert entries[0].label == "Volodymyr Zelensky"


def test_parse_entity_person_with_cross_lingual_aliases():
    data = {
        "entities": {
            "Q188817": {
                "labels": {
                    "en": {"value": "Volodymyr Zelensky"},
                    "uk": {"value": "Володимир Зеленський"},
                },
                "aliases": {"en": [{"value": "Zelenskyy"}]},
                "descriptions": {"en": {"value": "Ukrainian president"}},
                "claims": {"P31": [{"mainsnak": {"datavalue": {"value": {"id": "Q5"}}}}]},
            }
        }
    }
    entry = wikidata.parse_entity("Q188817", data)
    assert entry is not None
    assert entry.entity_type == "PERSON"  # P31 = Q5 (human)
    assert entry.label == "Volodymyr Zelensky"
    assert "Zelenskyy" in entry.aliases  # alias
    assert "Володимир Зеленський" in entry.aliases  # non-preferred label folded in


def test_parse_entity_unknown_type_and_missing():
    no_p31 = {"entities": {"Q1": {"labels": {"en": {"value": "Thing"}}}}}
    entry = wikidata.parse_entity("Q1", no_p31)
    assert entry is not None and entry.entity_type == "UNKNOWN"
    assert wikidata.parse_entity("Q1", {"entities": {}}) is None
