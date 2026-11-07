# DECISIONS.md -- the ledger for duna-buzon

This file is the decision ledger for duna-buzon. It is **append-only**, and **newest entries go at the
END**. Never rewrite, reorder or delete an entry; if a decision is reversed, append the reversal.

This file is **HISTORY, not state.** It records what was decided, when, by whom and why. It does not
describe what is currently true. For what is currently true, read the code, `CLAUDE.md` and
`README.md` -- and if they disagree with an entry here, the code is right and the entry is simply old.

Each entry's heading opens with the slice ID that produced it.

---

## BUZON-CONECTOR-1 -- the mailbox connector, built end to end (2026-11-06)

### Why

Carlos wants his personal finances fed automatically from his mail (memory 2026-11-06,
`FIN-PERSONAL-INTAKE-1` in Finanzas). Main decided where this runs -- on `duna-prod-1`, posting over
loopback (ORCH-CARLOS-DELEGATES-TIER-1-1, ledger `ORCH-BUZON-REPO-1`) -- and the shape covered by this
entry. Finanzas owns the receiving wire (`FIN-API-BUZON-ENTRADAS-1`, merged at
`duna-finanzas-api` `9b9464e`); this repository only consumes it.

### Starting state

The repository was empty but for `README.md`, `.gitattributes` and `.gitignore` (created by Main,
`ORCH-BUZON-REPO-1`, 2026-11-06). This slice built the whole package, its tests, its deploy entry
script, and its own `CLAUDE.md` and this ledger.

### The wire, read at a ref before writing the client

Every field name and status code in `src/duna_buzon/finanzas.py` was read from
`../finanzas/duna-finanzas-api` at `main` commit `9b9464e19251fd94b841e652c246d795f42c9431` --
`docs/buzon.md`, `src/duna_finanzas/web/buzon/router.py`, `src/duna_finanzas/web/buzon/schemas.py`,
`src/duna_finanzas/storage/archivos.py` (sniffing and the 10 MB/10-file/12,582,912-byte limits), and
`src/duna_finanzas/web/limite_cuerpo.py` (confirming `LIMITE_CUERPO_BYTES` is
`10*1024*1024 + 2*1024*1024 = 12,582,912`, exactly as relayed). `tests/test_buzon_rutas.py` at that same
commit confirmed the exact multipart shape (`meta` as a JSON-string form field, `archivos` as repeatable
file parts, a dummy part forced to keep the request multipart with zero attachments) -- mirrored in
`src/duna_buzon/adjuntos.py::construir_partes_multipart`. Everything Finanzas relayed in the spec
(2026-11-06) matched the code with no discrepancy found.

### Shape decisions this slice made, not otherwise specified

- **`sincronizar`'s own stop/retry rule simplified to "never persist the cursor on a stopping failure"**
  (401 from Finanzas or Graph, 503/5xx/connection error from either), rather than persisting a
  partial advance up to the failing message and then stopping. Both are correct under Finanzas'
  idempotency on `id_externo`; not persisting at all is simpler to reason about and test, at the cost
  of re-reading a few minutes of already-processed messages on the very next retry, which the 10-minute
  overlap already pays for anyway.
- **An `estado.json` file, alongside `cursor.json`**, holding only the last pass's counts (never
  per-message content), so `estado` has something to report between a stopped pass and the next
  successful one. Not named explicitly in the spec; added because `estado`'s own requirement
  ("prints counts") needs somewhere to read them from once a pass has run.
- **`deploy/host/duna-deploy-buzon` has NO separate `scripts/deploy.sh`**: the whole deploy sequence
  (pull, `uv sync --frozen`, create the data directory, install the two systemd units, enable the
  timer, health-check) lives directly in the entry script's `cmd_deploy`, since this product has no
  second deploy script to delegate to (unlike `duna-cuenta-api`/`duna-finanzas-api`, which do). Kept
  thin by host-deploy-v1's own measure: it is a fixed, short sequence, not business logic.
- **No `backup` subcommand**, per the spec's own explicit instruction, which is a deliberate departure
  from host-deploy-v1-amendment-sensitive-data.md's rule 3 (every product with on-host personal data
  ships one): the only data here is an encrypted token cache and a cursor, both fully re-creatable
  (`entrar` rebuilds the cache; a stale cursor only costs a few minutes of re-read, absorbed by
  Finanzas' idempotency) -- recorded in `deploy/host/README.md`'s own "No backup subcommand" section.

### Gate, measured

Run from a clean tree, on `main`, after the merge (tree sha `86cff6a1f43d7dde5a2b66105ac9689b7e50290e`,
proven identical to the slice branch's own tree):

    uv run pytest --junit-xml=.gate-out/gate.xml -q
    127 passed, 5 skipped in 2.18s (132 collected: 0 failed, 0 errors)

The 5 skips are every file-mode assertion (`cripto`, `cursor`, `auth` cache) that is meaningless on
this Windows development machine -- each one names the reason and will run for real the first time
this gate runs on the Linux host. No test touches the network, Microsoft, or a real Finanzas instance:
Graph and Finanzas are faked with `httpx.MockTransport` throughout, and MSAL is faked behind
`auth.py`'s own `construir_app` seam.

### Merge-policy-A classification, made by this slice

No schema or migration (no database at all in this repository). No cross-repo contract or DTO change
(this slice only *consumes* Finanzas' existing, already-merged contract; nothing here is read by any
other repository). Judged NO customer-facing bytes either, despite this slice adding printed CLI text
(the device-code sign-in message, `estado`'s summary line, `deploy/host/README.md`'s operator steps):
this repository has no product, no UI and no customer or operator other than Carlos acting as this
system's own administrator running an infrastructure command on his own host -- the same category as a
tool's `--help` text or log output, not a product surface a customer, operator or owner reads inside a
Duna product. Recorded here explicitly, since the call is judgment rather than a clean yes/no, so the
next reader (or the orchestrator's own reclassification) can see the reasoning and override it if it
disagrees.

### Attribution

Carlos wanted the automatic mail feed (memory 2026-11-06). Main decided where this runs and this
repository's whole shape (`ORCH-CARLOS-DELEGATES-TIER-1-1`, ledger `ORCH-BUZON-REPO-1`). Finanzas built
and owns the receiving wire (`FIN-API-BUZON-ENTRADAS-1`, `FIN-API-BUZON-ENTRADAS-1-B`). This slice
(`BUZON-CONECTOR-1`) built the connector end to end against that wire: package, tests, `CLAUDE.md`, this
ledger, and the host-deploy-v1 entry script.
