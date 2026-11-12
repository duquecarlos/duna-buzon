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

---

## BUZON-ENTRAR-ENV-ROOT-1 -- `entrar` could never read its own env files on the real host (2026-11-09)

### Why

Carlos measured it on `duna-prod-1`: `sudo -n /usr/local/sbin/duna-deploy-buzon entrar` failed at
`/bin/bash: line 3: /srv/duna/env/buzon.env: Permission denied` (and the same for
`buzon-conector.env` on the next line). Both files are `duna:duna` mode `0600`, but `cmd_entrar`
dropped to `duna` via `runuser` BEFORE sourcing either file by path, and `duna` cannot traverse
`/srv/duna/env/` itself on this host -- `status` and `deploy` never hit this, because both read the
same two files as ROOT through systemd's own `EnvironmentFile=` (`systemd-run`, which opens the file
before dropping privileges). `entrar` is the one subcommand that never uses `systemd-run` (a real tty
is already attached), so it had no equivalent and could never work on the real host.

### Fix, decided by Main

Root now opens both env files itself and hands them to the `duna`-owned child as already-open file
descriptors -- `3<"$ENV_CLIENTE" 4<"$ENV_TOKEN"` on the `runuser` line itself, so the open happens as
root, before the privilege drop. The child sources them from `/dev/fd/3` and `/dev/fd/4` (never by
path), closes both fds, and only then `exec`s python -- keeping the real tty and the no-argv,
never-printed properties `cmd_entrar` already had. `entrar` also now refuses up front, before
`runuser`, with a one-line message ("run `duna-deploy-buzon deploy` first") when
`$REPO/.venv/bin/python` does not exist -- Carlos hit exactly that ordering on the same host.

### Tests, measured

Two new tests in `tests/test_host_deploy_entry_script.py`: one asserts `cmd_entrar`'s block never
sources either env file by its `$ENV_CLIENTE`/`$ENV_TOKEN` path, only from `/dev/fd/3`/`/dev/fd/4`,
that the fd-opening redirections sit on the `runuser` line (root's own command), and that the fds are
closed before python replaces the shell -- a direct regression guard against sourcing paths as `duna`
again. The other asserts the venv-missing check runs before `runuser`. All 25 tests in that file,
including the pre-existing `bash -n` syntax check, pass against the new script.

`deploy/host/README.md` needed no change: nothing in it implied `duna` reads `/srv/duna/env/` by
path -- only that the files are owned by `duna` and mode `0600`, which is unaffected since root opens
them regardless of ownership (the same precondition `systemd-run`'s `EnvironmentFile=` already relies
on for `status`/`deploy`).

### Gate, measured

Run on the slice branch before the merge, then re-run on `main` after it (same tree, proven below):

    uv run pytest
    143 passed, 5 skipped in 4.18s

Tree identity proven before writing this entry: `git diff feat/buzon-entrar-env-root-1 HEAD --stat`
against the merge commit (`48521d439503d4075df7f1e535d4999e2b1a9727`) was empty, and
`HEAD^{tree}` equals `01feb2dda2d20789c1e8e8ff5ef4bd21ab173d4b`, the same tree the slice branch held
at its own last commit (`9cff8d2a6f51fef573636134d10d2f8f9fba556a`) -- so the gate above is this
merge's gate; it was not re-run a third time for this entry.

### Merge-policy-A classification, made by this slice

No schema or migration (no database in this repository). No cross-repo contract or DTO change
(nothing here touches the Finanzas wire). No customer-facing bytes: same reasoning
`BUZON-CONECTOR-1` recorded -- this repository's only reader is Carlos acting as his own system's
administrator running an infrastructure command on his own host, and the one new printed line (the
venv-missing refusal) is the same category as the existing EUID-refusal message, not a product
surface. All three conditions clear, so this slice merged on green without stopping for approval.

### Attribution

Carlos measured the failure on `duna-prod-1` and reported it verbatim. Main decided the fix (root
opens the files, hands the child fds; refuse early on a missing venv). This slice
(`BUZON-ENTRAR-ENV-ROOT-1`) carried it out: the script change, the comment above `cmd_entrar`, and the
two regression tests.

## BUZON-ALCANCES-MSAL-1 -- `entrar` raised ValueError on msal 1.39.0's reserved-scope check (2026-11-08)

### Why

Carlos measured it on the real host: `duna-deploy-buzon entrar` raised `ValueError: You cannot use any
scope value that is reserved. Your input: ['offline_access', 'Mail.Read'] The reserved list:
['offline_access', 'openid', 'profile']` from `initiate_device_flow`. `ALCANCES` listed
`offline_access` explicitly (`src/duna_buzon/auth.py:25`), but msal 1.39.0 refuses that -- it adds
`offline_access`/`openid`/`profile` to every request itself and raises if the caller also names one.

### Fix

`ALCANCES = ["Mail.Read"]`. The refresh token (silent renewal via `acquire_token_silent`) still comes
back, because MSAL adds `offline_access` on its own; a one-line comment at `ALCANCES` says so, so
nobody re-adds it believing it is required.

### Tests, measured

The old pin (`test_alcances_son_exactamente_mail_read_y_offline_access`) asserted the broken value and
would never have caught this. Replaced with
`test_alcances_son_exactamente_mail_read_sin_reservado`, which asserts `ALCANCES == ["Mail.Read"]` and
that it is disjoint from MSAL's reserved set (`offline_access`, `openid`, `profile`), so this cannot
regress silently. Added `test_alcances_no_disparan_el_scope_reservado_de_msal_real`, which runs the
REAL `msal.PublicClientApplication(...)._decorate_scope(ALCANCES)` -- no network, a pure check -- and
asserts it does not raise; this is exactly the internal call that raised on the real host, so the
existing `construir_app` fake seam could never have exposed this bug and a real-msal check was needed
alongside it.

### Gate, measured

Run on the slice branch before the merge (command: `uv run pytest`):

    144 passed, 5 skipped in 6.36s

Re-run on `main` after the merge, same command:

    144 passed, 5 skipped in 10.60s

Tree identity proven before writing this entry: merge commit `ea9136323178a9ad70e79fcf392553ca4383360b`
has tree `0b8bb0d42fe596cb7f36db28eef8850d2858f83c`, identical to the slice branch
`feat/buzon-alcances-msal-1`'s own tree at its last commit
(`d6d9ce5c3b200780e783c3546c14c92b2972bab4`) -- so the second run above is this merge's gate, not a
third independent run.

### Merge-policy-A classification, made by this slice

No schema or migration (no database in this repository). No cross-repo contract or DTO change --
`ALCANCES` governs what this process asks Microsoft Graph for, not the Finanzas wire, and the Graph
side effect (the token's granted scopes) is unaffected since MSAL added `offline_access` either way.
No customer-facing bytes: the diff is a list literal, a comment, and two unit tests; nothing printed or
logged changes. All three conditions clear, so this slice merged on green without stopping for
approval.

### Attribution

Carlos measured the failure on the real host and reported the exact exception text. This slice
(`BUZON-ALCANCES-MSAL-1`) diagnosed it against msal 1.39.0's own reserved-scope check, carried out the
fix, and added the regression tests, including one against the real library.

---

## BUZON-MOTIVO-DE-PARADA-1 -- a stopped pass names WHY (2026-11-09)

### Why

Carlos measured it on the real host: every pass since his sign-in (2026-11-09 01:00, 01:10, 01:16 UTC)
printed `vistos=2 reenviados=0 duplicados=0 omitidos_por_reglas=1 omitidos_por_413_422=0
adjuntos_descartados=0` and exited 1 with no reason. The second message matched a rule and the pass
stopped, either at Graph (`GraphAutenticacionError`/`GraphErrorTransitorio` while listing attachments or
fetching the full message) or at Finanzas (`enviar_entrada` -> `"unauthorized"` or `"retry"`, where
`"retry"` also swallowed every non-2xx/401/413/422 status such as 403/404/400). The operator could not
tell which, so nothing could be fixed.

### Fix, decided by Main

A stopped pass now prints one line on stderr -- `parada: <lado> <paso> http=<status>` or `parada: <lado>
<paso> error=<clase>` for a transport failure -- and the SAME string is appended to `estado.json`'s line
(`registro.linea_resumen`'s new optional `motivo` argument), so `estado` shows it too. Never content:
only the side (`graph`/`finanzas`), the step (`listar`/`adjuntos`/`mensaje`/`enviar`), the HTTP status,
or the exception's class name.

Finanzas' statuses that are NOT transient no longer pretend to be: any 4xx except
401/408/413/422/429 (400, 403, 404, 405, ...) now returns a new `ResultadoEnvio.estado ==
"rechazo_permanente"` (`finanzas.py`), which stops the pass (`ResultadoPasada.
detenida_por_rechazo_permanente`, cursor unmoved, exit 1) -- distinct from `"retry"` (408, 429, any 5xx,
or a transport error) and from the existing per-message `"rejected"` (413/422, which still advances the
cursor and continues). `graph.py`'s `GraphError` now carries the step (`listar`/`adjuntos`/`mensaje`)
plus the HTTP status or, for a transport failure, `type(exc).__name__` via a `motivo` property,
threaded through `GraphClient._get`/`_exigir_ok`'s new `paso` keyword.

### Tests, measured

`tests/test_finanzas.py` (+5): 408/429 stay `"retry"`; 400/403/404/405 are `"rechazo_permanente"` with
the status on `codigo_http`; a transport error carries `clase_excepcion == "ConnectError"`.

`tests/test_graph.py` (+5, 2 existing extended): each of the three steps (`listar`, `adjuntos`,
`mensaje`) produces the right `.motivo` string on a non-200/401 status and on a transport error.

`tests/test_sincronizar.py` (+6, 2 existing extended): a 4xx-non-transient Finanzas response stops the
pass as `detenida_por_rechazo_permanente` (not `detenida_por_error_transitorio`) with the exact motivo
string, cursor unmoved; a Finanzas transport error's motivo carries the exception class; a Graph error
while listing attachments carries `"graph adjuntos http=500"`.

`tests/test_cli.py` (+6): `comando_sincronizar` prints `parada: <motivo>` on stderr and `parada=<motivo>`
in both the stdout summary line and `estado.json`'s line (read back via `comando_estado`) for each stop
reason, parametrized; a normal pass prints nothing on stderr and no `parada=` in the summary; and one
full end-to-end test through real `GraphClient`/`FinanzasClient` mock transports with a marker Graph
token, a marker Finanzas token, a marker sender and a marker subject, asserting NONE of the four appear
in stdout or stderr when the pass stops on a 403 -- the direct test the spec asked for ("the line never
contains the bearer token or the message's sender/subject").

### Gate, measured

Run on the slice branch before the merge (command: `uv run pytest -q`):

    164 passed, 5 skipped in 4.91s

Re-run on `main` after the merge, same command:

    164 passed, 5 skipped in 6.66s

Tree identity proven before writing this entry: merge commit `ed25bbf801d801e574078b38805dbb55717724b4`
has tree `8a6409360c8d39ffd451256d0c24fa5150e9e37f`, identical to the slice branch
`feat/buzon-motivo-de-parada-1`'s own tree at its last commit
(`263669cb1a423614a649c53109eb5bfc8afb1aaf`), and `git diff HEAD feat/buzon-motivo-de-parada-1 --stat`
against the merge commit was empty -- so the second run above is this merge's gate, not a third
independent run. The 5 skips are the same pre-existing Windows file-mode skips `BUZON-CONECTOR-1`
already recorded; none are new.

### Merge-policy-A classification, made by this slice

No schema or migration (no database in this repository). No cross-repo contract or DTO change: the new
`"rechazo_permanente"` value on `finanzas.py`'s `Estado` is this client's OWN internal classification of
an existing Finanzas response, never serialized or read by any other repository; the wire's field names
and the request shape are unchanged. Judged NO customer-facing bytes, same reasoning `BUZON-CONECTOR-1`,
`BUZON-TOPES-META-1` and `BUZON-ENTRAR-ENV-ROOT-1` already recorded: this repository has no product, no
UI and no customer or operator other than Carlos acting as this system's own administrator running an
infrastructure command on his own host -- the new stderr line and the new `estado.json` field are the
same category as a tool's diagnostic log output, not a product surface a customer, operator or owner
reads inside a Duna product. All three conditions clear, so this slice merged on green without stopping
for approval.

### Attribution

Carlos measured the three silent stops on `duna-prod-1` and reported the exact summary line and exit
code. Main decided the fix: name the stop reason on stderr and in `estado.json`, and split Finanzas'
non-transient 4xx from `"retry"` into its own `rechazo_permanente` state. This slice
(`BUZON-MOTIVO-DE-PARADA-1`) carried it out: `graph.py`'s per-step `GraphError.motivo`, `finanzas.py`'s
`rechazo_permanente` split, `sincronizar.py`'s `ResultadoPasada.motivo`, `registro.py`'s and `cli.py`'s
wiring, and the tests above.

---

## BUZON-GRAPH-ADJUNTOS-1 -- `$expand` over the base attachment type 400'd on every matching message (2026-11-09)

### Bug, measured live on `duna-prod-1`

Carlos reported `parada: graph mensaje http=400` on the first message any rule matched -- the connector
had forwarded nothing. `obtener_mensaje_completo` (`src/duna_buzon/graph.py:177-204` at this slice's
start, verified against the repository before changing anything) asked
`GET /me/messages/{id}?$select=body&$expand=attachments($select=name,contentBytes,isInline)`.
`contentBytes` is a property of the DERIVED type `microsoft.graph.fileAttachment`, not of the base
`attachment` type the expand selects over, so Graph rejects the query with 400. Every matching message
hit this.

### Fix, decided by Main -- per-attachment fetch

Split into three kinds of call, each through the existing `_get`/`_exigir_ok` with its own `paso`:

- **Body**: `GET /me/messages/{id}?$select=body`, no `$expand` at all.
- **Attachment metadata**: `listar_adjuntos_metadata`'s `$select` widened from `name,size` to
  `name,size,isInline`; `@odata.type` is read off every row without being named in `$select` (Graph
  returns it as its own type annotation; `$select` only names declared properties, so it could never be
  named there) and carried on the now-wider `AdjuntoMetadata` (`id`, `nombre`, `tamano`, `es_en_linea`,
  `tipo_odata`).
- **Attachment content**: for each metadata row that is non-inline AND `#microsoft.graph.fileAttachment`,
  a separate `GET /me/messages/{id}/attachments/{aid}` with NO `$select`, reading `contentBytes` off the
  whole response -- `paso="adjunto"`, a new step name. An `itemAttachment` or `referenceAttachment` has
  no `contentBytes` at all, so it is skipped and counted in `MensajeCompleto.adjuntos_descartados` (new
  field), never fetched -- never raises.

`obtener_mensaje_completo` now takes the caller's already-fetched `metadatos_adjuntos` instead of
listing attachments a second time. `sincronizar.py` already called `listar_adjuntos_metadata` once (for
rule matching, when `mensaje.tiene_adjuntos`) -- it now keeps that list and threads it through to
`obtener_mensaje_completo`, and adds `completo.adjuntos_descartados` into `Contadores.
adjuntos_descartados` alongside `adjuntos.py`'s own `empacar` discards. The per-pass size/count limits
in `adjuntos.py` (`LIMITE_TAMANO_ARCHIVO_BYTES`, `LIMITE_CUERPO_BYTES`, `LIMITE_ADJUNTOS_POR_ENTRADA`)
are untouched -- they still apply after the bytes are fetched, exactly as before.

### Tests, measured

`tests/test_graph.py` (rewritten for the new two-argument `obtener_mensaje_completo` and the wider
`listar_adjuntos_metadata`, +13 cases net): exact request shape for the body fetch (`$select=body`, no
`$expand` key at all) and for each per-attachment fetch (no `$select` at all); inline attachments never
fetched and never counted as discarded; `itemAttachment` and `referenceAttachment` skipped, never
fetched, counted in `adjuntos_descartados` (parametrized over both types); a guard test
(`test_ningun_request_pone_contentbytes_en_select_ni_expand`) scanning every request a realistic flow
builds, asserting `contentBytes` never appears in any `$select` or `$expand` value and `$expand` is
never sent at all; a new `paso="adjunto"` error-naming test; and a fake that reproduces the real Graph
400 for the OLD query shape (`$expand` naming `contentBytes`) while answering 200 to every shape this
client now actually sends -- proving both that the fake is realistic and that the new client never
triggers it.

`tests/test_sincronizar.py` and `tests/test_registro_marcador.py`: their fake Graph transports updated
to the new per-message-body / per-attachment-content routing (previously a single combined
body+attachments response per message); behavior assertions unchanged.

### Gate, measured

Run on the slice branch before the merge (command: `.venv/Scripts/python.exe -m pytest -q`), on tree
`86c08fb00f57b8bce3d2c68fffa493d996827737` (slice commit `3f3bbe90b9b70a7a795e3bf53e40731599b6a372`):

    173 passed, 5 skipped in 17.31s

Tree identity proven before writing this entry, so that run was not repeated: merge commit
`43362dc6ceff57ab68bd9b61b0e588585b239f19` has tree `86c08fb00f57b8bce3d2c68fffa493d996827737`, IDENTICAL
to the slice branch's own tree at `3f3bbe90b9b70a7a795e3bf53e40731599b6a372`, and
`git diff HEAD feat/buzon-graph-adjuntos-1 --stat` against the merge commit was empty. The 5 skips are
the same pre-existing Windows file-mode skips `BUZON-CONECTOR-1` already recorded; none are new.

### Merge-policy-A classification, made by this slice

No schema or migration (no database in this repository). No cross-repo contract or DTO change: Graph's
own wire shape is Microsoft's, not this repository's, and the only other side of this connector --
Finanzas' buzon-v1 wire -- is unchanged by this diff (same `MetaEntrada`, same multipart shape). No
customer-facing bytes, same reasoning `BUZON-CONECTOR-1`, `BUZON-TOPES-META-1`,
`BUZON-ENTRAR-ENV-ROOT-1` and `BUZON-MOTIVO-DE-PARADA-1` already recorded: this repository has no
product, no UI and no customer or operator other than Carlos acting as this system's own administrator.
All three conditions clear, so this slice merged on green without stopping for approval.

### Attribution

Carlos measured the live 400 on `duna-prod-1` and reported the exact stop line, pinpointing it to the
first message any rule matched. Main decided the fix: per-attachment fetch instead of one
illegal-shaped expand, to also avoid one giant expanded response for a message with many or large
attachments. This slice (`BUZON-GRAPH-ADJUNTOS-1`) carried it out: `graph.py`'s split body/metadata/
per-attachment calls and the widened `AdjuntoMetadata`, `sincronizar.py`'s threading of the already-
fetched metadata list, and the tests above.

## BUZON-FUENTES-ESQUEMA-1 -- N independent mailbox sources, shaped for Gmail and legal@duna.solutions (2026-11-11)

### Surface, carried in from the observed slice

Carlos, 11 Nov 2026: the connector must eventually read his Hotmail, his Gmail, and
`legal@duna.solutions`, and later any user's own mailboxes. `BUZON-MULTI-FUENTE-OBS-1` (OBSERVED)
measured that this repository, until now, held exactly one `cache.bin`/`cursor.json`/`reglas.json`
under `$BUZON_DATOS` (`auth.py:29`, `cursor.py:18`, `cli.py:35-37` as they stood) and one Finanzas
token per person -- one mailbox, hard-coded. Main decided the shape this slice builds
(`ORCH-CARLOS-DELEGATES-TIER-1-1`); this slice is the build, not the decision.

### What changed

Every mailbox is now a SOURCE, independent of every other, under
`$BUZON_DATOS/fuentes/<id>/{fuente.json, credencial.bin, cursor.json, reglas.json, estado.json}`.
`fuente.json` carries `{id, proveedor, direccion, etiqueta, token_finanzas_ref, activa}` and no secret
of its own -- the credential moved apart into `credencial.bin`, encrypted exactly like the single
`cache.bin` was (`auth.py`'s `_guardar_cache_si_cambio`/`_cargar_cache_serializada` now take the
source's own directory, never the data root). New module `fuentes.py` owns this shape: load/save one
source, list every source, filter to the active ones, and the migration below.

`sincronizar` now loops over every ACTIVE source independently (`cli.py`'s new
`_ejecutar_una_fuente`): one source's auth failure, missing/invalid rules, provider error or Finanzas
stop is caught and reported under that source's own id, in both stdout (`fuente=<id> ...`) and stderr
(`fuente=<id> parada: ...`) -- it never stops the sources after it in the loop. The pass as a whole
exits non-zero the moment any source failed, so systemd still sees trouble. `estado` reports every
source's own cursor and last-pass line the same way; `fuentes` (new) lists every configured source
(id, provider, address, label, active/inactive) with no secret in it.

`entrar` takes an optional `--fuente <id>`: omitted, it resolves to the one configured source and
refuses with a clear message the moment there is more than one (or none) -- so the real host's
existing `duna-deploy-buzon entrar` (which calls `python -m duna_buzon entrar` with no arguments) keeps
working unchanged through and after the migration below, and `deploy/host/duna-deploy-buzon` itself
needed no change.

New module `proveedor.py` names the provider interface every source's mailbox client satisfies: list
messages since a cursor, list one message's attachment metadata, fetch one message's body and
attachments, close. `GraphClient` (`graph.py`) already has exactly this shape and needed no change at
all -- `construir_proveedor(fuente, token)` is the one new seam `cli.py` calls through, and the only
place that will grow an `if fuente.proveedor == "imap"` branch next.

### The wire field `fuente`, and what this slice could and could not verify about it

Every posted entry now carries its source's identity in the EXISTING `meta.fuente` field
(`finanzas.py`), as `"<proveedor>:<id>"` (e.g. `"microsoft:hotmail-carlos"`) instead of the one
constant value `"correo"` every entry carried before -- no new wire field, same six keys
`MetaEntrada.como_json()` has always sent. `MetaEntrada.fuente`'s own type widened from
`Literal["correo"]` to `str` accordingly.

**This slice could NOT re-read `EntradaBuzonMeta` at `../finanzas/duna-finanzas-api` to confirm
Finanzas accepts an arbitrary string here** (the build instructions asked for exactly that check): the
sandbox this slice ran in has no network and no filesystem access outside this repository's own
working directory, and the finanzas repository sits outside it. What this repository already has on
record (`BUZON-TOPES-META-1`, `DECISIONS.md` above) names caps on `id_externo`, `remitente` and
`asunto` only -- never on `fuente` -- and no entry anywhere in this repository's history claims `fuente`
is a Finanzas-side enum rather than a plain string field. On that basis, and because widening a
single-value field to a general string changes no KEY, no TYPE and no endpoint, this stays inside "no
new wire field" as the build instructions required. Source ids are short, hand-chosen names
(`hotmail-carlos`, `legal-duna`, ...), so `"<proveedor>:<id>"` will be well under any cap of the size
the three capped fields already carry. Flagged as an open unknown for Main or Carlos to confirm against
the live Finanzas schema before `legal@duna.solutions` or Gmail actually ship mail through this wire.

**CORRECTED by `BUZON-FUENTE-WIRE-CORREO-1` (2026-11-11): the open unknown above resolved the wrong
way.** Finanzas' `fuente` IS a Finanzas-side enum, not a plain string field --
`src/duna_finanzas/web/buzon/schemas.py:37`'s `Fuente = Literal["correo", "sms", "notificacion"]`, and
`storage/schema.py` carries a CHECK constraint `fuente_conocida` on `String(16)` besides. Every entry
this slice's `"<proveedor>:<id>"` built would have answered `422` on the next connector deploy against
a real Finanzas, including Carlos's own live Hotmail. `fuente` went back to the one constant `"correo"`
for every source; `FuenteConfig`'s computed identity (renamed `etiqueta_registro`) stays local to this
connector's own logs and status output, never the wire. See the `BUZON-FUENTE-WIRE-CORREO-1` entry
below for the fix and the contract test that now pins this down.

### Migration, automatic and idempotent

`fuentes.migrar_fuente_unica_si_necesario(directorio_datos, clave_cifrado)` runs at the top of every
command (`cli.py`'s `_migrar_si_necesario`). It is a no-op the instant `fuentes/` already exists --
migrated already, or a fresh multi-source install that never had the old layout, look identical from
there and both mean "nothing to do" -- and a no-op on a brand-new install with neither `fuentes/` nor
any legacy file. Otherwise it moves whichever of `cache.bin`/`cursor.json`/`reglas.json`/`estado.json`
exist at the data root into `fuentes/hotmail-carlos/` (`proveedor: "microsoft"`, `activa: true`),
preserving the SAME cursor value exactly -- no mail is re-sent, none is skipped -- and best-effort
recovers the account's own address for `fuente.json`'s `direccion` by parsing MSAL's own serialized
cache JSON (`Account` -> `username`) under the configured key; a cache that cannot be read this way
(wrong key, or none yet) still migrates, with `direccion` left empty for Carlos to fill in by hand --
a record field, never read by any provider logic.

### Tests, measured

New `tests/test_fuentes.py` (load/save round-trip, every `FuenteInvalidaError` path, listing and
filtering, and the migration: no-op cases, exact cursor preservation, idempotency across two calls,
address recovery from a real MSAL-shaped cache, a wrong key leaving `direccion` empty without losing
the credential, and a partial legacy state with only `reglas.json`). New `tests/test_proveedor.py`
(the Microsoft branch builds a `GraphClient`, `GraphClient` satisfies `Proveedor` structurally via
`runtime_checkable`, an unknown provider raises). `tests/test_auth.py`, `tests/test_cli.py`,
`tests/test_main.py`, `tests/test_sincronizar.py` and `tests/test_registro_marcador.py` rewritten for
the per-source directory argument, the `proveedor=`/`etiqueta_fuente=` parameters, the new CLI
dispatch, and the multi-source `cli.py` (including one test with two sources where the first's auth
failure is reported and the second still runs to completion).

### Gate, measured

`.venv/Scripts/python.exe -m pytest -q` on the slice branch before the merge, tree
`27b013c8585141947b975a4c186f3cf72a443b10` (slice commit `2258a4b0329a3ca0bb74425d847696e89f66cf8d`):

    206 passed, 6 skipped in 29.64s

Tree identity proven before writing this entry, so that run was not repeated for the merge: merge
commit `bfad4f36a55e24f467681a4ee0011e2a467c5a5f` has tree `27b013c8585141947b975a4c186f3cf72a443b10`,
IDENTICAL to the slice branch's own tree, and `git diff HEAD feat/buzon-fuentes-esquema-1 --stat`
against the merge commit was empty. The 6 skips are the same pre-existing Windows file-mode skips
`BUZON-CONECTOR-1` already recorded, plus one more of the same kind in the new `test_fuentes.py`; none
are a new KIND of skip.

### Merge-policy-A classification, made by this slice

No schema or migration in the database sense -- this repository has no database; the directory-layout
migration above moves files on disk, never a table or a model. No customer-facing bytes, same reasoning
`BUZON-CONECTOR-1` through `BUZON-GRAPH-ADJUNTOS-1` already recorded: this repository has no product,
no UI, and no customer or operator other than Carlos acting as this system's own administrator -- every
new line this slice prints (`fuente=<id> ...`) is that same operator-facing stdout/stderr, not a
customer-facing surface. No cross-repo contract or DTO change: the wire's key set, types and endpoint
are unchanged; only the VALUE one existing field carries changed, under the open unknown recorded
above. All three conditions clear, so this slice merged on green without stopping for approval.

### Attribution

Carlos named the surface (Hotmail, Gmail, `legal@duna.solutions`, and eventually other users' own
mailboxes) and ORCH-CARLOS-DELEGATES-TIER-1-1 authorized this Tier 1 build; `BUZON-MULTI-FUENTE-OBS-1`
measured today's single-mailbox shape and Main decided the directory layout this slice carried out:
`fuentes.py`'s per-source config and migration, `proveedor.py`'s provider interface, `auth.py`'s
per-source credential, `cli.py`'s per-source orchestration and new `fuentes` command, and the existing
`fuente` wire field now carrying each source's own identity.

### Open follow-up

**BUZON-FUENTES-IMAP-1** -- implement the `imap` branch of `proveedor.construir_proveedor` (Gmail and
`legal@duna.solutions` both need it) against the interface `proveedor.py` names in this slice. Not done
here: explicitly scoped to "the next slice" by this slice's own build instructions, and `fuentes.py`
already refuses a `fuente.json` naming any provider other than `microsoft`/`imap` so a hand-written
IMAP source stays inert (never silently mis-handled as Microsoft) until that slice lands.

---

## BUZON-FUENTE-IMAP-1 -- a generic IMAP provider, first used for legal@duna.solutions on Zoho (2026-11-11)

### Why

`BUZON-FUENTES-ESQUEMA-1` named the provider interface and left its own `imap` branch for "the next
slice" (`BUZON-FUENTES-IMAP-1` above). Carlos delegated this build (`ORCH-CARLOS-DELEGATES-TIER-1-1`);
`BUZON-MULTI-FUENTE-OBS-1` recommended exactly this provider. This slice builds it, against
`legal@duna.solutions`'s real mailbox shape first: Zoho Mail, `imap.zoho.com:993`, SSL, an app
password.

### The design question this slice had to answer, and the decision

The build instructions ask for "cursor by UIDVALIDITY+UID" -- the textbook-correct way to resync an
IMAP mailbox incrementally, and NOT what `sincronizar.py`/`cursor.py` carry today: every cursor in this
repository, before this slice, is a `datetime`, persisted in `cursor.json`, advanced by
`max(cursor_final, mensaje.recibido_en)`. IMAP's own `SEARCH SINCE <date>` has only DAY granularity, so
a cursor that were datetime-only would force every pass to re-list and re-fetch a whole day of mail,
repeatedly, forever.

Decision: keep the EXISTING datetime cursor and its whole call path completely unchanged for every
provider (`Proveedor.listar_mensajes(desde: datetime)` still takes exactly that, Microsoft's own
behaviour is untouched), and add a SECOND, OPTIONAL, provider-owned cursor that travels alongside it
through the exact same success/commit point -- never on its own:

- `graph.MensajeResumen` gains `cursor_proveedor: str | None = None` (Microsoft never sets it).
- `sincronizar.ejecutar_pasada` tracks a parallel `cursor_proveedor_final`, updated at every point
  `cursor_final` already is (`_avanzar`, one closure replacing five duplicated `max(...)` call sites),
  and returns it on `ResultadoPasada`.
- A new file per source, `fuentes/<id>/cursor_proveedor.json` (`cursor.cargar_cursor_proveedor`/
  `guardar_cursor_proveedor`), written by `cli.py`'s `_ejecutar_una_fuente` under the EXACT SAME
  `resultado.debe_persistir_cursor` guard the datetime cursor already is.

This is why invariant 5 (the cursor never advances past a message a pass could not finish) still holds
for IMAP: the provider's own UID resume token is never written by the provider itself mid-pass -- it is
carried on the message, like `recibido_en`, and only reaches disk when the REST of the pass already
proved itself safe to persist. `ClienteImap.listar_mensajes` uses it to resume with `UID SEARCH UID
<uid+1>:*` when the stored UIDVALIDITY still matches the mailbox's current one, and falls back to the
datetime-based `SEARCH SINCE <date>` (Python-filtered by the exact `desde` timestamp, the same precision
discipline Graph's own `$filter` already has) the first time a source ever runs, or the moment
UIDVALIDITY no longer matches -- the server's own signal that stored UIDs are no longer meaningful. A
`UID SEARCH UID n:*` is never issued once `n` is already at or past the mailbox's `UIDNEXT`: some IMAP
servers normalize an otherwise-empty range by returning the last message instead of none, so the search
is skipped outright in that case.

### `proveedor_errores.py`, and a two-line fix to `graph.py`

`sincronizar.ejecutar_pasada`'s own try/except caught `GraphAutenticacionError`/`GraphErrorTransitorio`
by name -- Microsoft-specific types baked into the one pass-execution loop every provider shares. New
module `proveedor_errores.py` names the two buckets generically
(`ProveedorAutenticacionError`/`ProveedorErrorTransitorio`); `graph.py`'s two exception classes now ALSO
inherit from them (`class GraphAutenticacionError(GraphError, ProveedorAutenticacionError)`), which
changes nothing about Graph's own behaviour (verified: `GraphError.__init__`'s `super().__init__(self.motivo)`
walks the MRO into the new base's un-overridden `Exception.__init__`, so `str(exc) == exc.motivo` exactly
as before) and `sincronizar.py` now reads `str(exc)` instead of `exc.motivo` at those two sites. `graph.py`
would otherwise have had to import from `proveedor.py`, which already imports `graph.py` -- the new
leaf module is what keeps that acyclic. `cliente_imap.py`'s own `ClienteImapAutenticacionError`/
`ClienteImapErrorTransitorio` sit under the same two buckets the same way.

### `cliente_imap.py`

`ClienteImap` satisfies `Proveedor` structurally, with no change to the shape. Connecting is LAZY --
`__init__` does no I/O at all (mirrors `GraphClient`, whose `httpx.Client` never connects until the
first request), so `construir_proveedor` can build it without risking an exception outside
`sincronizar.py`'s own try/except; one source's bad app password still never stops another source's
pass.

Read-only by construction: `self._conexion.select(buzon, readonly=True)` (which sends `EXAMINE`, never
`SELECT` -- `imaplib`'s own dispatch, confirmed against a real exchange before writing this module) is
the ONLY way this client opens the mailbox; every content fetch asks for `BODY.PEEK[<n>]`, never
`BODY[<n>]`; there is no method anywhere in this class that could send `STORE`, `COPY`, `MOVE` or
`EXPUNGE`. `tests/test_cliente_imap.py::test_examine_nunca_select_y_ningun_comando_de_escritura` proves
all of this from the outside, against a fake server that records every command line it receives.

Every message and attachment is addressed by IMAP UID, never a sequence number (which shifts as new
mail arrives mid-pass). Content is read by hand-writing a small parser for IMAP's own parenthesized-list
syntax (`_parsear_valor`/`_parsear_lista`/`_parsear_cadena`/`_parsear_atomo`) rather than pulling in a
third-party IMAP library -- `BODYSTRUCTURE` is walked into flat, numbered leaf parts
(`_particionar_bodystructure`); a leaf is the message's body candidate when it is `TEXT/PLAIN` or
`TEXT/HTML` without an `ATTACHMENT` disposition, and an attachment candidate otherwise (an inline image
counts as an attachment candidate too, listed but never downloaded -- the same skip Graph's own
`es_en_linea` already applies). Headers (`From`/`Subject`/`Message-Id`) are read as a `BODY.PEEK[HEADER.
FIELDS (...)]` literal and parsed with the stdlib `email` module, never ENVELOPE's own structure.
`AdjuntoMetadata.tipo_odata` is repurposed to carry the part's own MIME type (e.g. `"application/pdf"`)
since IMAP has no OData type annotation -- nothing outside this module's own `obtener_mensaje_completo`
reads that field for an IMAP-built `AdjuntoMetadata`, so nothing else needed to change.

`imaplib`'s exact wire behaviour (that `select(..., readonly=True)` sends `EXAMINE`; that `UIDVALIDITY`/
`UIDNEXT` surface through `response(name)`; that a FETCH literal comes back as a `(prefix, literal)`
tuple inside `dat`) was confirmed by driving the real stdlib `imaplib.IMAP4` against a throwaway socket
server before writing `cliente_imap.py`, rather than assumed from memory.

### `credencial_imap.py`

Mirrors `auth.py`'s shape for the one thing an IMAP source needs: `entrar` reads the app password with
`getpass.getpass` (no echo, never a command-line argument), verifies it with a REAL `LOGIN` against the
server (`_verificar_login_real`, the one seam, parallel to `auth.py`'s `construir_app`) BEFORE ever
writing anything, and only then encrypts and stores it in the source's own `credencial.bin` --
`auth.py` itself needed no change at all, since its existing `cache_cargable` (used by `estado`) already
only tests "does this file decrypt under the configured key", which says nothing about MSAL specifically
and already works unchanged for an IMAP source's stored password.

### `fuentes.py`, `cli.py`, `__main__.py`

`FuenteConfig` gains `host: str | None = None` / `port: int | None = None`, required (and validated) the
moment `proveedor == "imap"`, always `None` for Microsoft. New command `agregar-fuente imap --id <id>
--direccion <direccion> --host <host> --token-finanzas <ref> [--port <puerto>] [--etiqueta <etiqueta>]`
(`cli.comando_agregar_fuente`) writes a new source's `fuente.json` and never signs in -- `entrar --fuente
<id>` is the separate next step, exactly as the build instructions asked. `cli.comando_entrar` and
`_ejecutar_una_fuente` now branch on `fuente.proveedor` for which credential module to call
(`auth`/`credencial_imap`) and which errors to catch (`auth.SinCuentaError`/`credencial_imap.
SinCredencialError`/`cripto.ClaveInvalidaError`, unchanged otherwise); `comando_estado`,
`comando_fuentes` and `comando_sincronizar`'s own loop needed no change.

### Tests, measured

Two new files: `tests/test_cliente_imap.py` (15 tests) builds a real, in-process, single-connection TCP
server (`_ServidorImapFalso`, `socket`+`threading`) that records every command line, and drives
`ClienteImap` against it for: read-only-by-construction (above), listing with and without attachments,
the SINCE-date exact-timestamp filter, UID metadata and content fetch (including the inline-attachment
skip), the three cursor paths (no prior cursor, resume by UID, UIDVALIDITY mismatch falls back to
SINCE, and a cursor already at `UIDNEXT` issues no search at all), encoded-word subject decoding, a
failed LOGIN raising the generic authentication error, and a connection failure raising the generic
transient one. `tests/test_credencial_imap.py` (5 tests, 1 skipped on Windows for the file-mode
assertion, same as every other `no_es_windows`-gated test in this repository) covers `entrar`'s
read-verify-then-store order, a failed verification never writing anything, and `obtener_credencial`'s
round trip and its own `SinCredencialError`. Existing suites updated for the new `construir_proveedor`
kwarg and `FuenteConfig` fields: `test_proveedor.py`, `test_cli.py`, `test_main.py`.

`.venv/Scripts/python.exe -m pytest -q` on the slice branch before the merge, tree
`793545124df7ececc4becbfedb0483a8c713ff51` (slice commit `13ef57804aee98cb143c3b44ece7e4d14258ff14`):

    237 passed, 7 skipped

Tree identity proven before writing this entry, so that run was not repeated for the merge: merge
commit `0ad504eb02ef19571aacf4f4e1bcf6be98de8918` has tree `793545124df7ececc4becbfedb0483a8c713ff51`,
IDENTICAL to the slice branch's own tree, and `git diff HEAD feat/buzon-fuente-imap-1 --stat` against
the merge commit was empty. The 7 skips are the same pre-existing Windows file-mode skips earlier
slices already recorded, plus one more of the same kind in the new `test_credencial_imap.py`; none are
a new KIND of skip.

### Merge-policy-A classification, made by this slice

No schema or migration in the database sense -- this repository has no database; the new per-source
`cursor_proveedor.json` and the two new `fuente.json` fields are files on disk, exactly the same category
`BUZON-FUENTES-ESQUEMA-1` already classified this way. No customer-facing bytes: this repository has no
product, no UI, and no customer or operator other than Carlos acting as this system's own administrator
-- every new line this slice prints (`agregar-fuente`'s confirmation, `entrar`'s IMAP-login error) is
that same operator-facing stdout/stderr. No cross-repo contract or DTO change: the wire's key set, types
and endpoint are unchanged, and `fuente`'s value space already widened to an arbitrary string in the
prior slice -- this one only adds another concrete value (`"imap:<id>"`), not a new field. All three
conditions clear, so this slice merged on green without stopping for approval.

### Attribution

Carlos delegated this build (`ORCH-CARLOS-DELEGATES-TIER-1-1`); `BUZON-MULTI-FUENTE-OBS-1` recommended
this provider and this slice is its build, against the interface `BUZON-FUENTES-ESQUEMA-1` named:
`cliente_imap.py`'s IMAP client, `credencial_imap.py`'s app-password sign-in, `proveedor_errores.py`'s
provider-agnostic failure categories, the parallel provider-cursor threaded through `graph.py`/
`sincronizar.py`/`cursor.py`, and `fuentes.py`/`cli.py`/`__main__.py`'s new `agregar-fuente` command and
IMAP branches.

### Open follow-up

**BUZON-FUENTE-IMAP-CARPETAS-1** -- `ClienteImap` only ever examines `INBOX`. Graph's own
`listar_mensajes` deliberately reaches every folder (`graph.py`'s own docstring) since Carlos's inbox
rules move mail out of the Inbox before this connector ever sees it; nothing in this slice's build
instructions asked for the IMAP equivalent, and `legal@duna.solutions` has no such rules today, so it
was not built. Worth deciding before an IMAP source that DOES file mail into other folders (Gmail,
eventually) is added.

---

## BUZON-FUENTE-WIRE-CORREO-1 -- restore the wire `fuente` to the constant "correo" (2026-11-11)

### Why, and the miss

`BUZON-FUENTES-ESQUEMA-1` changed the wire `fuente` from the one constant `"correo"` every entry had
always carried to `"<proveedor>:<id>"`, flagging it in its own ledger entry as an open unknown it could
not verify ("this slice could NOT re-read `EntradaBuzonMeta`... the finanzas repository sits outside
[the sandbox]"). It guessed wrong: Finanzas' own `src/duna_finanzas/web/buzon/schemas.py:37` declares
`Fuente = Literal["correo", "sms", "notificacion"]`, and `storage/schema.py` carries a CHECK constraint
`fuente_conocida` on `String(16)` besides (`storage/schema.py:6560`, repeated at `6709`/`6725`). Every
entry this connector posted would have answered `422` on the next deploy, against a real Finanzas,
including Carlos's own live Hotmail -- nothing caught it only because no deploy had happened yet.
Finanzas (11 Nov) confirmed by delegation (`ORCH-CARLOS-DELEGATES-TIER-1-1`): keep `fuente="correo"`,
it is the CHANNEL; nothing source-specific belongs in it or in `id_externo`; a future optional `origen`
field (`FIN-API-BUZON-ORIGEN-OBS-1`, not built anywhere yet) is where a source's own identity will
eventually travel, if Finanzas ever needs it.

### The fix

`sincronizar.ejecutar_pasada` now builds every `MetaEntrada` with `fuente=limites.FUENTE_CORREO` (the
constant `"correo"`), for every provider (`microsoft`, `imap` alike) -- no per-source branch, since the
wire value no longer depends on which source built the entry. The `etiqueta_fuente` parameter this
threaded through (`ejecutar_pasada`, and `cli.py`'s call into it) is gone; nothing downstream of the
wire needs it anymore. `FuenteConfig.etiqueta_wire` (`fuentes.py`) is renamed `etiqueta_registro` and
redocumented: the SAME `"<proveedor>:<id>"` value, now explicitly logs-and-status-only, never sent to
Finanzas -- kept, not deleted, in case a future log line or `estado`/`fuentes` command output wants it.
Nothing currently prints it; this slice's build instructions ("no new field") were read as covering
operator-facing output too, so none was added.

`id_externo`'s own construction (`mensaje.internet_message_id`, `sincronizar.py`) is verified
byte-identical to `c4cb533`'s (the commit immediately before `BUZON-FUENTES-ESQUEMA-1`'s own code
commit `2258a4b`) -- confirmed by reading both files at that ref: `git show c4cb533:src/duna_buzon/sincronizar.py`
carried the identical `id_externo=mensaje.internet_message_id` line it carries today. Dedupe against
whatever Finanzas already stored for the Microsoft source still works.

### The contract test

`limites.py` gained the mirror it should have had before `BUZON-FUENTES-ESQUEMA-1` ever shipped:
`FuenteWire = Literal["correo", "sms", "notificacion"]` and `LIMITE_FUENTE: Final[int] = 16`, cited to
the same two Finanzas file:line pairs above, plus `FUENTE_CORREO: Final[FuenteWire] = "correo"`, the
one value this codebase ever constructs. `MetaEntrada.fuente`'s own type (`finanzas.py`) is restored
from the `str` `BUZON-FUENTES-ESQUEMA-1` widened it to, back to a `Literal` -- now `limites.FuenteWire`
rather than the old single-value `Literal["correo"]`, since the mirror itself has to admit all three
values Finanzas accepts even though this connector only ever sends one.

New `tests/test_limites.py` tests restate Finanzas' own `Literal` and cap verbatim
(`typing.get_args(FuenteWire) == ("correo", "sms", "notificacion")`, `LIMITE_FUENTE == 16`), assert
`MetaEntrada.fuente`'s type hint IS `FuenteWire` (not a bare `str`, so a wrong value is a type error at
every call site), and assert the one value the code ever builds (`FUENTE_CORREO`) passes both halves
of the contract. `tests/test_sincronizar.py` gained a direct assertion on the posted wire body
(`meta_enviada["fuente"] == "correo"`) in an existing end-to-end test that already captures the
multipart body. `tests/test_fuentes.py` renamed its `etiqueta_wire` test to match the rename.

### What this slice could NOT verify

Same sandbox limit the prior slice already recorded: no network and no filesystem access outside this
repository's own working directory, so the two Finanzas file:line citations above (and the `9b9464e`-era
citations elsewhere in this ledger) are taken from this slice's own build instructions verbatim, not
independently re-read against `../finanzas/duna-finanzas-api` in this run. The build instructions'
author is recorded as having validated them before dispatch; this slice did not re-derive them.

### Gate, measured

`.venv/Scripts/python.exe -m pytest -q` on the slice branch before the merge, tree
`24d8be6da63f77db7b20f460d2fa17ec52859153` (slice commit `b30e49a96e753ac63f042ce11f499ef4e94b2693`):

    241 passed, 7 skipped in 8.36s

Tree identity proven before writing this entry, so that run was not repeated for the merge: merge
commit (recorded below once written) carries the SAME tree, `24d8be6da63f77db7b20f460d2fa17ec52859153`,
and `git diff HEAD feat/buzon-fuente-wire-correo-1 --stat` against the merge commit was empty. The 7
skips are the exact same pre-existing Windows file-mode skips `BUZON-FUENTE-IMAP-1` already recorded;
none are a new kind.

### Merge-policy-A classification, made by this slice

No schema or migration -- this repository has no database, and no file layout changed; only a wire
VALUE and some identifiers moved. No customer-facing bytes: no `print`/stdout/stderr string anywhere in
this diff changed -- the fix is entirely in what gets POSTED to Finanzas and in internal naming, and
Carlos (this system's own operator) sees no different console output. No cross-repo contract or DTO
change: the wire's key set, types and endpoint are unchanged, and this slice RESTORES the value to what
Finanzas' own existing contract already accepted -- the same reasoning `BUZON-FUENTES-ESQUEMA-1` used to
clear this same condition when it (wrongly) changed the value the other way. All three conditions
clear, so this slice merged on green without stopping for approval.

### Attribution

Finanzas (11 Nov, relayed by Carlos via `ORCH-CARLOS-DELEGATES-TIER-1-1`) named the fix: keep
`fuente="correo"`, nothing source-specific in it or in `id_externo`, `origen` later if ever needed. This
slice carried it out and added the contract test that should have existed before the field this fixes
was ever touched.

### Open follow-up

**FIN-API-BUZON-ORIGEN-OBS-1** -- Finanzas' own follow-up, not this repository's: an optional `origen`
field on buzon-v1, for a source's own identity, if and when Finanzas decides it needs one. Not started
here; this connector will add it only once that field exists on the receiving end.

---

## BUZON-FUENTE-IMAP-CARPETAS-1 -- the IMAP source reads every folder, not only `INBOX` (2026-11-11)

### Why

`BUZON-FUENTE-IMAP-1`'s own open follow-up (above): `ClienteImap` only ever examined `INBOX`, while the
Microsoft source (`graph.py`'s `/me/messages`) already reaches every folder, since Carlos's own inbox
rules move mail out before this connector ever sees it. `legal@duna.solutions` has the same shape of
risk -- Zoho rules can file invoices and contracts into a folder -- so an IMAP source that only read
`INBOX` would miss exactly the mail this connector exists to forward. Decided by Main
(`ORCH-CARLOS-DELEGATES-TIER-1-1`): read every selectable folder except Trash/Junk/Sent/Drafts/All, by
SPECIAL-USE where the server advertises it, by name otherwise.

### The spec's premise did not hold, and what this slice did instead

The build instructions asked for the IMAP exclusion's name fallback to be "the same folders by the names
the Microsoft source already excludes (cite them)". There is no such list to cite: `graph.py`'s own
docstring says `/me/messages` "reaches every folder in the mailbox" -- no exclusion, by name or
otherwise -- and `BUZON-FUENTE-IMAP-1`'s own follow-up entry (above) says the same thing in these words:
"Graph's own `listar_mensajes` deliberately reaches every folder". Grepping this repository for every
spelling of Trash/Junk/Sent/Drafts in English and Spanish, across `src/`, `tests/` and `DECISIONS.md`,
turned up nothing but this slice's own new code. The measurement won over the prompt's assumption: this
slice built `_NOMBRES_EXCLUIDOS` (`cliente_imap.py`) as this provider's OWN name fallback, not a mirror of
anything Microsoft does, drawn from the default English folder names Zoho (`legal@duna.solutions`'s own
provider), Gmail and Outlook/Exchange actually use for Trash/Junk/Sent/Drafts/All-Mail. `graph.py` itself
is untouched by this slice -- there was nothing to extract from it.

### The fix

`cliente_imap.py`:

- `ConexionImapProtocolo` gained `list()`, matching `imaplib.IMAP4`'s own method.
- `ClienteImap._carpetas_a_examinar` calls `LIST` once per pass, parses each untagged response line
  (flags, delimiter, name -- reusing the same parenthesized-list/string/atom grammar `BODYSTRUCTURE`
  already needed, via `_parsear_linea_list`), and keeps a folder unless: it carries `\Noselect`; it
  carries one of the five excluded SPECIAL-USE attributes `\Trash`/`\Junk`/`\Sent`/`\Drafts`/`\All`
  (`_carpeta_excluida`); or -- only when it carries NO SPECIAL-USE attribute at all, not even a
  different one like `\Archive` or `\Flagged`, which would mean the server already answered the
  question -- its name is in `_NOMBRES_EXCLUIDOS`.
- `listar_mensajes` now loops over `_carpetas_a_examinar()`'s result (sorted, for a deterministic,
  repeatable order), `EXAMINE`-ing each one (`_examinar_carpeta`, quoting the mailbox name via the new
  `_entrecomillar_imap` -- needed now that a real folder name can carry spaces, e.g. "Facturas Legales")
  and running the SAME UID-resume-or-SINCE-fallback logic as before this slice (`_buscar_uids`,
  unchanged except for taking its UIDVALIDITY/UIDNEXT/prior-cursor as parameters instead of reading
  instance state set once at connect time), now evaluated once PER FOLDER.
- The resume token (`MensajeResumen.cursor_proveedor`) is now a JSON object mapping folder name to that
  folder's own `"<uidvalidity>:<uid>"` pair (`_formato_cursor_multi`/`_analizar_cursor_multi`), not a
  bare pair -- UID is only unique within one folder, so one pair could not represent more than one
  folder's position. `_analizar_cursor_multi` also reads the OLD bare-pair format a source already
  synced under `BUZON-FUENTE-IMAP-1` would have stored, as `{"INBOX": "<uidvalidity>:<uid>"}` -- the only
  folder that format could ever have come from -- so an already-deployed source upgrades in place and
  resumes exactly where `INBOX` left off instead of re-scanning it from the date cursor.
- `internet_message_id`'s own fallback (used only when a message carries no `Message-Id` header) is now
  folder-qualified (`f"{nombre_carpeta}:{uidvalidity}:{uid}"`), not the bare pair -- two different folders
  can each have their own UID 1 under the same UIDVALIDITY, and the bare pair (safe when `INBOX` was the
  only folder) would have let a message in one folder dedupe-collide at Finanzas with an unrelated
  message in another.
- `listar_adjuntos_metadata`/`obtener_mensaje_completo`, called by `sincronizar.py` in between two
  `next()` calls on the `listar_mensajes` generator while that folder is still the one selected on the
  connection, needed no change to their own bodies -- `_fetch_uno`'s new
  `_asegurar_carpeta_seleccionada` only adds a LAZY default-to-`INBOX` for the case for a caller that
  never ran `listar_mensajes` first at all (this module's own standalone metadata/body tests, which
  predate this slice and still call them that way).
- The single-folder constructor kwarg `buzon` is gone (nothing outside this module ever passed it);
  `BUZON_PREDETERMINADO = "INBOX"` stays, now naming only that lazy standalone-call default.

No change anywhere outside `cliente_imap.py` and its own tests: `proveedor.py`'s `construir_proveedor`,
`sincronizar.py`, `cursor.py`, `fuentes.py` and the CLI are untouched -- the per-folder cursor stays
inside the one opaque string `cursor_proveedor.json` already carried, exactly as `BUZON-FUENTE-IMAP-1`
designed it to.

### Tests

`tests/test_cliente_imap.py`'s fake server gained `_CarpetaFalsa` (one folder's own messages,
UIDVALIDITY/UIDNEXT and `LIST` flags) and a `carpetas=` constructor option, defaulting to today's single
`INBOX` shape when omitted, plus a `LIST` handler and folder-aware `EXAMINE`/`SELECT`/`UID` handling.
Seven new tests: a `\Trash`-flagged folder excluded and never `EXAMINE`d; a folder with no SPECIAL-USE
attribute at all excluded by name; a folder NAMED like an excluded one but flagged `\Archive` kept,
proving the name fallback never overrides a different attribute the server already set; a `\Noselect`
folder never `EXAMINE`d; the composite case the spec asked for by name -- three folders including Trash,
one folder's UIDVALIDITY changed from what the stored cursor remembers, proving that folder alone
restarts from the date cursor while the other's untouched folder entry carries forward unchanged in the
final `cursor_proveedor`; the old bare-pair cursor format read as `INBOX`'s own; and two folders sharing
a UID under the same UIDVALIDITY never colliding in `internet_message_id`. Two existing assertions on the
exact `cursor_proveedor` string (`test_listar_mensajes_simple_sin_adjuntos`,
`test_uidvalidity_distinta_cae_de_nuevo_a_since`) updated for the new JSON shape; every other existing
test in this file and in `test_proveedor.py` (including the one asserting `ClienteImap._cursor_inicial`
directly) passed unchanged -- that attribute still holds the raw string this slice's own
`_cursor_inicial_mapa` is parsed from, kept for exactly that test.

### Gate, measured

`.venv/Scripts/python.exe -m pytest -q` on the slice branch before the merge, tree
`30afd3e9a666db154bbf5d5ba2e132e56a8354d5` (slice commit `a87dcf848dbd693898a48f78a9c1fe698293ab12`):

    248 passed, 7 skipped in 15.73s

Tree identity proven before writing this entry, so that run was not repeated for the merge: merge commit
`df90e64495969b9b0c5cc7a48223a3920aacaf66` has tree `30afd3e9a666db154bbf5d5ba2e132e56a8354d5`, IDENTICAL
to the slice branch's own tree, and `git diff HEAD feat/buzon-fuente-imap-carpetas-1 --stat` against the
merge commit was empty.

248 = the 241 `BUZON-FUENTE-WIRE-CORREO-1` recorded (the most recent count on `main` before this slice)
plus this slice's 7 new tests, all in the new multi-folder section of `tests/test_cliente_imap.py`. The 7
skips are the same pre-existing Windows file-mode skips every earlier slice in this ledger already
recorded; none are new.

### Merge-policy-A classification, made by this slice

No schema or migration -- this repository has no database; the on-disk `cursor_proveedor.json` changes
its OWN internal encoding (bare pair to JSON object) but stays the same file, read back only through
`cursor.py`'s existing opaque-string accessors, exactly the category `BUZON-FUENTES-ESQUEMA-1` and
`BUZON-FUENTE-IMAP-1` already classified that file as. No customer-facing bytes: this repository has no
product, no UI and no customer or operator other than Carlos acting as this system's own administrator;
no `print`/stdout/stderr string this slice touched changed. No cross-repo contract or DTO change: the
buzon-v1 wire's key set, types and endpoint are unchanged -- this slice is entirely about WHICH messages
this connector ever offers to `reglas.coincide`, not about the shape of what it posts. All three
conditions clear, so this slice merged on green without stopping for approval.

### Attribution

Carlos delegated this build (`ORCH-CARLOS-DELEGATES-TIER-1-1`); Main decided the exclusion rule (SPECIAL-
USE first, name fallback second) this slice's own approval reason states verbatim. The open follow-up
this closes was written by `BUZON-FUENTE-IMAP-1` itself.

---

## BUZON-FUENTES-PULL-1 -- pull mailbox sources connected through Cuenta Duna (2026-11-11)

### Why

Carlos, 11 Nov (relayed verbatim by the Personal desk): "Also we have to work in the App to let the user
connect their own emails and in some way each day the Agent or the app should propose the money
movements". `ORCH-BUZON-OAUTH-CALLBACK-1` decided the OAuth itself ends at Cuenta Duna, never here --
this connector stays loopback-only and read-only, and only PULLS short-lived tokens. Cuenta Duna now
serves them (`CUENTA-API-FUENTES-CONECTADAS-1`); this slice is the pull side.

### The wire, restated verbatim in the build instructions, not re-read at a ref

The build instructions carried `GET /servicio/fuentes/{sub}`'s exact shape (HTTP Basic, client `buzon`;
`{"fuentes": [{"id", "proveedor": "google"|"microsoft", "direccion", "access_token",
"expira_en_segundos"}]}`, active sources only, 404 `cuenta_no_encontrada` for an unknown/erased `sub` or a
non-`buzon` caller) verbatim from `duna-cuenta-api`'s own `src/duna_cuenta/web/fuentes.py`,
`domain/fuentes_conectadas.py:fuente_a_wire`, `web/servicio.py`. This slice could not re-read that
repository at a ref (same sandbox limit `BUZON-FUENTES-ESQUEMA-1` and `BUZON-FUENTE-WIRE-CORREO-1` already
recorded: no filesystem access outside this repository's own working directory) -- taken on the build
instructions' own word, which records having validated them before dispatch.

### What changed

New `src/duna_buzon/cuenta.py`: `CuentaClient.listar_fuentes()` does the ONE pull `cli.py`'s
`comando_sincronizar` makes per pass (`_pull_fuentes_cuenta`), returning `FuenteCuenta` rows with a FRESH
access token each. The token lives in the dict `_pull_fuentes_cuenta` returns, for that one run, and is
NEVER written to disk anywhere in this slice's code -- no `credencial.bin` is ever created for a
`origen="cuenta"` source. A 404 or any transport failure raises `CuentaPullError` (status or exception
class only, mirroring `graph.GraphError`'s own shape); `cli.py` catches it, prints one count-only line
(`cuenta: cuenta fuentes http=404`, say) to stderr, and runs every LOCAL source exactly as if the pull had
never been attempted.

`fuentes.py`'s `FuenteConfig` gains `origen: Literal["local", "cuenta"]` (default `"local"`, so every
`fuente.json` written before this slice keeps meaning exactly what it always meant) and `ProveedorId`
widens to add `"google"`. Validated both ways: `origen="cuenta"` only ever pairs with `proveedor` in
`{"microsoft", "google"}` (what Cuenta Duna's wire can return); `origen="local"` only ever pairs with
`proveedor` in `{"microsoft", "imap"}` (what `entrar` can sign into) -- a hand-edited `fuente.json` naming
an impossible combination is refused at load, the same discipline `_PROVEEDORES_VALIDOS` already enforced
for an unknown provider string.

Each Cuenta-origin source is kept under `fuentes/cuenta-<id>/` (`fuentes.id_fuente_cuenta`), `<id>` being
Cuenta Duna's OWN id for it -- so a reconnect after a revoke resumes the identical cursor and rules rather
than starting over under a new local id, per this slice's own build instructions. `cli._pull_fuentes_cuenta`
rewrites that source's `fuente.json` on every successful pull (never its `credencial.bin` -- it has none)
and, on success, also marks any PREVIOUSLY-active Cuenta-origin source no longer returned as `activa=False`
without touching its cursor or its rules ("a revoked source simply stops appearing"). A source whose token
was not pulled THIS run (pull failed, not configured, or Cuenta Duna simply did not return it) is skipped
for the pass -- not a failure of the pass, and its cursor is left untouched for the next pull.
`cli.comando_entrar` refuses a `origen="cuenta"` source outright (connect/reconnect those at Cuenta Duna's
own screen, never here) rather than letting an operator accidentally start a device-code or IMAP sign-in
flow against a source this connector does not hold a local credential for.

New `src/duna_buzon/gmail.py`: `GmailClient`, read-only (no `modify`/`trash`/`delete`/`send` call exists
anywhere in the class), satisfying `proveedor.Proveedor` structurally with no change to that shape.
`users.messages.list` returns only ids -- Gmail's `format=metadata` cannot say whether a message has an
attachment at all (headers only, no `payload.parts`) -- so this client fetches each candidate message ONCE,
`format=full`, and caches the parsed MIME structure for the rest of the pass; `listar_adjuntos_metadata`/
`obtener_mensaje_completo` reuse that cache, and attachment CONTENT is still its own call per attachment
(`users.messages.attachments.get`), fetched only once a message has matched a rule -- mirroring Graph's own
split between metadata and content. `id_externo` is the RFC 822 `Message-ID` header, exactly as
`cliente_imap.py` already does, falling back to Gmail's own stable message id when a message carries none.

**Decision this slice made, not otherwise specified: the since-date query and the absence of a
provider-defined resume cursor.** Gmail's `after:` search operator is not documented to a precision finer
than a day, so this client queries one day EARLIER than `desde` and then filters exactly in Python
(`recibido_en < desde` skipped) -- the identical discipline `cliente_imap.py`'s own SINCE-date fallback
already uses for the same reason. Unlike IMAP's UID (an incrementing integer within one mailbox,
`BUZON-FUENTE-IMAP-1`'s own `cursor_proveedor`), a Gmail message id is not incrementing, so there is no
cheap "resume from the last one" primitive to build a second cursor from -- this provider, like the
Microsoft one, relies entirely on the existing datetime cursor plus Finanzas' own idempotency on
`id_externo` to absorb a re-scanned overlap window. `proveedor.construir_proveedor` gained one branch:
`proveedor == "google"` builds a `GmailClient`.

### Tests, measured

New `tests/test_cuenta.py` (6 tests): a successful pull with two sources, an empty list, HTTP Basic auth
headers present, 404, a transport error, and a 500 -- all against `httpx.MockTransport`, no network.

New `tests/test_gmail.py` (14 tests): GET-only guard; simple listing; `nextPageToken` paging (same `q` on
every page); the exact `after:<epoch - 1 day>` query; a message dated before `desde` filtered out in Python
despite the server returning it; 401/500/connection-error on listing; the Gmail-id fallback when
`Message-ID` is absent; attachment detection, download and inline-skip; HTML preferred over plain text when
both exist; exactly ONE `format=full` fetch per message even though listing, metadata and content all read
off it (the caching guard); and a 401 while fetching attachment CONTENT (the pulled token expiring
mid-pass) raising the same generic `GmailAutenticacionError` every other provider error already does, with
no special-case code needed in `sincronizar.py`.

`tests/test_config.py` (+5 functions, +10 collected -- two are `@pytest.mark.parametrize`d over 4 and 3
values respectively): Cuenta absent entirely is valid; all four present parses; each of the four set alone
is refused by name, naming only the missing variable, never the one that was set; an invalid/empty/wrong-
scheme `BUZON_CUENTA_URL` is refused (3 cases); `https://` is accepted.

`tests/test_fuentes.py` (+7): `origen` defaults to `"local"`; a `fuente.json` with no `origen` key at all
(written before this slice) still reads as `"local"`; a Cuenta-origin `fuente.json` round-trips;
`id_fuente_cuenta` prefixes correctly; an unknown `origen` string, a `"google"` source claiming
`origen="local"`, and an `"imap"` source claiming `origen="cuenta"` are each refused at load.

`tests/test_proveedor.py` (+2): `proveedor="google"` builds a `GmailClient`; it satisfies `Proveedor`
structurally via `runtime_checkable`.

`tests/test_cli.py` (+5, end-to-end through `comando_sincronizar`): Cuenta not configured never constructs
a `CuentaClient` at all; a successful pull runs the Cuenta source with the EXACT pulled token (never calling
`auth.obtener_token` for it), writes a `fuente.json` with no credential anywhere and no trace of the token
string in it; a 404 pull logs one line and the local source still runs to completion; a revoked source (the
second pull returns an empty list) is marked `activa=False` and is not executed, with its `reglas.json`
left in place; a reconnected source (a pre-existing `cursor.json` under `fuentes/cuenta-<id>/`, written
before `fuente.json` ever existed, simulating a prior connect-then-revoke) resumes that EXACT cursor rather
than `reglas.json`'s own `desde`.

### Gate, measured

`.venv/Scripts/python.exe -m pytest -q` on the slice branch before the merge, tree
`edbd2de7ec5bda7a29dbba876b42fb87b1777782` (slice commit `ad4118aa8e97ea87cbde85ce18004ead4c4ec6d9`):

    292 passed, 7 skipped in 24.26s

Tree identity proven before writing this entry, so that run was not repeated for the merge: merge commit
`5a68b66a52134544fa5ac7093ee18730937f0f9d` has tree `edbd2de7ec5bda7a29dbba876b42fb87b1777782`, IDENTICAL to
the slice branch's own tree, and `git diff HEAD feat/buzon-fuentes-pull-1 --stat` against the merge commit
was empty.

292 = the 248 `BUZON-FUENTE-IMAP-CARPETAS-1` recorded (the most recent count on `main` before this slice)
plus this slice's 44 new collected tests: 6 (`test_cuenta.py`) + 14 (`test_gmail.py`) + 10
(`test_config.py`, 5 functions, 2 parametrized) + 7 (`test_fuentes.py`) + 2 (`test_proveedor.py`) + 5
(`test_cli.py`) = 44. The 7 skips are the same pre-existing Windows file-mode skips every earlier slice in
this ledger already recorded; none are new.

### Merge-policy-A classification, made by this slice

No schema or migration -- this repository has no database; the new `origen` field and the `"google"`
provider are, like every earlier addition to `fuente.json`, a file on disk, not a table or a model. No
cross-repo contract or DTO change: this slice only CONSUMES Cuenta Duna's own, already-built
`GET /servicio/fuentes/{sub}` contract and Gmail's own public API -- it defines or changes no contract any
other repository reads, and the Finanzas buzon-v1 wire (the one contract this repository's own output
affects) is untouched: every message, from every provider, still posts with `fuente="correo"`. No
customer-facing bytes, same reasoning every earlier entry in this ledger has recorded: this repository has
no product, no UI, and no customer or operator other than Carlos acting as this system's own administrator
-- the new stderr line (`cuenta: cuenta fuentes http=...`) and the new `entrar` refusal message are that
same operator-facing diagnostic output, not a product surface a customer, operator or owner reads inside a
Duna product. All three conditions clear, so this slice merged on green without stopping for approval, per
its own approval reason ("Merge on a green gate").

### Attribution

Carlos named the surface (connect your own mailboxes, get money movements proposed automatically) and
`ORCH-CARLOS-DELEGATES-TIER-1-1` authorized this Tier 1 build, implementing `ORCH-BUZON-OAUTH-CALLBACK-1`'s
own decision that OAuth ends at Cuenta Duna and this connector only pulls tokens. This slice
(`BUZON-FUENTES-PULL-1`) built the pull side end to end: `cuenta.py`'s `CuentaClient`, `gmail.py`'s Gmail
provider, `fuentes.py`'s `origen` field and validation, and `cli.py`'s `_pull_fuentes_cuenta` and its
wiring into `sincronizar`/`entrar`.

## BUZON-IMAP-CARPETAS-INCLUIDAS-1 -- narrow an IMAP source to named folders (2026-11-11)

### Why

Carlos measured, 12 Nov, in his own browser: `legal@`, `hola@` and `soporte@duna.solutions` are not
separate logins on Zoho -- they are SHARED MAILBOXES inside his own `carlos@duna.solutions` login. So the
company source has to sign in as `carlos@` over IMAP, and today's IMAP reader (`cliente_imap.py`'s
`_carpetas_a_examinar`, built by `BUZON-FUENTE-IMAP-CARPETAS-1`) EXAMINEs every folder the server's `LIST`
returns, minus Trash/Junk/Sent/Drafts/All-Mail -- which would send Carlos's OWN personal inbox into the
COMPANY's Finanzas the moment that source signs in as `carlos@`. `ORCH-CARLOS-DELEGATES-TIER-1-1`
authorized this Tier 2 build (source scoping only, no wire change to Finanzas).

### What changed

`fuentes.py`'s `FuenteConfig` gains `carpetas: tuple[str, ...] | None`, meaningful only for
`proveedor == "imap"`. Absent (default, and every `fuente.json` from before this slice) means exactly
today's behaviour. Present means this source reads ONLY folders whose LIST name equals one of its entries
or sits under one of them in the server's own hierarchy (`entrada + delimitador + lo que sea`) -- still
minus the same excluded kinds, which apply FIRST and can never be pulled back in by naming them. A
non-list or an empty list is a `FuenteInvalidaError`, raised at load (`cargar_fuente`), never discovered
mid-sync.

`cliente_imap.py`: `_parsear_linea_list` now also returns each LIST line's own delimiter (`NIL` parsed to
`None`), previously discarded. New `_carpeta_en_lista` matches an entry's exact name or `entrada +
delimitador` as a prefix -- NEVER a bare string prefix, so an entry "Legal" can never match a sibling
folder "Legales" (no delimiter boundary between them). `ClienteImap` takes `carpetas` and applies it
through one shared `_incluida` predicate, used by both `_carpetas_a_examinar` (the sync path, unchanged in
every other respect) and the new `listar_carpetas_con_estado` (a `LIST`-only probe: every folder, flags,
and whether this source would read it -- no `EXAMINE`, no content, no credential). `listar_mensajes` now
prunes `mapa_cursor` to the currently-included folders BEFORE the pass starts, so a folder's stored
`(UIDVALIDITY, UID)` cursor is dropped the moment that folder stops being included (by `carpetas`
narrowing, or by the folder itself becoming excluded) -- it is never carried forward into
`cursor_proveedor.json` for a folder this source no longer reads.

`cli.py` gains `comando_carpetas(config, id_fuente)`: resolves the source, refuses a non-`imap` one or an
unknown id by name, decrypts its already-stored app password (refusing with "run entrar" if there is
none), and prints `carpeta=<name> banderas=<flags> leida=si|no` for every folder the server returns.
`comando_agregar_fuente` gains an optional `carpetas` parameter, written straight into the new
`FuenteConfig` field.

`__main__.py`: `agregar-fuente` gains a repeatable `--carpeta <nombre>` option (accumulated into a list,
passed through only when at least one was given -- omitted means `carpetas=None`, same as today). A new
command, `carpetas <id>`, takes exactly one positional argument and dispatches to `cli.comando_carpetas`.

### Tests, measured

`tests/test_fuentes.py` (+5): `carpetas` defaults to `None`; round-trips through `guardar_fuente`/
`cargar_fuente`; a `fuente.json` with no `carpetas` key at all (every source before this slice) still reads
as `None`; a non-list and an empty list are each rejected at load.

`tests/test_cliente_imap.py` (+10, plus the fake server gaining a configurable LIST delimiter): no
`carpetas` configured reads everything not excluded (unchanged behaviour, made explicit); an exact-name
match; a nested match under a `/`-delimited parent; a nested match under a `.`-delimited parent; a folder
("Legales") that merely starts with an included entry's letters ("Legal") is NEVER matched; an excluded
folder (`\Trash`) is never pulled back in even when it is named exactly like an included entry;
`listar_carpetas_con_estado` marks every folder's inclusion correctly, with and without `carpetas`
configured; `listar_carpetas_con_estado` issues `LIST` and nothing else -- no `EXAMINE`/`SELECT`/`STORE`/
`COPY`/`MOVE`/`EXPUNGE`/`FETCH`; and cursor pruning -- a folder dropped from `carpetas` has its stored
cursor absent from the next `cursor_proveedor` the moment a message is yielded from a folder still
included.

`tests/test_main.py` (+5): `--carpeta` repeated twice builds a list of two entries; omitted dispatches
`carpetas=None`; `carpetas` with no id, and with an extra argument, are each usage errors; `carpetas <id>`
dispatches to `cli.comando_carpetas` with that id.

`tests/test_cli.py` (+6): `comando_agregar_fuente` writes the given `carpetas` into `fuente.json`;
`comando_carpetas` end to end against a fake IMAP server (via a monkeypatched `cli.ClienteImap` wired to
the same fake-server seam `test_cliente_imap.py` already uses) -- once with no filter (everything not
excluded prints `leida=si`) and once with `carpetas=("Legal",)` (only `Legal` prints `leida=si`); an
unknown id, a non-`imap` source, and a source with no credential stored are each a one-line error, never a
crash.

### Gate, measured

`.venv/Scripts/python.exe -m pytest -q`, run twice: once on the slice branch (`feat/buzon-imap-carpetas-
incluidas-1`, commit `3f98e4c0531d2c0afb9d322153de4d6f82f7f438`) and once on the final tree after the
merge, both identical:

    318 passed, 7 skipped in ~20s

318 = the 292 `BUZON-FUENTES-PULL-1` recorded on `main` before this slice, plus this slice's 26 new
collected tests: 5 (`test_fuentes.py`) + 10 (`test_cliente_imap.py`) + 5 (`test_main.py`) + 6
(`test_cli.py`) = 26. The 7 skips are the same pre-existing Windows file-mode skips every earlier slice in
this ledger already recorded; none are new.

Tree identity, measured before writing this entry: merge commit `be26eba5ec16701ebe9f438edb6700ba19cdcafc`
has tree `439b81f4cf01a7af28a60729b577750720e06e47`, IDENTICAL to the slice branch's own tree (same sha),
and `git diff HEAD feat/buzon-imap-carpetas-incluidas-1 --stat` against the merge commit was empty -- so
the gate above was not re-run a third time for the merge.

### Merge-policy-A classification, made by this slice

No schema or migration -- this repository has no database; `carpetas` is, like every earlier addition to
`fuente.json`, a field on a file, not a table or a model. No cross-repo contract or DTO change: this slice
touches no wire this repository sends -- the Finanzas buzon-v1 contract is completely untouched (no entry's
shape, caps, or `fuente="correo"` value changed), and this slice defines no contract any other repository
reads. No customer-facing bytes, the same reasoning every earlier entry in this ledger has recorded: this
repository has no product, no UI, and no customer or operator other than Carlos acting as this system's own
administrator -- the new `carpetas` stderr/stdout lines (`comando_carpetas`'s own output, the new
`agregar-fuente` usage line) are that same operator-facing diagnostic output, not a product surface a
customer, operator or owner reads inside a Duna product. All three conditions clear, so this slice merged
on green without stopping for approval, per its own approval reason ("source scoping, no wire change to
Finanzas").

### Attribution

Carlos measured the Zoho shared-mailbox shape in his own browser and named the risk (a company source
signing in as `carlos@` would otherwise read his own personal inbox into the company's Finanzas);
`ORCH-CARLOS-DELEGATES-TIER-1-1` authorized this Tier 2 build. This slice (`BUZON-IMAP-CARPETAS-
INCLUIDAS-1`) built the narrowing end to end: `fuentes.py`'s `carpetas` field and validation,
`cliente_imap.py`'s delimiter-aware matching and cursor pruning, the new `carpetas <id>` operator command,
and `agregar-fuente`'s new repeatable `--carpeta` flag.
