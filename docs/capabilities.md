# Capability catalogue

| Capability | Owner | MVP behavior |
| --- | --- | --- |
| `memory.write@v1` | memory-market | Persists a canonical Memory Unit and requests hash-only attestation |
| `memory.read@v1` | memory-market | Enforces visibility and explicit grants before returning content |
| `memory.search@v1` | memory-market | Metadata and text search over records visible to the caller; leaves out memories the Truth Layer rejected unless `truth` lists the statuses wanted (or `any`) |
| `memory.share@v1` | memory-market | Creates a revocable read grant |
| `memory.score@v1` | memory-market | One caller score per memory, aggregated without rewriting history |
| `memory.verify@v1` | truth-layer | Scores supplied evidence and records the result |
| `claim.check@v1` | truth-layer | Alias of the verification contract for standalone claims |
| `contradiction.scan@v1` | truth-layer | Deterministic conflict indicators; not semantic proof |
| `evidence.pack@v1` | truth-layer | Returns a portable content-addressed evidence pack |
| `memory.attest@v1` | provenance-ledger | Adds a signed event to a memory lineage; refuses a caller the hub does not name (anonymous or trial) |
| `lineage.get@v1` | provenance-ledger | Returns the ordered chain and its current root |
| `receipt.verify@v1` | provenance-ledger | Verifies canonical payload hash, chain link and hybrid Ed25519 + ML-DSA-65 signatures, that the signing keys are the ledger's own (or listed in `PROVENANCE_TRUSTED_PUBLIC_KEYS` / `PROVENANCE_TRUSTED_PQ_PUBLIC_KEYS` after a rotation), and that the memory's newest stored receipt is the recorded chain head |

Each row is registered as an independent AI Market v2 offer in the bundled Hub. Versioned direct HTTP endpoints are documented by each service at `/docs`; provider manifests are exposed at `/ai-market/v2/manifest`.

## Automatic publication and promotion

Each provider registers its capabilities with the bundled Hub at startup and
retries while the Hub is unavailable. The Hub then exposes the signed catalog,
includes owned providers in `ecosystem.nodes`, records invocation metrics and
announces its public identity to the configured federation roots.

This is automatic distribution, not automatic trust. Registration requires an
operator-issued scoped publisher token, and every external federation root keeps
a new peer pending until its operator verifies and pins the signing identity.
Social publishing, commercial directories and paid campaigns remain explicit
operator choices; a provider cannot approve itself or spend a marketing budget.

The public integration and manifest-builder workflow is documented at
`https://attestedmemory.net/developers` and in the localized
`attested/docs/i18n/*/DEVELOPER_GUIDE.md` files.
