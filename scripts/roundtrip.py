"""Roundtrip fidelity check.

Closes the loop:  original PDF  ->  (ingest -> DB -> export JSON)  ->  reconstruct
by re-extracting the PDF FRESH and comparing against what we exported.

Layers compared:
  1. bytes      : sha256(pdf) vs stored content_sha256
  2. full_text  : fresh extraction vs exported text (exact) + text_sha256
  3. sentences  : fresh segmentation vs exported sentences (count, text, offsets)

Run (in the container, whose pypdf matches the recorded provenance version):
    docker compose run --rm --entrypoint python ingester \
        /data/roundtrip.py /data/static/fixtures/AirFrance447_BEA.pdf \
                           /data/exports/airfrance.json
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

# This script lives in scripts/; put the repo root on the path so `ingest` (a
# top-level package) is importable when run directly as `python scripts/roundtrip.py`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ingest import extract as extract_mod
from ingest import sentences as sent_mod


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _first_diff(a: str, b: str) -> str:
    n = min(len(a), len(b))
    for i in range(n):
        if a[i] != b[i]:
            return (f"first diff at char {i}: "
                    f"stored={a[i-20:i+20]!r} vs fresh={b[i-20:i+20]!r}")
    if len(a) != len(b):
        return f"lengths differ: stored={len(a)} fresh={len(b)} (common prefix ok)"
    return "identical"


def main(argv):
    pdf_path = Path(argv[0])
    export_path = Path(argv[1])

    exp = json.loads(export_path.read_text(encoding="utf-8"))["documents"][0]
    doc = exp["document"]
    exp_text = exp["content"]["full_text"]
    exp_sents = exp["sentences"]

    data = pdf_path.read_bytes()
    fresh = extract_mod.extract(data, filename=pdf_path.stem, extension=pdf_path.suffix)
    fresh_text = fresh.full_text or ""
    fresh_sents = sent_mod.build_sentences(fresh_text)

    checks = []

    # 1. bytes
    checks.append(("bytes: sha256(pdf) == content_sha256",
                   _sha(data) == doc["content_sha256"],
                   f"{_sha(data)[:16]} vs {doc['content_sha256'][:16]}"))
    checks.append(("bytes: size == byte_size",
                   len(data) == doc["byte_size"],
                   f"{len(data)} vs {doc['byte_size']}"))
    checks.append(("meta: page_count",
                   fresh.page_count == doc["page_count"],
                   f"{fresh.page_count} vs {doc['page_count']}"))

    # 2. full_text
    text_equal = fresh_text == exp_text
    checks.append(("text: fresh extraction == exported full_text",
                   text_equal,
                   "identical" if text_equal else _first_diff(exp_text, fresh_text)))
    checks.append(("text: sha256(fresh) == stored text_sha256",
                   _sha(fresh_text.encode()) == doc["text_sha256"],
                   f"{_sha(fresh_text.encode())[:16]} vs {doc['text_sha256'][:16]}"))

    # 3. sentences
    checks.append(("sentences: count",
                   len(fresh_sents) == len(exp_sents),
                   f"{len(fresh_sents)} vs {len(exp_sents)}"))

    mismatches = 0
    first_bad = None
    for i, (a, b) in enumerate(zip(fresh_sents, exp_sents)):
        if (a["text"] != b["text"] or a["char_start"] != b["char_start"]
                or a["char_end"] != b["char_end"]):
            mismatches += 1
            if first_bad is None:
                first_bad = (i, a["text"][:50], b["text"][:50])
    checks.append((f"sentences: per-item text+offsets ({len(exp_sents)})",
                   mismatches == 0,
                   "all identical" if mismatches == 0
                   else f"{mismatches} differ; first @#{first_bad[0]}: "
                        f"{first_bad[1]!r} vs {first_bad[2]!r}"))

    # ULIDs are freshly minted each run (time+random) -> they SHOULD differ.
    ulids_differ = all(a["id"] != b["id"]
                       for a, b in zip(fresh_sents, exp_sents)) if fresh_sents else True
    checks.append(("sentences: ULIDs are fresh (differ by design)",
                   ulids_differ, "new ULIDs each run — expected"))

    # report
    width = max(len(name) for name, _, _ in checks)
    all_ok = True
    print(f"\nRoundtrip: {pdf_path.name}\n{'='*72}")
    for name, ok, detail in checks:
        all_ok &= ok
        print(f"  [{'PASS' if ok else 'FAIL'}] {name:<{width}}  {detail}")
    print("=" * 72)
    print("RESULT:", "ALL PASS — stored data faithfully reproduces the source"
          if all_ok else "MISMATCH — see FAIL rows above")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
