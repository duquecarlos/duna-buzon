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

---

## BUZON-TOPES-META-1 -- caps on id_externo/remitente/asunto before posting to Finanzas (2026-11-07)

### Why

Finanzas capped three of buzon-v1's `meta` fields that previously had none
(`FIN-API-BUZON-LIMITES-CAMPOS-1`, `../finanzas/duna-finanzas-api` `3d48f65`, merging feature commit
`38690b9`): `id_externo` <= 998 chars, `remitente` <= 320 chars, `asunto` <= 998 chars (`cuerpo_texto`'s
existing 20,000-char cap is unchanged). Over a cap, the route now answers `422` naming the field, backed
by a database `CHECK` per field. Without this slice a real receipt with a long subject, sender address or
message id would be silently rejected by Finanzas and permanently skipped by this connector's existing
413/422 handling -- this slice makes the connector respect the caps on the way in, so a long *subject*
specifically is never lost.

### The wire, read at the ref relayed by Finanzas (2026-11-07)

Read directly, never taken on Finanzas' relay alone: `git -C ../finanzas/duna-finanzas-api show
3d48f65:docs/buzon.md` (the "`meta` field length limits" section) and the feature commit itself,
`38690b9494e7d061a5e5318183fb9712cd977aa7` (`src/duna_finanzas/web/buzon/schemas.py`'s
`EntradaBuzonMeta.id_externo`/`remitente`/`asunto` `Field(max_length=...)`, and
`src/duna_finanzas/storage/schema.py`'s `BUZON_ID_EXTERNO_MAXIMO_CARACTERES` /
`BUZON_REMITENTE_MAXIMO_CARACTERES` / `BUZON_ASUNTO_MAXIMO_CARACTERES` constants and their `CHECK`s).
The relayed figures (998/320/998) matched the code exactly -- no discrepancy found. `max_length` and the
`CHECK`'s `length(...) <=` are both inclusive, so a value AT the cap is accepted, matching this slice's
own inclusive boundary (`len(x) > cap` is the only thing that trips a skip).

### Decided by Main, carried out by this slice

- **`asunto` is truncated to 998 characters before posting, never skipped.** A receipt with a long
  subject is still a receipt. Truncation is Python string slicing (`texto[:998]`), which counts Unicode
  code points, never bytes, so it can never split a multi-byte character -- the identical mechanism
  `texto.py::html_a_texto` already uses to cap `cuerpo_texto` at 20,000.
- **`remitente` over 320 or `id_externo` over 998 are skipped BEFORE posting, never truncated.**
  Truncating an identifier would break idempotency (`id_externo`, Finanzas' own de-duplication key) or
  attribution (`remitente`). The skip is counted under the EXISTING `omitidos_por_413_422` counter and
  the cursor advances past it, exactly like a 413/422 rejection from Finanzas itself -- no new counter,
  since this is the same category of permanent, cursor-advancing skip.
- New module `src/duna_buzon/limites.py` holds the three caps and the two decisions (`
  excede_limites_no_truncables`, `truncar_asunto`), wired into `sincronizar.ejecutar_pasada` right after
  a message matches a rule and before `obtener_mensaje_completo` is called, so a message that will be
  skipped for an over-cap identifier never costs an extra Graph round-trip.

### Tests, measured

`tests/test_limites.py` (7 tests): both pure functions at exactly each cap and one over, for all three
fields, plus a subject built from multi-byte characters (`é`, `🙂`) one character over the cap, proving
the result is still valid UTF-8 (no character split) and exactly 998 characters.

`tests/test_sincronizar.py` (8 new tests): end-to-end through `ejecutar_pasada` --
`id_externo`/`remitente` each at-cap (forwarded normally) and one-over (skipped, `omitidos_por_413_422`
incremented, cursor still advances, a Finanzas transport that asserts it is never called); `asunto` at
cap (sent unchanged), one over (truncated in the ACTUAL posted multipart body, read back with a small
regex-based multipart parser added to the test file), and the same multi-byte case end-to-end.

`tests/test_registro_marcador.py::test_el_marcador_no_aparece_en_stdout_ni_en_los_logs` still passes
unmodified -- nothing in this slice logs or returns a field's content, only counts.

### Gate, measured

Run on the slice branch before the merge (tree sha `77e7cb15b72ea663e251eeff8716f232aae22062`):

    uv run pytest
    141 passed, 5 skipped in 1.61s

Tree identity proven before the merge commit was written: `git diff HEAD feat/buzon-topes-meta-1
--stat` against the merge commit was empty, and `HEAD^{tree}` after the merge equals
`77e7cb15b72ea663e251eeff8716f232aae22062`, the same tree the gate above ran on -- so the gate above is
this merge's gate; it was not re-run. The 5 skips are the same pre-existing Windows file-mode skips
`BUZON-CONECTOR-1` already recorded; none are new.

### Merge-policy-A classification, made by this slice

No schema or migration (no database in this repository). No cross-repo contract or DTO change: the wire
shape (field names, keys) is unchanged -- this slice only makes the EXISTING client honour a cap Finanzas
already set and merged; nothing Finanzas reads from this connector changes shape. No customer-facing
bytes: same reasoning `BUZON-CONECTOR-1` recorded -- this repository's only reader is Carlos acting as
his own system's administrator, and nothing user-visible (CLI text, `estado`'s summary) changed. All
three conditions clear, so this slice merged on green without stopping for approval.

### Attribution

Finanzas set the three caps and their values (`FIN-API-BUZON-LIMITES-CAMPOS-1`, read at
`../finanzas/duna-finanzas-api` `3d48f65`). Main decided which side of each cap truncates and which side
skips, and that the skip reuses the existing 413/422 counter rather than adding a new one. This slice
(`BUZON-TOPES-META-1`) read the wire at that ref, built `limites.py`, wired it into `sincronizar.py`, and
wrote the boundary tests.
