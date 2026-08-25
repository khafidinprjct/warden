from nacl.signing import SigningKey
from warden.concierge.discord import build_card, verify_signature
from warden.core.models import Action, Decision, Incident, Verdict


def test_ed25519_verify():
    sk = SigningKey.generate(); pk = sk.verify_key.encode().hex()
    body = b'{"type":1}'; ts = "1700000000"
    sig = sk.sign(ts.encode() + body).signature.hex()
    assert verify_signature(pk, sig, ts, body)
    assert not verify_signature(pk, sig, "1700000001", body)


def test_card_has_buttons_when_awaiting():
    inc = Incident(rule="preempted", severity="critical", job_id="j", instance_ref="z/vm", summary="x")
    dec = Decision(action=Action.START_INSTANCE, verdict=Verdict.NEED_APPROVAL, explain=["L1"])
    card = build_card(inc, dec, "usul start")
    ids = [c["custom_id"] for c in card["components"][0]["components"]]
    assert ids == [f"warden:approve:{dec.decision_id}", f"warden:deny:{dec.decision_id}", f"warden:always:{dec.decision_id}"]
    assert all(len(i) <= 100 for i in ids)
