# Changelog

## 1.1.0 — 2026-11-25

- `POST /api/memories/{id}/grants` and `DELETE /api/memories/{id}/grants/{grantee}`: share and revoke through the
  Memory Market, forwarding the caller's actor proof and the exact body (used by the `attested-memory` 0.3.0 client).

## 1.0.0 — 2026-09-06

- Personal memory shell for write, search and read flows.
- Scoped SaaS key introspection through the Gateway.
- Actor signature forwarding to Memory Market.
- Docker and PostgreSQL-backed Hub integration profile.
