"""Memory Market against a real Provenance Ledger, for two gaps the Agent Memory Atlas re-read of
25d6b1a named (2026-11-25):

* reconcile_provenance linked a ``created`` receipt by content hash alone, so a receipt anyone
  appended for an unattested public unit became that unit's own. It now takes only the owner's.
* The market keeps each unit's receipt id and chain root, and nothing compared them with the
  ledger, whose own chain_head check compares the ledger database with itself.
  ``reconcile_provenance --check`` does that comparison.
"""
import pytest

from memory_market.clients import receipt_parents, receipt_refs
from memory_market.models import MemoryCreate
from memory_market.reconcile_provenance import check, reconcile
from memory_market.store import MemoryStore
from provenance_ledger.crypto import Signer
from provenance_ledger.models import AttestationCreate
from provenance_ledger.store import Ledger


class LedgerClients:
    """TrustClients' ledger calls, answered by a Ledger object instead of HTTP."""

    def __init__(self, ledger: Ledger):
        self.ledger = ledger

    def lineage(self, memory_id):
        return self.ledger.lineage(memory_id).model_dump(mode="json")

    def verify_receipt(self, receipt_id):
        try:
            return self.ledger.verify(receipt_id).model_dump(mode="json")
        except KeyError:
            return {"receipt_id": receipt_id, "valid": False, "checks": {"exists": False}}

    def created_receipt(self, unit):
        from memory_market.clients import TrustClients

        probe = TrustClients.__new__(TrustClients)
        probe.lineage = self.lineage
        return TrustClients.created_receipt(probe, unit)

    def attest(self, unit, event_type, actor_id, metadata=None):
        return self.ledger.append(AttestationCreate(
            memory_id=unit.id, event_type=event_type, content_hash=unit.content_hash, actor_id=actor_id,
            source_refs=receipt_refs(unit), parent_memory_ids=receipt_parents(unit), metadata=metadata or {},
        )).model_dump(mode="json")


@pytest.fixture()
def pair(tmp_path, monkeypatch):
    monkeypatch.delenv("ATTESTED_PQC_ENABLED", raising=False)
    store = MemoryStore(str(tmp_path / "market.sqlite3"))
    ledger = Ledger(str(tmp_path / "ledger.sqlite3"), Signer(str(tmp_path / "ledger.pem")))
    return store, ledger, LedgerClients(ledger)


def test_reconcile_does_not_link_a_created_receipt_someone_else_appended(pair):
    store, ledger, clients = pair
    unit = store.create(MemoryCreate(title="public", content="body", visibility="public"), "agent:owner")
    # A receipt for this unit's id and content in another actor's name (any operator-route caller;
    # through the hub the ledger now refuses it outright).
    planted = ledger.append(AttestationCreate(memory_id=unit.id, event_type="created",
                                              content_hash=unit.content_hash, actor_id="aimarket:bref-" + "e" * 32))
    assert reconcile(store, clients) == {"unattested": 1, "linked": 0, "attested": 1, "failed": 0}
    linked = store.get_unchecked(unit.id).provenance.receipt_id
    assert linked != planted.id
    assert ledger.get(linked).actor_id == "agent:owner"


def test_check_finds_a_chain_truncated_with_its_head_moved(pair):
    store, ledger, clients = pair
    kept = store.create(MemoryCreate(title="kept", content="one"), "agent:owner")
    cut = store.create(MemoryCreate(title="cut", content="two"), "agent:owner")
    assert reconcile(store, clients)["attested"] == 2
    second = clients.attest(cut, "verified", "truth-layer", {"status": "supported"})
    store.set_provenance(cut.id, second["id"], second["chain_root"], created=False)
    assert check(store, clients) == {"attested": 2, "matching": 2, "unreachable": 0, "mismatched": []}

    # Someone with write access to the ledger database deletes the newest receipt and moves the head
    # back. The ledger's own checks pass; the root the market kept does not match.
    first = ledger.lineage(cut.id).receipts[0]
    with ledger.connection:
        ledger.connection.execute("DELETE FROM receipts WHERE id = ?", (second["id"],))
        ledger.connection.execute("UPDATE receipt_heads SET chain_root = ? WHERE memory_id = ?",
                                  (first.chain_root, cut.id))
    assert ledger.verify(first.id).valid is True

    outcome = check(store, clients)
    assert outcome["matching"] == 1 and outcome["unreachable"] == 0
    assert outcome["mismatched"] == [{"memory_id": cut.id, "receipt_id": second["id"],
                                      "problem": "the recorded receipt is no longer in the ledger chain"}]


def test_check_finds_a_rewritten_receipt(pair):
    store, ledger, clients = pair
    unit = store.create(MemoryCreate(title="kept", content="one"), "agent:owner")
    reconcile(store, clients)
    receipt_id = store.get_unchecked(unit.id).provenance.receipt_id
    with ledger.connection:
        ledger.connection.execute("UPDATE receipts SET actor_id = 'someone' WHERE id = ?", (receipt_id,))
    problem = check(store, clients)["mismatched"][0]["problem"]
    assert problem.startswith("the ledger does not verify the receipt: ") and "event_hash" in problem


def test_a_payment_receipt_before_the_creation_receipt_leaves_the_unit_for_reconcile(pair):
    store, ledger, clients = pair
    unit = store.create(MemoryCreate(title="paid", content="body", visibility="paid", price_usdc="1.00"),
                        "agent:owner")
    shared = clients.attest(unit, "shared", "memory-market:payment", {"settlement": "sha256:" + "0" * 64})
    store.set_provenance(unit.id, shared["id"], shared["chain_root"], created=False)
    assert reconcile(store, clients) == {"unattested": 1, "linked": 0, "attested": 1, "failed": 0}
    assert [r.event_type for r in ledger.lineage(unit.id).receipts] == ["shared", "created"]
    assert check(store, clients)["matching"] == 1
