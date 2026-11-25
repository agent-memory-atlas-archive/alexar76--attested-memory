import pytest
import hashlib
import contextvars
from types import SimpleNamespace

from provenance_ledger.crypto import Signer
from provenance_ledger.models import AttestationCreate
from provenance_ledger.store import ChainHeadMissing, Ledger
from provenance_ledger.db import Database
from fastapi import Response
import provenance_ledger.app as app_module


def test_append_chain_and_verify(tmp_path):
    ledger = Ledger(str(tmp_path / "ledger.sqlite3"), Signer(str(tmp_path / "key.pem")))
    content_hash = "sha256:" + hashlib.sha256(b"portable memory").hexdigest()
    first = ledger.append(AttestationCreate(
        memory_id="mem_1234567890abcdef12345678", event_type="created",
        content_hash=content_hash, actor_id="agent:test", source_refs=["https://example.test/source"],
    ))
    second = ledger.append(AttestationCreate(
        memory_id=first.memory_id, event_type="verified", content_hash=content_hash,
        actor_id="truth-layer", metadata={"status": "supported"},
    ))

    assert second.previous_root == first.chain_root
    assert ledger.verify(first.id).valid is True
    assert ledger.verify(second.id).valid is True
    assert ledger.lineage(first.memory_id).chain_root == second.chain_root
    assert len(ledger.lineage(first.memory_id).receipts) == 2
    head = ledger.connection.execute(
        "SELECT chain_root FROM receipt_heads WHERE memory_id = ?", (first.memory_id,)
    ).fetchone()
    assert head["chain_root"] == second.chain_root


def _pre_head_database(ledger):
    """Turn a ledger database into one from before receipt_heads existed."""
    with ledger.connection:
        ledger.connection.execute("DELETE FROM receipt_heads")
        ledger.connection.execute("DELETE FROM ledger_meta")


def test_migration_backfills_chain_head_once(tmp_path):
    db_path = str(tmp_path / "ledger.sqlite3")
    key_path = str(tmp_path / "key.pem")
    ledger = Ledger(db_path, Signer(key_path))
    receipt = ledger.append(AttestationCreate(
        memory_id="mem_1234567890abcdef12345678", event_type="created",
        content_hash="sha256:" + "c" * 64, actor_id="agent:test",
    ))
    _pre_head_database(ledger)
    ledger.connection.close()

    reopened = Ledger(db_path, Signer(key_path))
    row = reopened.connection.execute(
        "SELECT chain_root FROM receipt_heads WHERE memory_id = ?", (receipt.memory_id,)
    ).fetchone()
    assert row["chain_root"] == receipt.chain_root
    assert reopened.verify(receipt.id).valid is True


def test_a_deleted_head_is_not_rebuilt_and_the_chain_is_not_extended(tmp_path, monkeypatch):
    """Atlas re-read 2026-11-25: the backfill ran at every start, so deleting receipts and then the
    head row was healed from the receipts that were left, and verify passed again."""
    monkeypatch.delenv("ATTESTED_PQC_ENABLED", raising=False)
    db_path, key_path = str(tmp_path / "ledger.sqlite3"), str(tmp_path / "key.pem")
    ledger = Ledger(db_path, Signer(key_path))
    memory_id = "mem_1234567890abcdef12345678"
    first = ledger.append(AttestationCreate(
        memory_id=memory_id, event_type="created", content_hash="sha256:" + "f" * 64, actor_id="agent:test"))
    second = ledger.append(AttestationCreate(
        memory_id=memory_id, event_type="verified", content_hash="sha256:" + "f" * 64, actor_id="truth-layer"))
    with ledger.connection:
        ledger.connection.execute("DELETE FROM receipts WHERE id = ?", (second.id,))
        ledger.connection.execute("DELETE FROM receipt_heads WHERE memory_id = ?", (memory_id,))
    ledger.connection.close()

    reopened = Ledger(db_path, Signer(key_path))
    result = reopened.verify(first.id)
    assert result.checks["chain_head"] is False and result.valid is False
    with pytest.raises(ChainHeadMissing):
        reopened.append(AttestationCreate(
            memory_id=memory_id, event_type="updated", content_hash="sha256:" + "f" * 64, actor_id="agent:test"))
    assert [r.id for r in reopened.lineage(memory_id).receipts] == [first.id]


def test_tamper_is_detected(tmp_path):
    ledger = Ledger(str(tmp_path / "ledger.sqlite3"), Signer(str(tmp_path / "key.pem")))
    receipt = ledger.append(AttestationCreate(
        memory_id="mem_1234567890abcdef12345678", event_type="created",
        content_hash="sha256:" + "a" * 64, actor_id="agent:test",
    ))
    with ledger.connection:
        ledger.connection.execute("UPDATE receipts SET actor_id = 'attacker' WHERE id = ?", (receipt.id,))
    result = ledger.verify(receipt.id)
    assert result.valid is False
    assert result.checks["event_hash"] is False


def test_pq_requirement_fails_closed_for_classical_receipt(tmp_path, monkeypatch):
    monkeypatch.delenv("ATTESTED_PQC_ENABLED", raising=False)
    signer = Signer(str(tmp_path / "key.pem"))
    ledger = Ledger(str(tmp_path / "ledger.sqlite3"), signer)
    receipt = ledger.append(AttestationCreate(
        memory_id="mem_1234567890abcdef12345678", event_type="created",
        content_hash="sha256:" + "b" * 64, actor_id="agent:test",
    ))
    monkeypatch.setenv("ATTESTED_PQC_REQUIRE", "1")
    result = ledger.verify(receipt.id)
    assert result.valid is False
    assert result.checks["pq_signature"] is False


def test_postgres_transaction_executes_on_checked_out_connection():
    calls = []

    class Connection:
        def execute(self, sql, params):
            calls.append((sql, params))
            return "cursor"

    db = object.__new__(Database)
    db._pool = object()
    db._tx = contextvars.ContextVar("provenance_test_tx", default=None)
    db._tx.set((object(), Connection()))

    assert db.execute("SELECT ?", (7,)) == "cursor"
    assert calls == [("SELECT %s", (7,))]


def test_health_fails_with_503_when_database_is_down(monkeypatch):
    class Connection:
        is_postgres = True

        def execute(self, *_args):
            raise RuntimeError("database unavailable")

    fake_ledger = SimpleNamespace(
        connection=Connection(), signer=SimpleNamespace(pq_public_key="pq"),
    )
    monkeypatch.setattr(app_module, "ledger", lambda: fake_ledger)
    response = Response()
    payload = app_module.health(response)
    assert response.status_code == 503
    assert payload["status"] == "degraded"
    assert payload["database"] == {"backend": "postgresql", "healthy": False}


def test_hub_attestation_actor_is_the_named_buyer_or_plainly_anonymous():
    """It was sha256 of X-Payment-Channel: one made-up actor for every buyer the hub sent an
    empty header for (credits, x402, trial). Now the hub's buyer ref, else aimarket:anonymous."""
    from fastapi import Request

    from provenance_ledger.app import _aimarket_actor

    def req(headers: dict) -> Request:
        return Request({"type": "http", "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()]})

    ref = "bref-" + "c" * 32
    assert _aimarket_actor(req({"X-AIMarket-Buyer-Ref": ref})) == "aimarket:" + ref
    for headers in ({}, {"X-Payment-Channel": ""}, {"X-AIMarket-Buyer-Ref": "bref-zz"}):
        assert _aimarket_actor(req(headers)) == "aimarket:anonymous"


def _rewrite_and_resign(ledger, receipt, attacker_key_path):
    """What someone who can write the database can do: change a field, recompute both hashes
    and sign the new root with a key of their own, which the row then carries."""
    import json
    from provenance_ledger.store import canonical_json, sha256

    attacker = Signer(attacker_key_path)
    event = {
        "id": receipt.id, "memory_id": receipt.memory_id, "event_type": receipt.event_type,
        "content_hash": receipt.content_hash, "actor_id": "attacker",
        "source_refs": receipt.source_refs, "parent_memory_ids": receipt.parent_memory_ids,
        "metadata": receipt.metadata, "created_at": receipt.created_at,
    }
    event_hash = sha256(canonical_json(event))
    chain_root = sha256(receipt.previous_root + ":" + event_hash)
    with ledger.connection:
        ledger.connection.execute(
            "UPDATE receipts SET actor_id='attacker', event_hash=?, chain_root=?, signature=?, public_key=? WHERE id=?",
            (event_hash, chain_root, attacker.sign(chain_root), attacker.public_key, receipt.id),
        )
        ledger.connection.execute(
            "UPDATE receipt_heads SET chain_root=? WHERE memory_id=?", (chain_root, receipt.memory_id))
    assert json.dumps(event)  # the forged event is well-formed


def test_a_row_re_signed_with_another_key_is_rejected(tmp_path, monkeypatch):
    monkeypatch.delenv("ATTESTED_PQC_ENABLED", raising=False)
    ledger = Ledger(str(tmp_path / "ledger.sqlite3"), Signer(str(tmp_path / "key.pem")))
    receipt = ledger.append(AttestationCreate(
        memory_id="mem_1234567890abcdef12345678", event_type="created",
        content_hash="sha256:" + "d" * 64, actor_id="agent:test",
    ))
    _rewrite_and_resign(ledger, receipt, str(tmp_path / "attacker.pem"))
    result = ledger.verify(receipt.id)
    # The hashes and the signature are internally consistent; only the key gives it away.
    assert result.checks["event_hash"] and result.checks["chain_root"] and result.checks["signature"]
    assert result.checks["signer_key"] is False
    assert result.valid is False


def test_an_earlier_key_listed_as_trusted_still_verifies(tmp_path, monkeypatch):
    monkeypatch.delenv("ATTESTED_PQC_ENABLED", raising=False)
    old = Signer(str(tmp_path / "old.pem"))
    ledger = Ledger(str(tmp_path / "ledger.sqlite3"), old)
    receipt = ledger.append(AttestationCreate(
        memory_id="mem_1234567890abcdef12345678", event_type="created",
        content_hash="sha256:" + "e" * 64, actor_id="agent:test",
    ))
    rotated = Ledger(str(tmp_path / "ledger.sqlite3"), Signer(str(tmp_path / "new.pem")))
    assert rotated.verify(receipt.id).checks["signer_key"] is False
    monkeypatch.setenv("PROVENANCE_TRUSTED_PUBLIC_KEYS", old.public_key)
    assert rotated.verify(receipt.id).valid is True


def test_deleting_the_newest_receipts_of_a_chain_is_detected(tmp_path, monkeypatch):
    monkeypatch.delenv("ATTESTED_PQC_ENABLED", raising=False)
    ledger = Ledger(str(tmp_path / "ledger.sqlite3"), Signer(str(tmp_path / "key.pem")))
    memory_id = "mem_1234567890abcdef12345678"
    first = ledger.append(AttestationCreate(
        memory_id=memory_id, event_type="created", content_hash="sha256:" + "f" * 64, actor_id="agent:test"))
    second = ledger.append(AttestationCreate(
        memory_id=memory_id, event_type="verified", content_hash="sha256:" + "f" * 64, actor_id="truth-layer"))
    assert ledger.verify(first.id).valid is True
    with ledger.connection:
        ledger.connection.execute("DELETE FROM receipts WHERE id = ?", (second.id,))
    result = ledger.verify(first.id)
    assert result.checks["previous_link"] is True and result.checks["signature"] is True
    assert result.checks["chain_head"] is False
    assert result.valid is False


def test_hub_attest_refuses_an_anonymous_caller_but_reads_stay_open(tmp_path, monkeypatch):
    """memory.attest@v1 appended for aimarket:anonymous, taking memory_id and content_hash from the
    caller; Memory Market's capability route already refused anonymous writes."""
    from fastapi.testclient import TestClient

    monkeypatch.delenv("ATTESTED_PQC_ENABLED", raising=False)
    test_ledger = Ledger(str(tmp_path / "ledger.sqlite3"), Signer(str(tmp_path / "key.pem")))
    monkeypatch.setattr(app_module, "ledger", lambda: test_ledger)
    client = TestClient(app_module.app)
    body = {"input": {"memory_id": "note-1234567890", "event_type": "created",
                      "content_hash": "sha256:" + "9" * 64}}
    product = app_module.BY_CAPABILITY["memory.attest@v1"].product_id
    url = f"/capabilities/{product}/memory.attest@v1/invoke"

    refused = client.post(url, json=body)
    assert refused.status_code == 403
    assert test_ledger.lineage("note-1234567890").receipts == []

    named = client.post(url, json=body, headers={"X-AIMarket-Buyer-Ref": "bref-" + "a" * 32})
    assert named.status_code == 200, named.text
    receipt = named.json()["result"]
    assert receipt["actor_id"] == "aimarket:bref-" + "a" * 32

    product_l = app_module.BY_CAPABILITY["lineage.get@v1"].product_id
    lineage = client.post(f"/capabilities/{product_l}/lineage.get@v1/invoke",
                          json={"input": {"memory_id": "note-1234567890"}})
    assert lineage.status_code == 200 and len(lineage.json()["result"]["receipts"]) == 1


def test_hub_buyers_extend_only_their_own_chains(tmp_path, monkeypatch):
    """Atlas re-read 2026-11-25: any named buyer could append any event with any hash to any memory
    id, including a `created` receipt on another owner's Memory Market unit."""
    from fastapi.testclient import TestClient

    monkeypatch.delenv("ATTESTED_PQC_ENABLED", raising=False)
    test_ledger = Ledger(str(tmp_path / "ledger.sqlite3"), Signer(str(tmp_path / "key.pem")))
    monkeypatch.setattr(app_module, "ledger", lambda: test_ledger)
    client = TestClient(app_module.app)
    url = f"/capabilities/{app_module.BY_CAPABILITY['memory.attest@v1'].product_id}/memory.attest@v1/invoke"
    alice, bob = "bref-" + "a" * 32, "bref-" + "b" * 32

    def attest(memory_id, buyer, event="updated"):
        return client.post(url, headers={"X-AIMarket-Buyer-Ref": buyer}, json={"input": {
            "memory_id": memory_id, "event_type": event, "content_hash": "sha256:" + "9" * 64}})

    # Outside the market's ids, the first buyer starts the chain and only they extend it.
    assert attest("note-alice-1", alice, "created").status_code == 200
    assert attest("note-alice-1", alice).status_code == 200
    refused = attest("note-alice-1", bob, "created")
    assert refused.status_code == 403 and "another actor" in refused.json()["detail"]

    # A Memory Market id: nobody starts it through the hub, not even its future owner.
    market_id = "mem_" + "1" * 24
    assert attest(market_id, alice, "created").status_code == 403
    assert test_ledger.lineage(market_id).receipts == []
    # The market starts it (operator route) in the owner's name; then the owner may extend, others not.
    test_ledger.append(AttestationCreate(memory_id=market_id, event_type="created",
                                         content_hash="sha256:" + "9" * 64, actor_id="aimarket:" + alice))
    assert attest(market_id, alice).status_code == 200
    assert attest(market_id, bob, "created").status_code == 403
    assert [r.actor_id for r in test_ledger.lineage(market_id).receipts] == ["aimarket:" + alice] * 2
