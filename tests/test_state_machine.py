import pytest
from warden.core.models import Incident, IncidentState as S
from warden.core.state_machine import IllegalTransition, transition


def test_happy_path():
    inc = Incident(rule="preempted")
    for s in (S.TRIAGED, S.DECIDED, S.EXECUTING, S.VERIFYING, S.RESOLVED):
        transition(inc, s)
    assert inc.state == S.RESOLVED and len(inc.timeline) == 5


def test_illegal_is_loud():
    inc = Incident(rule="x")
    with pytest.raises(IllegalTransition):
        transition(inc, S.RESOLVED)
