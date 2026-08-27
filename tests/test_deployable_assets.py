"""Guard for catalogue #36: files the *deployed service* reads at runtime must live inside the shipped image.

The nightly gold evaluation (/eval) ran in Cloud Run, where `.gcloudignore` excludes `tests/`, `docs/`, `chaos/` and `data/`.
The gold set used to live under `tests/fixtures/gold`, so every nightly attempt crashed with FileNotFoundError. These tests fail
on the same mistake being made again — for the gold set and for any other runtime asset placed outside the package.
"""
from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "warden"
IGNORED = [line.strip().rstrip("/") for line in (ROOT / ".gcloudignore").read_text().splitlines()
           if line.strip() and not line.startswith("#")]


def test_gold_set_lives_inside_the_shipped_package():
    from warden.eval import gold
    assert gold.FIX.is_relative_to(PKG), f"gold set at {gold.FIX} is outside {PKG} and would not ship to Cloud Run"
    top = gold.FIX.relative_to(ROOT).parts[0]
    assert top not in IGNORED, f".gcloudignore excludes '{top}', so the gold set would be missing in the image"


def test_every_gold_case_file_is_present_and_readable():
    from warden.eval import gold
    threshold, cases = gold.load_cases()
    assert 0 < threshold <= 1 and cases, "gold set must declare a threshold and at least one case"
    for c in cases:
        f = gold.FIX / c["file"]
        assert f.is_file(), f"gold case file missing: {f}"
        assert f.read_text(errors="ignore").strip(), f"gold case file empty: {f}"


def test_gold_cases_yaml_is_the_one_the_package_ships():
    from warden.eval import gold
    d = yaml.safe_load((gold.FIX / "cases.yaml").read_text())
    assert {c["file"] for c in d["cases"]} <= {p.name for p in gold.FIX.iterdir()}
