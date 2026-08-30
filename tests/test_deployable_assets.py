"""Guard for catalogue #36: files the *deployed service* reads at runtime must survive `.gcloudignore`.

The nightly gold evaluation (/eval) runs inside the Cloud Run image. The gold set first lived under `tests/fixtures/gold`
(excluded by the `tests/` rule), and after it moved into the package its `.log` case files were still dropped by the blanket
`*.log` rule — the evaluation failed twice for the same reason at two different layers. These tests evaluate every runtime
asset against the ignore file the way gcloud does (last matching pattern wins, `!` re-includes).

`.gitignore` drops the same files for a different reason: the gold logs were never committed at all, so a clean clone
could not run the evaluation (catalogue #37, found by the N2 clean-clone gate). Both ignore files are checked here.

The authoritative check is `gcloud meta list-files-for-upload .`; this is its offline equivalent, so the failure shows up in
pytest rather than in production at 02:00.
"""
from __future__ import annotations

import subprocess
from fnmatch import fnmatch
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "warden"


def _patterns() -> list[tuple[str, bool]]:
    out = []
    for line in (ROOT / ".gcloudignore").read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        neg = line.startswith("!")
        out.append((line[1:] if neg else line, neg))
    return out


def is_uploaded(rel: str) -> bool:
    """True if `rel` (a repo-relative posix path) would be sent to Cloud Build."""
    ignored = False
    for pat, neg in _patterns():
        if pat.endswith("/"):
            hit = rel == pat[:-1] or rel.startswith(pat)
        elif "/" in pat:
            hit = fnmatch(rel, pat)
        else:
            hit = fnmatch(Path(rel).name, pat)
        if hit:
            ignored = not neg
    return not ignored


def test_the_matcher_agrees_with_the_rules_we_rely_on():
    # a matcher that never excludes anything would make every other test in this file pass vacuously
    assert not is_uploaded("tests/test_rules.py")
    assert not is_uploaded("chaos/run.py")
    assert not is_uploaded("some/other/place/train.log")
    assert is_uploaded("warden/main.py")


def test_gold_set_lives_inside_the_shipped_package():
    from warden.eval import gold
    assert gold.FIX.is_relative_to(PKG), f"gold set at {gold.FIX} is outside {PKG} and would not ship to Cloud Run"


def test_every_gold_case_file_is_present_and_uploaded():
    from warden.eval import gold
    threshold, cases = gold.load_cases()
    assert 0 < threshold <= 1 and cases, "gold set must declare a threshold and at least one case"
    for name in ["cases.yaml"] + [c["file"] for c in cases]:
        f = gold.FIX / name
        assert f.is_file(), f"gold case file missing: {f}"
        assert f.read_text(errors="ignore").strip(), f"gold case file empty: {f}"
        rel = f.relative_to(ROOT).as_posix()
        assert is_uploaded(rel), f".gcloudignore drops {rel}; the nightly /eval would crash on it in Cloud Run"


def test_gold_cases_yaml_is_the_one_the_package_ships():
    from warden.eval import gold
    d = yaml.safe_load((gold.FIX / "cases.yaml").read_text())
    assert {c["file"] for c in d["cases"]} <= {p.name for p in gold.FIX.iterdir()}


def test_every_gold_case_file_is_committed():
    """A file that exists only in one working tree is not reproducible: a clean clone must be able to run the evaluation."""
    from warden.eval import gold
    tracked = subprocess.run(["git", "ls-files", "-z", str(gold.FIX.relative_to(ROOT))],
                             cwd=ROOT, capture_output=True, text=True)
    if tracked.returncode != 0:
        return                                     # not a git checkout (e.g. inside the deployed image) — nothing to assert
    committed = {Path(p).name for p in tracked.stdout.split("\0") if p}
    _, cases = gold.load_cases()
    missing = {"cases.yaml", *(c["file"] for c in cases)} - committed
    assert not missing, f"gold files present on disk but not committed: {sorted(missing)} — a clean clone cannot run /eval"


def test_procfile_is_the_core_entrypoint_not_the_dashboard():
    """The two services are built from one source tree, and buildpacks read `Procfile` to decide what runs.

    A UI deploy that writes `Procfile.ui` over `Procfile` and does not put it back leaves the repository pointing at
    the dashboard, so the next `gcloud run deploy warden-core --source .` silently ships the dashboard under the core's
    name: the watcher, the tick and every ingest endpoint stop existing, and `/health` still answers `{"ok":true}`
    because both apps have one. Catalogue #44 — production core was down for eleven minutes on 30 Aug.
    """
    core = (ROOT / "Procfile").read_text()
    ui = (ROOT / "Procfile.ui").read_text()
    assert "warden.main:app" in core, f"Procfile must launch the core; it says: {core.strip()!r}"
    assert "warden.ui2.app" not in core, f"Procfile is the dashboard's, not the core's: {core.strip()!r}"
    assert "warden.ui2.app" in ui, f"Procfile.ui must launch the dashboard; it says: {ui.strip()!r}"
