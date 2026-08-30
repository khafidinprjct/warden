"""Catalogue #41: `/ask` ran the Concierge through a sync wrapper from inside the event loop.

FastAPI runs an `async def` endpoint in the loop. `concierge.ask()` calls `asyncio.run()`, which raises
"asyncio.run() cannot be called from a running event loop" — so every question asked through the dashboard failed and
wrote a red `gemini` health row. The endpoint must await the coroutine.
"""
from __future__ import annotations

import inspect
import os

os.environ.setdefault("FIRESTORE_EMULATOR_HOST", "127.0.0.1:8081")
os.environ.setdefault("WARDEN_PROJECT", "warden-local")
os.environ.setdefault("WARDEN_FIRESTORE_DB", "warden-test")
os.environ.setdefault("WARDEN_PROVIDER", "fake")
os.environ.setdefault("WARDEN_DEV", "1")

import warden.main as main  # noqa: E402


def _async_endpoints():
    for r in main.app.routes:
        fn = getattr(r, "endpoint", None)
        if fn and inspect.iscoroutinefunction(fn):
            yield r.path, fn


def test_no_async_endpoint_calls_a_sync_asyncio_run_wrapper():
    """An async endpoint may not call the sync wrappers; they would raise inside the running loop."""
    banned = {"diagnose", "investigate", "ask"}          # the sync faces of the agent coroutines
    offenders = []
    for path, fn in _async_endpoints():
        src = _code_only(inspect.getsource(fn))
        for name in banned:
            # an import of the sync wrapper, or a bare call to it, inside an async endpoint
            if f"import {name} as" in src or f"import {name}\n" in src or f" {name}(" in src.replace("await ", ""):
                if f"await {name}_async(" not in src:
                    offenders.append(f"{path} → {name}")
    assert not offenders, f"async endpoints calling a sync asyncio.run wrapper: {offenders}"


def _code_only(src: str) -> str:
    """Comments explaining the bug mention it by name; assert against the code, not the prose."""
    return "\n".join(line.split("#", 1)[0] for line in src.splitlines())


def test_ask_awaits_the_coroutine():
    src = _code_only(inspect.getsource(main.ask))
    assert "await ask_async(" in src, "/ask must await the Concierge coroutine"
    assert "asyncio.run" not in src
