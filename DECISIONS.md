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
