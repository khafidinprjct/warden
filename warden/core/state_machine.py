"""Mesin status insiden. Transisi hanya lewat tabel eksplisit; ilegal = exception (fail-loud)."""
from __future__ import annotations

from warden.core.models import Incident, IncidentState as S, now

TRANSITIONS: dict[S, set[S]] = {
    S.DETECTED: {S.TRIAGED, S.SUPPRESSED, S.FALSE_POSITIVE},
    S.TRIAGED: {S.DIAGNOSING, S.DECIDED, S.FALSE_POSITIVE},        # DECIDED langsung utk aturan tanpa LLM
    S.DIAGNOSING: {S.DIAGNOSED, S.ESCALATED},
    S.DIAGNOSED: {S.DECIDED, S.ESCALATED},
    S.DECIDED: {S.EXECUTING, S.AWAITING_APPROVAL, S.HELD, S.ESCALATED, S.RESOLVED},
    S.AWAITING_APPROVAL: {S.EXECUTING, S.CLOSED, S.ESCALATED},
    S.HELD: {S.DECIDED, S.ESCALATED},
    S.EXECUTING: {S.VERIFYING, S.FAILED_ACTION},
    S.VERIFYING: {S.RESOLVED, S.FAILED_ACTION, S.ESCALATED},   # tindakan sukses tapi manusia masih perlu (mis. karantina → rerun)
    S.FAILED_ACTION: {S.ESCALATED, S.DECIDED},
    S.ESCALATED: {S.RESOLVED, S.CLOSED, S.DECIDED},
    S.RESOLVED: set(),
    S.CLOSED: set(),
    S.SUPPRESSED: set(),
    S.FALSE_POSITIVE: set(),
}


class IllegalTransition(Exception):
    pass


def transition(inc: Incident, to: S, note: str = "", actor: str = "warden") -> Incident:
    if to not in TRANSITIONS[inc.state]:
        raise IllegalTransition(f"{inc.incident_id}: {inc.state} -> {to} tidak diizinkan")
    inc.timeline.append({"ts": now().isoformat(), "from": inc.state, "to": to, "note": note, "actor": actor})
    inc.state = to
    inc.updated_at = now()
    return inc
