"""The OCR language packs the Dockerfile installs must actually exist.

`tesseract-ocr-deu-frak` was requested for German Fraktur. Debian names that pack
`tesseract-ocr-frk`, so `apt-get install` failed with `Unable to locate package`
and exit 100 — breaking every image build, on a line no test covered.

The failure is cheap to prevent and expensive to hit: it surfaces only when
someone rebuilds, which on this project is during a deploy.
"""

from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_DOCKERFILE = (_ROOT / "Dockerfile").read_text(encoding="utf-8")

#: Debian ships one pack per Tesseract language code, named
#: `tesseract-ocr-<code>`. Codes are three letters, optionally with a script
#: suffix (`chi_sim`); a hyphenated name like `deu-frak` is not a language code
#: and therefore not a package.
#:
#: The suffix class MUST include the hyphen. Excluding it makes the pattern stop
#: at the first hyphen, capturing `deu` from `tesseract-ocr-deu-frak` — so the
#: guard would have passed on the exact Dockerfile that failed to build.
_PACKAGE_RE = re.compile(r"tesseract-ocr-([a-z_-]+?)(?=\s|\\|$)", re.MULTILINE)

#: Packages that are not language packs and so are exempt from the code shape.
_NON_LANGUAGE = {"script"}


def _requested_codes() -> set[str]:
    return {code for code in _PACKAGE_RE.findall(_DOCKERFILE) if code not in _NON_LANGUAGE}


def test_every_language_pack_name_is_a_tesseract_language_code() -> None:
    """A hyphen in the suffix means the package name was invented, not looked up."""
    bad = {code for code in _requested_codes() if not re.fullmatch(r"[a-z]{3}(_[a-z]+)?", code)}
    assert not bad, (
        f"not Tesseract language codes, so no such Debian package: {sorted(bad)}. "
        "German Fraktur is `frk` (package tesseract-ocr-frk), not `deu-frak`."
    )


def test_the_fraktur_pack_uses_the_code_the_application_asks_tesseract_for() -> None:
    """The installed pack and the requested language must be the same code.

    ports/patent_text_source.py documents `"deu+frk"`; installing a pack under any
    other name leaves that request unsatisfied at run time even if the build
    happens to succeed.
    """
    source = (_ROOT / "src" / "doc_ray" / "ports" / "patent_text_source.py").read_text(
        encoding="utf-8"
    )
    assert "deu+frk" in source, "the Fraktur example moved; re-derive the expected code"
    assert "frk" in _requested_codes(), "the image must install the pack the code requests"


def test_english_is_always_installed() -> None:
    """`eng` is the default for both OCR paths, so its absence breaks extraction."""
    assert "eng" in _requested_codes()
