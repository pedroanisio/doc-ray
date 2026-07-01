"""Tests for the lexeme identity + occurrence layer."""

from __future__ import annotations

import json
import re

import pytest

import docmodel as dm
import lexeme as lx


# A deterministic, dependency-free analyzer for testing the identity core.
class StubAnalyzer:
    name = "stub/test"
    LEMMA = {"ran": "run", "runs": "run", "running": "run", "leaves": "leave"}
    POS = {"ran": "VERB", "runs": "VERB", "running": "VERB", "leaves": "VERB",
           "the": "DET", "dog": "NOUN", "dogs": "NOUN"}

    def analyze(self, surfaces):
        out = []
        for s in surfaces:
            w = s.casefold()
            out.append((self.LEMMA.get(w, w), self.POS.get(w, "NOUN")))
        return out


# ---------------------------------------------------------------------------
# canonical_key
# ---------------------------------------------------------------------------

def test_canonical_key_normalises_case_and_whitespace():
    assert lx.canonical_key("  Run ", "verb", "EN") == "en:VERB:run"


def test_canonical_key_separates_pos_and_lang():
    assert lx.canonical_key("run", "VERB", "en") != lx.canonical_key("run", "NOUN", "en")
    assert lx.canonical_key("run", "VERB", "en") != lx.canonical_key("run", "VERB", "pt")


# ---------------------------------------------------------------------------
# Registry — deterministic identity
# ---------------------------------------------------------------------------

def test_same_lexeme_always_gets_same_uid():
    reg = lx.Registry()
    a = reg.add("run", "VERB", "en")
    b = reg.add("run", "VERB", "en")
    assert a == b
    assert len(reg.lexemes()) == 1


def test_uid_is_namespaced_by_pos_and_lang():
    reg = lx.Registry()
    verb = reg.add("run", "VERB", "en")
    noun = reg.add("run", "NOUN", "en")
    pt = reg.add("run", "VERB", "pt")
    assert verb != noun != pt and verb != pt
    assert verb.startswith("lx_en.v_")
    assert noun.startswith("lx_en.n_")
    assert pt.startswith("lx_pt.v_")


def test_inflected_forms_are_recorded_and_share_one_uid():
    reg = lx.Registry()
    reg.add("run", "VERB", "en", surface="ran")
    reg.add("run", "VERB", "en", surface="running")
    reg.add("run", "VERB", "en", surface="ran")  # duplicate surface
    entry = reg.get(reg.add("run", "VERB", "en"))
    assert entry["forms"] == ["ran", "running"]


def test_collision_is_resolved_by_growing_the_hash():
    # size=1 → only 16 base slots, so adding many keys forces collisions.
    reg = lx.Registry(size=1)
    uids = {reg.add(f"lemma{i}", "NOUN", "en") for i in range(60)}
    assert len(uids) == 60                       # all distinct despite collisions
    # And identity stays stable: re-adding returns the same uid.
    again = reg.add("lemma7", "NOUN", "en")
    assert again == reg._by_key["en:NOUN:lemma7"]


def test_registry_to_dict_is_json_serialisable():
    reg = lx.Registry()
    reg.analyzer = "stub/test"
    reg.add("run", "VERB", "en", surface="ran")
    blob = json.dumps(reg.to_dict())
    data = json.loads(blob)
    assert data["analyzer"] == "stub/test"
    assert data["lexemes"][0]["key"] == "en:VERB:run"


# ---------------------------------------------------------------------------
# annotate — offsets and occurrences over the lossless tree
# ---------------------------------------------------------------------------

def test_occurrence_offsets_point_at_the_exact_surface():
    text = "The dog ran. The dogs run home."
    tree = dm.decompose(text)
    _, occ = lx.annotate(tree, StubAnalyzer(), lang="en", doc_id="d")
    assert occ, "expected occurrences"
    for o in occ:
        assert text[o.start:o.end] == o.surface   # offsets are exact


def test_repeated_lemma_shares_uid_across_occurrences():
    text = "She ran. He runs. They are running."
    tree = dm.decompose(text)
    reg, occ = lx.annotate(tree, StubAnalyzer(), doc_id="d")
    run_uids = {o.uid for o in occ if o.lemma == "run"}
    assert len(run_uids) == 1                      # ran / runs / running → one lexeme
    forms = reg.get(run_uids.pop())["forms"]
    assert set(forms) >= {"ran", "runs", "running"}


def test_occurrence_ref_is_deterministic_span():
    text = "Dogs run."
    tree = dm.decompose(text)
    _, occ = lx.annotate(tree, StubAnalyzer(), doc_id="mydoc")
    assert occ[0].ref == f"mydoc@{occ[0].start}:{occ[0].end}"


def test_verb_filtering():
    text = "The dog ran home."
    tree = dm.decompose(text)
    _, occ = lx.annotate(tree, StubAnalyzer(), doc_id="d")
    verbs = [o.surface for o in occ if o.pos == "VERB"]
    assert verbs == ["ran"]


# ---------------------------------------------------------------------------
# RuleAnalyzer (dependency-free)
# ---------------------------------------------------------------------------

def test_rule_analyzer_normalises_safe_plurals():
    lemmas = dict(zip(["dogs", "studies", "boxes", "grass"],
                      [l for l, _ in lx.RuleAnalyzer().analyze(["dogs", "studies", "boxes", "grass"])]))
    assert lemmas == {"dogs": "dog", "studies": "study", "boxes": "box", "grass": "grass"}


# ---------------------------------------------------------------------------
# SpacyAnalyzer (skipped when spaCy / model absent)
# ---------------------------------------------------------------------------

def test_spacy_analyzer_lemmatises_and_tags_in_context():
    pytest.importorskip("spacy")
    try:
        analyzer = lx.SpacyAnalyzer("en")
    except OSError:
        pytest.skip("en_core_web_sm not installed")
    pairs = analyzer.analyze(["She", "ran", "and", "leaves"])
    assert ("run", "VERB") in pairs
    assert ("leave", "VERB") in pairs   # 'leaves' tagged as a verb in context


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _write(tmp_path, text):
    p = tmp_path / "doc.txt"
    p.write_text(text, encoding="utf-8")
    return p


def test_cli_emits_occurrences_json_with_valid_offsets(tmp_path, capsys):
    text = "The dogs run home."
    src = _write(tmp_path, text)
    lx.main([str(src), "--backend", "rule", "-q"])
    data = json.loads(capsys.readouterr().out)
    assert data
    for o in data:
        assert text[o["start"]:o["end"]] == o["surface"]


def test_cli_writes_registry_file(tmp_path):
    src = _write(tmp_path, "The dogs run.")
    reg_file = tmp_path / "reg.json"
    lx.main([str(src), "--backend", "rule", "--registry", str(reg_file), "-q"])
    data = json.loads(reg_file.read_text(encoding="utf-8"))
    assert data["schema"] == 1 and data["lexemes"]


def test_cli_missing_file_exits(tmp_path):
    with pytest.raises(SystemExit):
        lx.main([str(tmp_path / "nope.txt")])


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------

def test_format_lexeme_stats_reports_core_metrics():
    text = "She ran. He runs. They keep running."
    tree = dm.decompose(text)
    reg, occ = lx.annotate(tree, StubAnalyzer(), doc_id="d")
    stats = lx.format_lexeme_stats(reg, occ)
    assert "===== Lexeme statistics =====" in stats
    assert re.search(r"Occurrences \(tokens\) : \d+", stats)
    assert re.search(r"Type/token ratio     : \d\.\d{3}", stats)
    assert "By POS" in stats
    assert "Verbs" in stats


def test_format_lexeme_stats_handles_empty():
    assert lx.format_lexeme_stats(lx.Registry(), []) == "No lexemes extracted."


def test_cli_emits_stats_block_to_stderr(tmp_path, capsys):
    src = _write(tmp_path, "The dogs run home. The cat sleeps.")
    lx.main([str(src), "--backend", "rule"])
    err = capsys.readouterr().err
    assert "===== Lexeme statistics =====" in err
    assert "Type/token ratio" in err


# ---------------------------------------------------------------------------
# Language detection + routing (multi-language documents)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text, expected", [
    ("The aircraft climbed to a high altitude and levelled off slowly.", "en"),
    ("O Congresso Nacional promulgou a nova constituição federal do Brasil.", "pt"),
    ("Le pilote a perdu le contrôle de l'avion dans la tempête soudaine.", "fr"),
])
def test_detect_language_identifies_en_pt_fr(text, expected):
    assert lx.detect_language(text) == expected


def test_detect_language_defers_when_ambiguous_or_empty():
    assert lx.detect_language("") is None
    assert lx.detect_language("qwerty zxcvb hjkl") is None  # no stopwords → no signal


def test_router_routes_by_detected_language():
    router = lx.LanguageRouter(default_lang="en")
    router._analyzers = {"en": StubAnalyzer(), "fr": StubAnalyzer()}  # avoid loading models
    lang_en, _ = router.resolve("the dog and the cat are here".split())
    lang_fr, _ = router.resolve("le chien et le chat sont dans la rue".split())
    assert lang_en == "en"
    assert lang_fr == "fr"


def test_annotate_multilang_tags_language_per_segment():
    text = "The dog ran here today.\n\nLe chat et le chien sont dans la rue."
    tree = dm.decompose(text)
    router = lx.LanguageRouter(default_lang="en")
    router._analyzers = {"en": StubAnalyzer(), "fr": StubAnalyzer()}
    reg, occ = lx.annotate_multilang(tree, router, doc_id="d")
    by_surface = {o.surface.lower(): o.lang for o in occ}
    assert by_surface["dog"] == "en"      # first segment → English (default)
    assert by_surface["chien"] == "fr"    # second segment → detected French
    # The registry namespaces the two languages apart.
    assert any(l["lang"] == "fr" for l in reg.lexemes())


def test_occurrence_to_dict_includes_lang():
    o = lx.Occurrence("lx_en.v_x", "ran", "run", "VERB", "d", 0, 3, "en")
    assert o.to_dict()["lang"] == "en"


def test_stats_includes_language_breakdown():
    text = "The dog ran here.\n\nLe chat est noir ici maintenant."
    tree = dm.decompose(text)
    router = lx.LanguageRouter(default_lang="en")
    router._analyzers = {"en": StubAnalyzer(), "fr": StubAnalyzer()}
    reg, occ = lx.annotate_multilang(tree, router, doc_id="d")
    assert "By language" in lx.format_lexeme_stats(reg, occ)


def test_cli_detect_reports_language_breakdown(tmp_path, capsys):
    text = ("The dog and the cat are running here today.\n\n"
            "Le chien et le chat sont dans la rue maintenant.\n")
    src = _write(tmp_path, text)
    lx.main([str(src), "--detect"])
    assert "By language" in capsys.readouterr().err
