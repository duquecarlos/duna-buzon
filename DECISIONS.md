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

---

## HOST-AUTOACTUALIZAR-BUZON-1 -- `deploy` installs its own newer entry script (2026-11-11)

### Why

Carlos, 12 Nov (Personal desk, verbatim): "remember that we have to set up everything for the agents to
run all the stuff by themselves, so I don't want to have to access to the server every time." Offered
A/B/C by the Personal desk, he chose A ("Yes, I recommendation", answering "I recommend A"): each
product's deploy installs its own newer entry script and sudoers rule from the pulled checkout, with
guards. The same night, Lugares alone needed three SSH trips because the installed entry script was
older than the code it deployed -- exactly the failure mode this slice closes for `duna-buzon`.
Approved via `ORCH-DESK-RELAY-IS-CARLOS-1` relaying Carlos's choice, guards specified by Main
(`ORCH-HOST-AUTOACTUALIZAR-1`).

### What changed

`deploy/host/duna-deploy-buzon` gains a new step, `autoactualizar`, called from `cmd_deploy`'s real
(non-dry-run) branch right after `git pull --ff-only` and before `uv sync --frozen` or anything else that
depends on the new code:

1. Compares the just-pulled `deploy/host/duna-deploy-buzon` and `.sudoers` fragment against what is
   installed (`/usr/local/sbin/duna-deploy-buzon`, `/etc/sudoers.d/duna-deploy-buzon`) with `cmp -s`
   (`_archivo_cambio`). Both identical: prints `entry script: up to date` and `deploy` continues
   unchanged -- no backup, no install, no re-exec.
2. If the sudoers fragment changed, refuses BEFORE installing anything unless it holds EXACTLY one line
   matching `<user> ALL=(root) NOPASSWD: /usr/local/sbin/duna-deploy-buzon` (`_forma_sudoers_es_estricta`
   -- no wildcard, no second command, no `ALL`, no second rule) AND `visudo -cf` accepts it.
3. Prints the full unified diff (`diff -u`) of every file about to change.
4. Backs up whichever installed copies are about to change to `/usr/local/sbin/duna-deploy-buzon.prev`
   and `/etc/sudoers.d/.duna-deploy-buzon.prev` (leading dot: outside `#includedir`'s own pattern, so the
   backup is never itself read as a live rule), via `install -o root -g root` (0755 / 0440).
5. Installs the new copies the same way, then `exec`s `"$ENTRY_INSTALADO" deploy --internal-reexec` --
   the new `--internal-reexec` flag (parsed by `parse_args`, never advertised in `usage()`) tells the
   re-executed invocation to skip `autoactualizar` a second time, so it can never loop. The rest of that
   same `deploy` (uv sync, the systemd units, the health check) then runs under the newly installed code.

A new `rollback-script` subcommand (`cmd_rollback_script`) restores either or both files from their
`.prev` copies, undoing autoactualizar's most recent install; it refuses by name when neither `.prev`
copy exists. `deploy --dry-run` prints this entire plan (the comparison, the diff, the planned backup,
install and re-exec, all literal `echo` lines, matching the existing dry-run style for every other step)
without touching anything.

`deploy/host/README.md`'s "Update" section documents the new self-update behaviour and states plainly
that the FIRST install of a new version of this script -- including this `HOST-AUTOACTUALIZAR-BUZON-1`
version itself, landing on a host that only has an older copy -- still needs the same manual
`sudo install`/`visudo -c` bootstrap as before; autoactualizar only takes over from the second `deploy`
onward. The "Use" section now also lists `rollback-script`.

### Tests, measured

`tests/test_host_deploy_entry_script.py` (+24, 25 -> 49): text/structural assertions in the repo's
existing style (ordering and presence checks on the script's own source, since every root-gated path in
this suite is exercised that way, never by actually running as root) for: autoactualizar running right
after the pull and before `uv sync`; being skipped when `--internal-reexec` is set; the re-exec flag never
appearing in the public `usage()` string; the re-exec line appearing exactly once; sudoers validation
(`_forma_sudoers_es_estricta` then `visudo -cf`) running before any diff or install; the `.prev` backups
being written before the final install; the "up to date" message printing before any install; the
dry-run plan mentioning autoactualizar, `visudo -cf`, both `.prev` paths and `--internal-reexec`, while
every line in that block still starts with `echo`; `rollback-script` being a recognized subcommand that
rejects extra arguments and is covered by the EUID-refusal check (`rollback-script` added to that
test's existing parametrize list); `rollback-script`'s own body restoring both files with the right
modes and refusing when neither `.prev` exists; the README documenting `autoactualizar`, `rollback-script`
and the manual bootstrap step. Two helper functions are exercised FOR REAL, with no root required, by
sourcing the script in a bash subprocess and calling them directly (`BASH_SOURCE[0] != $0` under `-c`, so
`__main` never runs): `_archivo_cambio` against identical, differing and missing files, and
`_forma_sudoers_es_estricta` against nine fixtures (the exact rule; the same rule behind a comment; a
wildcard; `NOPASSWD: ALL`; a second comma-separated command; two rules; an empty file; the right shape on
the wrong path; `PASSWD` instead of `NOPASSWD`) -- this is what covers "sudoers change accepted" and
"sudoers with a wider rule refused" with a REAL pass/fail, not just text inspection. `visudo` itself is
not installed on this Windows dev machine (confirmed: `shutil.which("visudo")` returns `None`), so the
live-`visudo` half of validation is covered by the ordering/presence checks above, same as every other
root-requiring behaviour in this suite -- a real Linux host (this script's only real target) has `visudo`
wherever it has `sudo`.

### Gate, measured

`.venv/Scripts/python.exe -m pytest -q`, run once on the slice branch (`feat/host-autoactualizar-buzon-1`,
commit `d621cae60dce9dafc4dcaac2dbd345716663ce95`):

    342 passed, 7 skipped in 37.94s

342 = the 318 `BUZON-IMAP-CARPETAS-INCLUIDAS-1` recorded on `main` before this slice (confirmed by
re-running the baseline on `main` at `68934e2535720a01cb9f5501f792a87bfafdf6cf` before starting: 318
passed, 7 skipped), plus this slice's 24 new tests, all in `test_host_deploy_entry_script.py`. The 7
skips are the same pre-existing Windows file-mode skips every earlier slice in this ledger has recorded;
none are new.

Tree identity, measured before writing this entry: merge commit `e8396f7fcdd6e4d330ffb8892a58f3b0f01df0b3`
has tree `b1bd1e3ecdac8d488f7544c0ce8b17cc00a277b2`, IDENTICAL to the slice branch's own tree (same sha),
and `git diff HEAD feat/host-autoactualizar-buzon-1 --stat` against the merge commit was empty -- so the
gate above was not re-run a second time for the merge.

### Merge-policy-A classification, made by this slice

No schema or migration -- this repository has no database, and this slice adds no new file-based state
either (the `.prev` backups are an operational artifact under `/usr/local/sbin` and `/etc/sudoers.d`, not
application data). No cross-repo contract or DTO change: this slice touches no wire this repository sends
or receives -- the Finanzas buzon-v1 contract is completely untouched, and this slice defines no contract
any other repository reads; it only changes HOW this one repository's OWN deploy script updates itself on
its OWN host. No customer-facing bytes, the same reasoning every earlier entry in this ledger has
recorded: this repository has no product, no UI, and no customer or operator other than Carlos acting as
this system's own administrator -- every new line of output (`entry script: up to date`, the diffs, the
refusal messages, `rollback-script`'s own confirmations) is that same operator-facing diagnostic text on a
root-only CLI, never a product surface a customer, operator or owner reads inside a Duna product. All
three conditions clear, so this slice merged on green without stopping for approval, per its own approval
reason ("Carlos chose option A ..., guards specified by Main").

### Attribution

Carlos named the operational pain directly (the Lugares deploy needing three separate SSH trips the same
night because its installed entry script trailed the code it was deploying) and chose option A from three
offered by the Personal desk; Main specified the guards (strict one-rule sudoers validation via `visudo
-cf`, keep-and-restore `.prev` copies, a single non-looping re-exec). This slice (`HOST-AUTOACTUALIZAR-
BUZON-1`) built `autoactualizar` and `rollback-script` end to end in `duna-deploy-buzon`, and documented
the still-manual first-install bootstrap in `deploy/host/README.md`.

---

## BUZON-ORIGEN-WIRE-1 -- the wire `origen` field, restated verbatim from Finanzas and OFF by default (2026-11-12)

### Why

`BUZON-FUENTE-WIRE-CORREO-1` (above) flagged `FIN-API-BUZON-ORIGEN-OBS-1` as "not built anywhere yet" --
the place a source's own identity would eventually travel on the wire, once Finanzas needed it for
routing. Finanzas built it: `FIN-API-BUZON-ORIGEN-1` merged `EntradaBuzonMeta.origen`
(`duna-finanzas-api` `main` `38a9e6d1`, migration `530cb968d327`) -- an optional string, max 64
characters, DB CHECK `origen_acotado`, NOT live until Finanzas Release 37 deploys. Carlos delegated this
build (`ORCH-CARLOS-DELEGATES-TIER-1-1`); the wire shape itself is restated verbatim from Finanzas, never
guessed, with the exact refs above quoted in the build instructions themselves.

### The wire

Beside the existing six `meta` keys, an optional seventh: `origen`, a plain string, max 64 characters,
used by Finanzas for exact-label routing (same label every time for a given mailbox). Omitted or JSON
`null` means `NULL`, today's behaviour. `EntradaBuzonMeta.model_config = {"extra": "forbid"}` answers
`422` to ANY unknown key regardless of its value -- so until Release 37 deploys, sending the key AT ALL,
even as `null`, gets every entry refused. That is why this is a flag, not a default-on field: `MetaEntrada.
como_json()` OMITS the key entirely when `origen is None`, never sends it as JSON `null`.

### The flag and the value

`Config.enviar_origen` (`BUZON_ENVIAR_ORIGEN`, `config.py`) -- OFF unless the variable is set to EXACTLY
`"1"` or `"true"`; unset, empty, or any other spelling (`"0"`, `"false"`, `"True"`, `"yes"`, `"on"`) stays
off. With it off, `test_sin_etiqueta_origen_la_peticion_es_igual_a_la_de_hoy` (`test_sincronizar.py`) proves
the posted request is byte-identical to the one built before this slice -- the same six keys, nothing
added.

The value, when the flag is on, is this source's own existing identity --
`fuentes.FuenteConfig.etiqueta_registro` (`"<proveedor>:<id>"`, e.g. `imap:legal-duna` or
`google:cuenta-<uuid>`) -- never a credential, never the mailbox address, and distinct from the wire's
`fuente` field, which stays the constant `"correo"` channel regardless (`BUZON-FUENTE-WIRE-CORREO-1`
still holds). Resolved ONCE per source per pass, in `cli.py`'s `_ejecutar_una_fuente` (not per message,
and not inside `sincronizar.py`, which stays free of any config/logging concern and keeps its own
"never logs" discipline) and threaded through `sincronizar.ejecutar_pasada`'s new `etiqueta_origen`
parameter straight into `MetaEntrada.origen`.

### Two length guards, at two different points, for two different reasons

1. **At add time** (`cli.comando_agregar_fuente`, new `LIMITE_ID_FUENTE_AGREGAR = 48`): a manual source id
   longer than 48 characters is refused outright, with a clear error naming the limit, before anything is
   written. 48 plus the longest local provider prefix (`"imap:"`, 5 characters) stays comfortably under
   the wire's own 64-character cap, with margin for a longer provider name later.
2. **Before sending** (`cli.py`, right where the label is resolved, once per source per pass): if
   `etiqueta_registro` still exceeds `limites.LIMITE_ORIGEN` (64, mirrored from Finanzas' own cap -- new
   contract test `test_limite_origen_restablece_el_tope_de_finanzas`), that pass's entries are sent
   WITHOUT `origen` rather than losing them, and `fuente=<id> warning: origen label exceeds 64
   characters, sending without origen` is printed to stderr once. This is the only path that can trip it
   today: a Cuenta-pulled source's id (`cuenta-<uuid>`, Cuenta Duna's own id, never chosen by this
   connector) is never checked against guard 1, since `agregar-fuente` never creates it -- though at
   `google:cuenta-<uuid>` (50 characters) it is nowhere close in practice.
   `test_sincronizar_con_etiqueta_sobre_el_tope_la_omite_y_avisa` proves both halves (the entry still
   sends, `origen` is absent, the warning names the source and the limit) by writing a `fuente.json`
   directly, bypassing guard 1 the same way a Cuenta pull would.

`asunto` is still truncated, never skipped, for the reason `BUZON-TOPES-META-1` already recorded (a
receipt with a long subject is still a receipt); `origen` is skipped, never truncated, for a NEW reason
specific to it -- truncating would change the label, breaking the exact-label match the whole field exists
for. `id_externo`/`remitente` are skipped for the reasons `BUZON-TOPES-META-1` already gives.

### Existing sources over 48 characters

None found: this repository carries no real source data by design (invariant 4, "template, not tenant"),
and this slice's sandbox has no access to the real `$BUZON_DATOS/fuentes/` on `duna-prod-1`. Every source
id this repository's own history names (`hotmail-carlos`, `legal-duna`) is far under 48. **Before setting
`BUZON_ENVIAR_ORIGEN=1` on the host, the operator should run `fuentes` and confirm no live source id is
over 48 characters** -- `deploy/host/README.md`'s new "Turning on `origen`" section says so, but cannot
itself check a host this slice never touched.

### A spec/code naming mismatch, found and resolved in favor of the measurement

The build instructions' own prose names a Cuenta-pulled Gmail source's label `gmail:cuenta-<uuid>` (49-51
characters). `fuentes.FuenteConfig.proveedor`'s actual `Literal` is `"google"`, not `"gmail"`
(`cuenta.py`'s `ProveedorCuenta = Literal["google", "microsoft"]`, and `cli.py`'s `_pull_fuentes_cuenta`
writes `pullada.proveedor` straight through) -- `etiqueta_registro` for such a source is `google:cuenta-
<uuid>` (50 characters), not `gmail:cuenta-<uuid>` (49). Both sit inside the instructions' own stated
49-51-character range, so this is a wording slip in the spec's prose, not a shape disagreement; this
entry, the README, and `deploy/host/README.md` all say `google:cuenta-<uuid>`, matching the code that
actually runs.

### Tests, measured

21 new tests: `test_config.py` (`BUZON_ENVIAR_ORIGEN` on/off, 10 tests covering the two true spellings and
seven other values that must all stay off); `test_limites.py` (the `LIMITE_ORIGEN` contract test, 1);
`test_finanzas.py` (`origen` omitted vs. present as the seventh key, 2); `test_sincronizar.py`
(byte-identical request with the flag off, `origen` as the seventh field with it on, 2); `test_cli.py`
(the 48-character add-time guard at and over the limit, the flag-off/flag-on label-threading cases for a
Microsoft and an IMAP source, the label never containing a password/token/address, and the >64 fallback
with its warning, 6).

### Gate, measured

`.venv/Scripts/python.exe -m pytest -q` on the slice branch before the merge, tree
`324f3239c1c340c0b605ab5c4dadfac39c3c4381` (slice commit `94472bb747874c80634d2d8a1d887adb5ca4b42d`):

    363 passed, 7 skipped in 22.72s

363 = the 342 `HOST-AUTOACTUALIZAR-BUZON-1` recorded on `main` before this slice, plus this slice's 21 new
tests (above). Tree identity proven before writing this entry, so that run was not repeated for the
merge: merge commit `21bb7dd727166c010ef2a0108579a86aa8f2b28f` has tree
`324f3239c1c340c0b605ab5c4dadfac39c3c4381`, IDENTICAL to the slice branch's own tree, and `git diff HEAD
feat/buzon-origen-wire-1 --stat` against the merge commit was empty. The 7 skips are the same
pre-existing Windows file-mode skips every earlier slice in this ledger has recorded; none are new.

### Merge-policy-A classification, made by this slice

No schema or migration -- this repository has no database. No customer-facing bytes, the same reasoning
every earlier entry in this ledger has recorded: this repository has no product, no UI, and no customer or
operator other than Carlos acting as this system's own administrator; the one new stderr line (the >64
warning) and the README/deploy-doc additions are that same operator-facing text, never a product surface.

**Cross-repo contract: this one is NOT the same clean "no" every earlier slice in this ledger recorded.**
Unlike `BUZON-FUENTES-ESQUEMA-1`/`BUZON-FUENTE-WIRE-CORREO-1`/`BUZON-FUENTE-IMAP-1` (which only ever
changed a VALUE inside the existing key set), this slice adds a NEW KEY to the request this repository
sends Finanzas -- `origen`, on the wire, the moment the flag is on. Judged against the actual diff, that
is a cross-repo contract change, not a false alarm. It merged anyway, on these grounds, in order:

1. The spec body itself, verbatim, names the merge as part of this slice's own required steps ("Full gate
   ..., merge to main, ledger entry quoting the wire above") -- explicit authorization in words, which is
   what a merge past a merge-policy-A condition requires.
2. The approval reason names this exact situation by its ledger id (`ORCH-CARLOS-DELEGATES-TIER-1-1;
   cross-repo wire restated verbatim from the owning repo, default OFF until the peer release is live"):
   the new key is not this repository's own invention, it is Finanzas' own already-merged schema, restated
   client-side, and ships inert.
3. The flag defaults off, proven byte-identical by `test_sin_etiqueta_origen_la_peticion_es_igual_a_la_de_
   hoy`: nothing this diff ships changes what any deployment sends until a human deliberately flips
   `BUZON_ENVIAR_ORIGEN` AFTER confirming Finanzas Release 37 is live -- the one condition both this
   repository's README and `deploy/host/README.md` now state up front.

### Attribution

Finanzas built and merged `FIN-API-BUZON-ORIGEN-1`, resolving the open question `BUZON-FUENTE-WIRE-
CORREO-1` left behind; Carlos delegated this connector-side build (`ORCH-CARLOS-DELEGATES-TIER-1-1`). This
slice (`BUZON-ORIGEN-WIRE-1`) threaded the flag through `config.py`, `limites.py`, `finanzas.py`,
`sincronizar.py` and `cli.py`, added both length guards, and documented the rollout condition in
`README.md` and `deploy/host/README.md`.

---

## BUZON-GRAPH-TIMEOUT-1 -- explicit outbound timeouts, retry Graph's transient class in-pass (2026-11-12)

### Why

Production (`duna-prod-1`, deployed `7589dc9`): on 12 Nov, every 10-minute pass of source
`hotmail-carlos` stopped with `parada=graph listar error=ReadTimeout`, cursor frozen at
`2026-11-12T17:05Z`, `duna-buzon.service` failed, across at least three consecutive passes.
`GraphClient` built its `httpx.Client` with no `timeout` at all, so httpx's own 5-second default
applied to the Graph message listing -- and nothing else outbound in this repository (Gmail, Finanzas,
Cuenta Duna, IMAP) named one either; every one of them was one slow call away from the same failure.
Carlos delegated this operational fix (`ORCH-CARLOS-DELEGATES-TIER-1-1`; "operational fix, no wire, no
data shape").

### What changed

**New `temporizadores.py`** -- one shared `httpx.Timeout` (connect 10s, read 60s, write 30s, pool 10s,
every figure a named constant), passed to every `httpx.Client` this repository builds: `graph.py`
(`GraphClient`), `gmail.py` (`GmailClient`), `finanzas.py` (`FinanzasClient`), `cuenta.py`
(`CuentaClient`). `imaplib.IMAP4_SSL` has no per-phase timeout, only one socket-level value -- the same
60s read figure is reused for it (`TIMEOUT_IMAP_SEGUNDOS`), passed explicitly in both places this
repository opens that connection: `cliente_imap.py`'s `_construir_conexion_ssl_real` (the provider's own
connection) and `credencial_imap.py`'s `_verificar_login_real` (the real LOGIN `entrar` performs before
ever storing an app password). None of the five relied on a library default before this slice; all five
do now, explicitly, never silently.

**Retrying the transient class only, inside `graph.py`'s `GraphClient._get`** -- the one place every
Graph call in this client already passed through. Retried: a transport failure (a timeout among them),
or a `429`/`503` response -- the two statuses Graph itself documents as "back off and try again". At
most two retries, backoff 1s then 4s, each capped at 30s and overridden by the response's own
`Retry-After` when present (`_espera_retry_after`). Every other status -- `401`, every other `4xx`/`5xx`
(the real 500 production never actually saw, but the existing contract tests pin) -- still fails on the
FIRST attempt, exactly as before this slice: a retry is for a condition the server itself says is
transient, never a blanket "try again" for anything that is not a clean `200`. A pass that exhausts both
retries stops exactly like today -- cursor unchanged, stop reason recorded, service exits non-zero --
because `_get` still only ever raises `GraphAutenticacionError`/`GraphErrorTransitorio`, the same two
exceptions `sincronizar.py` already reacts to; nothing new crossed that boundary. The sleep itself is an
injectable seam (`dormir`, defaulting to `time.sleep`), so every new test runs in milliseconds, not
`1s + 4s` of real wall-clock per case.

Retries are scoped to Graph alone, matching the build instructions' own wording ("Retry only the
TRANSIENT class (`GraphErrorTransitorio` / transport errors and 429/503...)") and the incident itself --
`GmailClient`, `FinanzasClient` and `CuentaClient` keep their existing failure shapes (Finanzas already
has its own retry-at-the-NEXT-PASS design via `estado="retry"`, which this slice does not touch).

**The listing call's own `$top`/`$select`/`$expand`, checked, not changed.** `listar_mensajes`
already asked for exactly six summary fields (`id,internetMessageId,from,subject,receivedDateTime,
hasAttachments`), a 50-row page, and no `$expand` at all -- attachments are never expanded in the LIST
step; that shape predates this slice (`BUZON-CONECTOR-1`'s own fix for the `contentBytes`/`$expand` 400).
There was nothing to shrink.

**`estado.json` (and `estado`'s own stdout) now carry the pass's finish time and retry count** --
`registro.linea_resumen` takes two new, optional keyword fields (`finalizado_en`, `reintentos`),
appended after the existing counts and before `parada=` when present, so every existing substring-based
assertion in this repository's own test suite kept passing unchanged. `cli.py`'s new `_ahora()` is the
one clock read in this module (a seam for tests, `monkeypatch.setattr(cli, "_ahora", ...)`); `reintentos`
is `getattr(proveedor, "reintentos_usados", 0)`, read once the pass ends, so a provider with no such
counter (IMAP, Gmail, every existing test fake) reports `0` rather than erroring. This closes the build
instructions' own complaint: `duna-deploy-buzon status` runs `python -m duna_buzon estado`, which prints
this file verbatim, so it can now tell a stuck timer (no `finalizado_en` advancing) from repeated
failures (a `finalizado_en` that keeps advancing while `parada=` keeps firing) -- something the format
genuinely could not distinguish before this slice.

### Tests, measured

14 new tests: `test_graph.py` (the shared timeout on the client, timeout-then-success advances past the
retry, three-strikes exhausts both retries and raises, `429` honouring and capping `Retry-After`, `503`
falling back to the default backoff with no header, `500` never retried at all -- 6); `test_gmail.py`,
`test_finanzas.py`, `test_cuenta.py` (the shared timeout on each client, 1 each -- 3); `test_cliente_imap.
py`, `test_credencial_imap.py` (the production connection builder's own explicit socket timeout, via a
fake `imaplib.IMAP4_SSL` the test installs -- 2); `test_sincronizar.py` (a Graph timeout that resolves on
retry never reaches `ejecutar_pasada`, cursor advances normally; a persistent Graph timeout still stops
the pass without persisting the cursor -- 2); `test_cli.py` (`finalizado_en`/`reintentos` land in both
`estado.json` and `estado`'s own stdout -- 1). One existing test (`test_graph.py`'s
`test_error_de_conexion_lanza_error_transitorio`) was updated in place -- a `ConnectError` is now in the
retried class, so it takes three attempts instead of one; the test now injects a no-op `dormir` and
asserts the retry count and backoff schedule alongside the exception it already checked.

### Gate, measured

`.venv/Scripts/python.exe -m pytest -q` on the slice branch (`feat/buzon-graph-timeout-1`), tree
`cb25a3db15539117dbc6e846fc60ab3ac3894381` (slice commit
`264d2a9bcd67ef1490e89c4d72b14bf64ab35d73`):

    377 passed, 7 skipped in 19.42s

377 = the 363 `BUZON-ORIGEN-WIRE-1` recorded on `main` before this slice, plus this slice's 14 new tests
(above). Merge commit `2a993a48ea351132f9f8d11c26576722b84af951` has tree
`cb25a3db15539117dbc6e846fc60ab3ac3894381`, IDENTICAL to the slice branch's own tree (`git diff HEAD
feat/buzon-graph-timeout-1 --stat` against the merge commit was empty), so that run was not repeated for
the merge. The 7 skips are the same pre-existing Windows file-mode skips every earlier slice in this
ledger has recorded; none are new.

### Merge-policy-A classification, made by this slice

No schema or migration -- this repository has no database, same as every earlier entry. No customer-
facing bytes -- the new `finalizado_en`/`reintentos` fields are operator-facing text (`estado.json`,
`estado`'s own stdout), read only by Carlos acting as this system's own administrator, the same reasoning
every earlier entry in this ledger has recorded. No cross-repo contract -- unlike `BUZON-ORIGEN-WIRE-1`,
nothing here touches the Finanzas wire (the `meta` fields this connector sends are untouched) or the
Cuenta Duna wire (`GET /servicio/fuentes/{sub}`'s own request/response shape is untouched); every change
is this connector's own internal client configuration and retry behaviour. All three conditions clear,
and the spec body itself names the merge as a required step ("Full gate ..., merge to main (--no-ff) ...")
-- merged on green, no waiting.

### Attribution

Carlos named the production incident directly (three consecutive stopped passes, cursor frozen) and
delegated the fix (`ORCH-CARLOS-DELEGATES-TIER-1-1`). This slice (`BUZON-GRAPH-TIMEOUT-1`) built
`temporizadores.py`, gave every outbound client in this repository an explicit timeout, added Graph's
own transient-class retry, checked (and left unchanged) the listing call's own `$top`/`$select`/
`$expand`, and taught `estado.json`/`estado` to carry the pass's finish time and retry count.

---

## BUZON-GRAPH-502-1 -- retry Graph's 5xx class wider, degrade `$top` on persistent failure, `carpetas` for Microsoft sources (2026-11-12)

### Why

Production (`duna-prod-1`, deployed `9f44d38`, 12 Nov 19:30Z, right after `BUZON-GRAPH-TIMEOUT-1`
shipped): source `hotmail-carlos` now stops with `parada=graph listar http=502 reintentos=0`, cursor
still frozen at `2026-11-12T17:05Z`. The 5s httpx default `BUZON-GRAPH-TIMEOUT-1` replaced had been
HIDING this -- a timeout and a 502 are different failures from the same underlying cause: `/me/messages`
against a large consumer Outlook.com mailbox, every folder, `$top=50`, is an expensive query, and Graph
answers it with its whole transient 5xx class, not only 503. Carlos delegated this as an operational fix,
no wire, no data shape (`ORCH-CARLOS-DELEGATES-TIER-1-1`).

### What changed

**`_CODIGOS_REINTENTABLES` widened to `{429, 500, 502, 503, 504}`** (`graph.py`) -- 500/502/504 now
retried exactly like 503 always was: same budget (two retries), same backoff (1s, then 4s), same
`Retry-After` honouring. 401 and every other 4xx are still never retried -- those are a real auth
failure or a defect, never papered over.

**`$top` degrade when the widened retry still isn't enough (`_listar_pagina`).** After a LIST page's
normal retry budget is spent and it is STILL answering with one of those codes, the page is retried
once more at `$top=10`, then once more at `$top=1` -- each a SINGLE attempt, no backoff sleep, since
this is a size escalation, not the same transient retry; each tried only if the page's current size is
still bigger than it. `top_lista_usado` always ends up naming whichever `$top` was last in use -- the
one that finally got a page through, or, if every size still failed, the smallest one tried. The cursor
and dedup semantics never change: a degraded page asks the exact same `$filter`/`$orderby`, just fewer
rows per response, so the same messages come back in the same order, only spread over more pages.
`_top_de`/`_con_top` read or rewrite a request's `$top` whether it is a fresh request (`parametros`
dict) or a `@odata.nextLink` continuation (`httpx.URL.copy_set_param`, since the link already bakes its
query in) -- degrade works identically on page 1 and on every later page.

**`carpetas`, previously `imap`-only, extended to `proveedor == "microsoft"` (`fuentes.py`,
`graph.py`, `proveedor.py`).** The build instructions asked for narrowing WHERE the list reads, but only
if a source's rules allow it; Graph's `/me/messages` has no such allow-list concept, so this reuses the
field IMAP already has rather than inventing a second one. When set, `GraphClient` reads each named
folder on its own, in the order configured, via `/me/mailFolders/{carpeta}/messages` instead of one
`/me/messages` call across the whole mailbox -- each entry is whatever Graph itself accepts there (a
well-known name like `inbox`, or a folder id), and each folder's own listing keeps every bit of today's
shape (filter, order, select, pagination, retry/degrade). Absent (the default, and every Microsoft
`fuente.json` before this slice) means today's behaviour exactly -- proven byte for byte by
`test_carpetas_no_configuradas_mantiene_la_peticion_de_hoy_byte_a_byte`. No CLI writes this for a
Microsoft source today (`agregar-fuente` only supports `imap`) -- an operator edits `fuente.json` by
hand, documented in `deploy/host/README.md`.

**`estado.json`/`estado`'s own stdout now also carry `top=<n>`** (`registro.py`'s `linea_resumen`,
`cli.py`) -- `getattr(proveedor, "top_lista_usado", None)`, the same optional-field pattern
`BUZON-GRAPH-TIMEOUT-1` used for `reintentos`, so a provider with no such concept (IMAP, Gmail, every
existing test fake) omits it rather than guessing.

### Tests, measured

15 new tests: `test_graph.py` (502 retried and succeeds; persistent 502 degrades to `$top=10` and
succeeds; persistent 502 at every size gives up and records `top_lista_usado == 1`; `carpetas` reads
only the named folders, in order, no duplicate; `carpetas` absent keeps today's request byte for byte --
5); `test_fuentes.py` (`carpetas` default/round-trip/absent-key/empty-list for `proveedor ==
"microsoft"`, mirroring the existing `imap` tests -- 4); `test_cli.py` (`top=` lands in both
`estado.json` and `estado`'s own stdout; a provider with no `top_lista_usado` never gets a guessed one --
2); `test_sincronizar.py` (502 retried and the cursor advances; persistent 502 degraded to `$top=10` and
the cursor advances; persistent 502 at every size stops the pass and leaves the cursor unchanged;
`carpetas` reads both configured folders and every message still reaches Finanzas exactly once, cursor
advancing to the later folder's own message -- 4). One existing test (`test_graph.py`'s
`test_500_nunca_se_reintenta`, which asserted 500 was NEVER retried) was replaced in place by
`test_500_se_reintenta_igual_que_503`, since that is exactly the behaviour this slice reverses --
mirroring `BUZON-GRAPH-TIMEOUT-1`'s own precedent for updating `test_error_de_conexion_lanza_
error_transitorio` in place when a slice changes what it asserts about.

### Gate, measured

`.venv/Scripts/python.exe -m pytest -q` on the slice branch (`feat/buzon-graph-502-1`), tree
`37dc082d218b29c63dd147bfebc176df28005717` (slice commit `d73ca172739b00b39adc1a5ba76a280b6b75ab81`):

    392 passed, 7 skipped in 43.97s

392 = the 377 `BUZON-GRAPH-TIMEOUT-1` recorded on `main` before this slice, plus this slice's 15 new
tests (above). Merge commit `325262bb268f186c6736a68d1d3f3a360b03bc17` has tree
`37dc082d218b29c63dd147bfebc176df28005717`, IDENTICAL to the slice branch's own tree (`git diff HEAD
feat/buzon-graph-502-1 --stat` against the merge commit was empty), so that run was not repeated for the
merge. The 7 skips are the same pre-existing Windows file-mode skips every earlier slice in this ledger
has recorded; none are new.

### Merge-policy-A classification, made by this slice

No schema or migration -- this repository has no database, same as every earlier entry. No
customer-facing bytes -- the new `top=<n>` field and the `carpetas` lever are operator-facing
(`estado.json`, `estado`'s own stdout, a hand-edited `fuente.json`), read and written only by Carlos
acting as this system's own administrator, the same reasoning `BUZON-GRAPH-TIMEOUT-1` recorded for
`finalizado_en`/`reintentos`. No cross-repo contract -- nothing here touches the Finanzas wire (the
`meta` fields this connector sends are untouched) or the Cuenta Duna wire; every change is this
connector's own internal Graph client behaviour. All three conditions clear, and the spec body itself
names the merge as a required step ("Full gate ..., merge to main (--no-ff) ...") -- merged on green, no
waiting.

### Attribution

Carlos named the production incident directly (`parada=graph listar http=502`, cursor frozen) and
delegated the fix as an operational one, no wire, no data shape (`ORCH-CARLOS-DELEGATES-TIER-1-1`). This
slice (`BUZON-GRAPH-502-1`) widened Graph's retried status-code class to the whole transient 5xx family,
added the `$top`-degrade escalation for a page that still fails past that, extended `carpetas` from
`imap`-only to also cover `microsoft` sources, and taught `estado.json`/`estado` to carry the `$top`
last in use.

---

## BUZON-CARPETAS-MICROSOFT-CLI-1 -- CLI for `carpetas` on an existing microsoft (or imap) source (2026-11-12)

### Why

`BUZON-GRAPH-502-1` made `carpetas` work for `proveedor == "microsoft"`, but `deploy/host/README.md`
still said there was no CLI for it -- an operator had to hand-edit `fuente.json` as `duna`. On 12 Nov,
right after that slice shipped, `hotmail-carlos` needed exactly that (Graph 502 even at `$top=1` against
`/me/messages`), and Carlos ran a python one-liner by hand to set it. Main reaches the host only through
`deploy/host/duna-deploy-buzon`, so this was the gap: operator tooling over an existing setting, no wire
(`ORCH-CARLOS-DELEGATES-TIER-1-1`).

### What changed

**`fijar-carpetas <id> [--carpeta X ...] [--todas]`** (`__main__.py`'s `_parsear_fijar_carpetas`,
`cli.py`'s `comando_fijar_carpetas`) -- sets `carpetas` on an existing `imap` or `microsoft` source to
exactly the folders given, or, with `--todas`, removes it entirely (back to today's default: every
folder). Validates the id exists (`fuentes.cargar_fuente`, which already raises `FuenteInvalidaError`
for an unknown one) and the provider is `imap` or `microsoft` before writing anything; prints the before
and after lists; never touches `credencial.bin` or the cursor.

**The write is atomic** (`fuentes.guardar_fuente_atomico`, new) -- a temp file in the SAME directory
(`tempfile.mkstemp`, mode 0600 where the OS supports it) followed by `os.replace`, never a truncate-in-
place window where a concurrent `sincronizar` pass or a reader could see a half-written `fuente.json`.
Every other writer of `fuente.json` keeps `guardar_fuente`'s own truncate-in-place write, unchanged --
both now share one `_contenido_fuente_json` for the JSON shape, so "preserving every other key" is
exact: whatever `FuenteConfig`'s own round trip already carries (id, proveedor, direccion, etiqueta,
token_finanzas_ref, activa, host, port, origen, carpetas), nothing invented or dropped.

**`carpetas-graph <id>`** (`cli.py`'s `comando_carpetas_graph`, `graph.py`'s `GraphClient.listar_carpetas`)
-- the `microsoft`-source twin of the existing `imap`-only `carpetas <id>`: signs in with the already-
stored token and lists every TOP-LEVEL Graph mail folder's display name and id, plus its well-known name
(`inbox`, `archive`, `sentitems`, `drafts`, `deleteditems`, `junkemail`, `outbox`) where one applies, so
an operator can choose a `carpetas` entry without guessing. One `/me/mailFolders` LIST call plus one
`/me/mailFolders/{nombre}` GET per well-known name probed (a 404 just means that mailbox lacks it, not an
error) -- every one of them a GET, like every other call `GraphClient` makes; Graph's own `mailFolder`
resource carries no field naming a folder's well-known name, so this is how `graph.py`'s own docstring
explains finding it. Never called from the sync path (`listar_mensajes`).

**Both exposed through `deploy/host/duna-deploy-buzon`:**
- `carpetas <id>` -- reads the source's own `proveedor` straight from its `fuente.json` (never a secret;
  root can read any file on the host) via the new pure, path-parameterized `_proveedor_desde_archivo`
  (same technique `_archivo_cambio`/`_forma_sudoers_es_estricta` already use), then runs `python -m
  duna_buzon carpetas <id>` for `imap` or `carpetas-graph <id>` for `microsoft`, read-only, as `duna`.
- `fijar-carpetas <id> <carpeta>...` / `fijar-carpetas <id> --todas` -- forwards to `python -m duna_buzon
  fijar-carpetas`, translating each positional folder name into its own `--carpeta` argument; every
  folder name reaches python as its own argv element inside an array (`comando+=(...)`,
  `"${comando[@]}"`), never built into a shell string, never `eval`'d.
- An id must match `[a-z0-9-]+` and a folder name `[A-Za-z0-9_ .-]+` (or a Graph id, same shape),
  checked in `parse_args` BEFORE `require_root`, same ordering every other subcommand already uses.
  `--todas` mixed with any other positional argument is refused by name as the ambiguous shape it is,
  rather than silently read as a folder literally named `--todas` (which would otherwise fit the
  allowlist, since `-` is a permitted character). The sudoers rule (`carlos ALL=(root) NOPASSWD: /usr/
  local/sbin/duna-deploy-buzon`, no argument list) already allows any argument to this one fixed path --
  unchanged.

**`deploy/host/README.md`** -- the "There is no CLI for this on a Microsoft source today ... edit
`fuente.json` by hand" sentence is replaced by a new "Choosing and setting `carpetas`" section
documenting both verbs; the "Use" block gained the two new invocation lines. **Top-level `README.md`**
gained `carpetas-graph`/`fijar-carpetas` bullets next to the existing `carpetas <id>` one, and a
sentence in "When Graph can't list the whole mailbox" pointing at them instead of "no CLI".

### Tests, measured

30 new `def test_` functions (`git diff bbfafe8 feat/buzon-carpetas-microsoft-cli-1 -- tests/`, zero
removed), several of them `@pytest.mark.parametrize`d into multiple collected cases: `test_graph.py`
(`listar_carpetas` marks the well-known match and sorts by name; no well-known match at all; 401 still
raises `GraphAutenticacionError` -- 3); `test_cli.py` (`carpetas-graph` lists name/id/well-known and
refuses a non-microsoft source, an unknown id, and "no account" the same way `carpetas` already does;
`fijar-carpetas` sets then removes with `--todas`, works for `imap` too, refuses an unknown id and a
`google` source, preserves every other key including `credencial.bin`, and leaves no `.tmp` behind --
10); `test_main.py` (`carpetas-graph`/`fijar-carpetas` usage-shape rejections and dispatch,
`--carpeta`+`--todas` together refused, `--carpeta` with no value refused -- 8);
`test_host_deploy_entry_script.py` (9 functions, several parametrized: the id/carpeta allowlist accepts
the valid shapes and rejects the invalid ones -- including shell metacharacters and the
`--todas`-mixed-with-a-folder ambiguity -- for both `carpetas` and `fijar-carpetas`;
`_proveedor_desde_archivo` reads the field or refuses by name; `cmd_carpetas` routes `imap`/`microsoft`
to the right python subcommand and refuses `google`; the array-based systemd-run call never interpolates
into a string).

### Gate, measured

`uv run pytest -q` on the slice branch (`feat/buzon-carpetas-microsoft-cli-1`), tree
`1fb7b935a054eabae5ab6e9f3d563181cfee34c7` (slice commit `4ef010fa393b24c3add34ca174495d9862bd3433`):

    434 passed, 7 skipped in 47.66s

`git diff bbfafe8 feat/buzon-carpetas-microsoft-cli-1 -- tests/` shows 30 new `def test_` lines and zero
removed, some of them `@pytest.mark.parametrize`d into several collected cases each -- consistent with
434, though this entry does not re-run `main`'s own pre-slice total to reconcile the exact arithmetic
(the closest prior figure in this ledger, `BUZON-GRAPH-502-1`'s 392, was measured on an earlier tree and
several unrelated slices have landed on `main` since, so it is not directly comparable). Merge commit
`fc895e0a9e16c1089c9c6a10aa116fb9b2ddf9d9` has tree `1fb7b935a054eabae5ab6e9f3d563181cfee34c7`, IDENTICAL
to the slice branch's own tree, so that run was not repeated for the merge. The 7 skips are the same
pre-existing Windows file-mode and bash-availability skips every earlier slice in this ledger has
recorded; none are new.

### Merge-policy-A classification, made by this slice

No schema or migration -- this repository has no database, same as every earlier entry. No
customer-facing bytes -- every new byte here is operator-facing: a CLI's own stdout (`carpetas-graph`,
`fijar-carpetas`), a root-owned shell script's own stdout/stderr, and a `fuente.json` field read and
written only by Carlos acting as this system's own administrator -- the same reasoning `BUZON-GRAPH-
502-1` and `BUZON-GRAPH-TIMEOUT-1` recorded for `top=<n>`/`reintentos`/`finalizado_en`. No cross-repo
contract -- nothing here touches the Finanzas wire or the Cuenta Duna wire; every change is this
connector's own internal CLI and its own deploy script. All three conditions clear, and the spec body
names the merge as a required step ("Full gate ..., merge to main (--no-ff), DECISIONS.md") -- merged on
green, no waiting.

### Attribution

Carlos's own production incident (12 Nov, Graph 502 at `$top=1` against `hotmail-carlos`) needed a python
one-liner run by hand because this CLI did not exist yet; this slice (`BUZON-CARPETAS-MICROSOFT-CLI-1`)
closes that gap with `fijar-carpetas`/`carpetas-graph` and their own `duna-deploy-buzon` wrapper, so the
next such incident needs no hand edit and no python one-liner.

---

## BUZON-FUENTES-LISTAR-RESILIENTE-1 -- one broken `fuente.json` is reported, not a crash; `estado` nudges `entrar`/`sincronizar` (2026-11-19)

### Why

From the 19 Nov hub census of this repository (`logs/worker-*-hub-censo-2026-11-19-duna-buzon-*.json`,
follow-up `BUZON-FUENTES-LISTAR-RESILIENTE-1`), under hub principle `hit_01m7szasnqh2sqbs4dzjrsvyqm`
("one failing part is reported, never takes the whole read down"): `fuentes.listar_fuentes`
(`fuentes.py:282-295`) was called by every CLI command and raised `FuenteInvalidaError` on the FIRST bad
`fuente.json` it found, aborting the listing of every other source -- a healthy source sitting right
next to a broken one became unreachable by every command (`entrar --fuente <healthy-id>`,
`sincronizar`, `estado`, `fuentes`, all of them) the moment the broken one existed. Folded in the same
slice: `BUZON-ESTADO-FUENTE-NUEVA-NUDGE-1`, since `comando_estado` printed `cursor=none` identically for
a source that had never run `entrar` at all and one that had signed in but never completed a
`sincronizar` pass -- Carlos could not tell from `estado`'s own output which command to run next.
Approved under `ORCH-CARLOS-DELEGATES-TIER-1-1` (Carlos delegates), Main as decider.

### What changed

**`fuentes.listar_fuentes` no longer raises on a broken sibling.** A new
`listar_fuentes_con_errores(directorio_datos) -> tuple[list[FuenteConfig], list[FuenteError]]` walks
`fuentes/` exactly as before but now catches `FuenteInvalidaError` PER child directory: a source whose
`fuente.json` fails to parse is skipped and recorded as a `FuenteError(id, motivo)` -- `motivo` is
`FuenteInvalidaError`'s own message, which (every raise site in `fuentes.py`) names only the id and the
shape complaint, never a credential or a message's own content. `listar_fuentes` itself is now a thin
wrapper that returns only the healthy half of that pair, so every existing caller (`comando_entrar`,
`comando_sincronizar`, `comando_estado`, `comando_carpetas`, `comando_carpetas_graph`,
`comando_agregar_fuente`) is resilient with no call-site change: a named, healthy source keeps working
exactly as before, regardless of what else is broken under `fuentes/`.

**`comando_fuentes` (`fuentes`, the one command whose whole job is to show the roster) surfaces the
errors.** It now calls `listar_fuentes_con_errores` directly, prints every healthy source exactly as
before, then prints one line per broken source to stderr:

    fuente={id} error: {motivo}

and returns `1` when any source is broken (`0` when the roster is entirely healthy or entirely empty) --
the same "non-zero means look at stderr" convention `sincronizar` and `estado` already use.

**`comando_estado` (`BUZON-ESTADO-FUENTE-NUEVA-NUDGE-1`) tells the two "no cursor yet" cases apart.**
Previously, a source with no `cursor.json` always printed only `fuente={id} cursor=none`, whether it had
never run `entrar` or had signed in and simply never completed a sync pass. It now also prints, in the
same no-cursor branch, the exact next command:

    fuente={id} siguiente: ejecute 'entrar --fuente {id}' para firmar

when the source is `origen="local"` and its own `credencial.bin` is absent or empty (the same
"empty is absent" convention `auth.py`/`credencial_imap.py` already use for that file) -- it has never
even signed in; or

    fuente={id} siguiente: ejecute 'sincronizar' para la primera pasada

otherwise -- either a `local`-origen source that has signed in but never synced, or an `origen="cuenta"`
source, which has no `entrar` step at all (its token is pulled live, never stored) and so is always in
this second bucket. A new `cli._tiene_credencial(directorio_fuente)` helper (file present and
non-empty) backs the check. Both new lines mirror the existing sibling string
`comando_agregar_fuente` already prints ("ejecute 'entrar --fuente {id_fuente}' para firmar.") --
quoted here verbatim, decided within this repo's existing register (Spanish imperative instruction,
English-style `key=value` fields) and reported as this slice's own choice
(`ORCH-CARLOS-DELEGATES-TIER-1-1`).

**`README.md`** documents both: `fuentes` now reports a broken source by id and reason without
stopping the rest of the list, and `estado` now names the next command for a source with no cursor yet.

### Tests, measured

7 new `def test_` functions (`git diff main~1 main -- tests/test_fuentes.py tests/test_cli.py | grep -c
'^+def test_'`): `tests/test_fuentes.py` gets `test_listar_fuentes_una_fuente_rota_no_aborta_el_resto`,
`test_listar_fuentes_con_errores_reporta_id_y_motivo_sin_contenido`,
`test_listar_fuentes_con_errores_sin_nada_roto`; `tests/test_cli.py` gets
`test_fuentes_una_fuente_rota_no_aborta_la_lista_de_las_sanas`,
`test_estado_fuente_nunca_entrada_sugiere_entrar`,
`test_estado_fuente_entrada_pero_sin_sincronizar_sugiere_sincronizar`,
`test_estado_fuente_de_cuenta_sin_sincronizar_sugiere_sincronizar`.

`.venv/Scripts/python.exe -m pytest -q` on the slice branch (`feat/buzon-fuentes-listar-resiliente-1`),
tree `980c5b0840013499ff6cf33fa635eb6bdde51632` (slice commit
`be4d4e4edb6df83fd142074fe8a9d0aacb810a30`):

    472 passed, 7 skipped in 416.49s (0:06:56)

The 7 skips are the same pre-existing Windows file-mode skips every earlier slice in this ledger has
recorded; none are new. Merge commit `767c53e2145795055005d82e8a5efceb809a47f7` has tree
`980c5b0840013499ff6cf33fa635eb6bdde51632`, IDENTICAL to the slice branch's own tree (`git diff
feat/buzon-fuentes-listar-resiliente-1 HEAD` empty), so that run was not repeated for the merge.

### Merge-policy-A classification, made by this slice

No schema or migration -- this repository has no database, same as every earlier entry. No
customer-facing bytes -- every new byte here is operator-facing: `fuentes`' and `estado`'s own stdout/
stderr, read only by Carlos running this connector's CLI by hand on `duna-prod-1`, and `README.md`, read
only by Carlos as this connector's own operator -- the same reasoning `BUZON-ENTRADA-UNIDADES-
REGISTROS-1` and `BUZON-CARPETAS-MICROSOFT-CLI-1` recorded for their own operator-facing fields. No
cross-repo contract -- nothing here touches the Finanzas wire or the Cuenta Duna wire; `fuentes.py` and
`cli.py` are this connector's own internal enumeration and CLI-output logic. All three conditions clear,
and the approval ("Merge on green.") authorizes merging without waiting -- merged on green, no pause.

### Attribution

Main, deciding under delegation (`ORCH-CARLOS-DELEGATES-TIER-1-1`): a reversible behaviour fix on an
existing surface (`fuentes.listar_fuentes`, `cli.comando_estado`/`comando_fuentes`) under hub principle
`hit_01m7szasnqh2sqbs4dzjrsvyqm`, with its two new person-facing strings decided within this repo's own
register and quoted verbatim above, matching the sibling string `comando_agregar_fuente` already prints.

## BUZON-ENTRADA-UNIDADES-REGISTROS-1 -- diff-install every unit, a drift report, a `registros` verb (2026-11-12)

### Why

Two gaps measured in `duna-pagos` on 12 Nov apply here too (`ORCH-CARLOS-DELEGATES-TIER-1-1`): (a) a
deploy that only ever diffed and installed the main `.service` would leave any other job unit on the
host stale (`PAGOS-DEPLOY-INSTALA-UNIDADES-1`, pagos `ba4ab93`); (b) with no log verb, a failing job
could not be diagnosed without Carlos running `journalctl` by hand (`PAGOS-ENTRADA-REGISTROS-1`, pagos
`5582345`). Carlos named the second half directly: `duna-buzon.service` had failed all evening on a
Microsoft 502 and only `status`'s one summary line was readable.

### What changed

**`deploy`'s unit install is now diffed, not blind.** For each of `duna-buzon.service`/
`duna-buzon.timer` (the `UNIDADES` array, a fixed constant), `_archivo_cambio` compares the repository's
own `deploy/systemd/<unit>` against what is installed under `/etc/systemd/system/<unit>`; only a unit
that differs (or is missing) gets `install -o root -g root -m 644`'d, and each is printed as `sin
cambios` or `actualizada`. Exactly one `systemctl daemon-reload` runs after the per-unit loop, then
`systemctl enable --now duna-buzon.timer` -- unchanged from before, since this repo has only the one
timer. `--dry-run` describes the same plan without installing or reloading anything (`deploy/host/
duna-deploy-buzon`'s `cmd_deploy`). The main service's restart policy (none) and its health check
(`python -m duna_buzon estado` under `systemd-run`) are untouched.

**`status` gained a read-only drift report.** A new `_estado_unidad` prints `coincide`/`distinta`/
`falta` per unit (same comparison `_archivo_cambio` already made, reworked as a three-way read); a new
`_user_de_unidad` prints the installed `User=` of `duna-buzon.service` (the one job unit, `UNIDADES_JOB`);
and `status` now also prints `/srv/duna/data/buzon`'s owner and mode (`stat -c '%U:%G'`/`stat -c '%a'`).
When `duna-buzon.service`'s own `systemctl is-active` reports `failed`, `status` automatically prints
its last 30 redacted journal lines -- the exact gap Carlos hit: only the summary line was readable while
the service failed all evening.

**A new read-only verb, `registros <unidad> [n]`** (same contract as `duna-pagos`'): `<unidad>` is
checked against the fixed `UNIDADES` allowlist (`_unidad_permitida`, membership only, never a regex);
`n` is normalized by `_normaliza_n` -- empty defaults to 40, anything over 200 is capped to 200, anything
that is not a positive integer is refused with `usage` before `require_root` runs (same ordering every
other subcommand's shape check already uses). `cmd_registros` runs exactly `journalctl -u "$unidad" -n
"$n" --no-pager -o cat`, the unit name passed as `journalctl`'s own `-u` argument and never built into a
shell string, piped through a new `_redactar` filter shared with `status`'s own auto-print: the
credentials half of a `postgres://`/`postgresql://` URL and the value of any
`...SECRET=`/`...TOKEN=`/`...PASSWORD=`/`...KEY=` assignment each become `***`.

**`deploy/host/README.md`** documents the diffed install, the drift report's new fields, and a new
"Reading a unit's own log (`registros`)" section.

### A pre-existing test guard was too broad, fixed rather than widened around

`test_ningun_valor_de_entorno_se_imprime`/`test_los_archivos_de_entorno_nunca_se_leen_con_cat`
(`tests/test_host_deploy_entry_script.py`) banned the bare substring `"cat "` anywhere in an `echo`/
`printf` line or in the whole script, to catch an actual `cat` of the two secret env files. Journalctl's
own mandatory `-o cat` output-format flag (required by this spec) contains that exact substring and is
not a shell `cat` invocation, so both guards false-positived against it. Measured, not guessed: the
first run after this slice's changes failed exactly those two tests and no others (68 passed, 2 failed).
Fixed by tightening the regex to `(?<!-o )\bcat\b` -- a `cat` word not immediately preceded by `-o ` --
which still catches a real `cat $ENV_CLIENTE`/`cat $ENV_TOKEN` and now also lets `-o cat` through; both
tests pass clean afterward.

### Tests, measured

31 new/changed `def test_` functions in `tests/test_host_deploy_entry_script.py`
(`_estado_unidad`'s three outcomes; `_user_de_unidad` with/without a `User=` line and a missing file;
`_unidad_permitida`'s allowlist, parametrized over valid and foreign unit names; `_normaliza_n`'s
default/cap/rejection, parametrized over 11 cases; `_redactar` against a postgres URL and four
secret-style assignments in one stream, alongside an untouched plain line; `registros`'s recognized-
subcommand check; its invalid-shape rejections (missing unit, unknown unit, `n=0`/negative/non-numeric,
a shell-metacharacter unit, extra arguments), parametrized over 7 cases; its valid shapes failing only on
EUID, parametrized over 5 cases; a static check that the unit name never reaches `journalctl` through a
built string; a static check that `cmd_deploy`'s per-unit loop only installs inside its own
`_archivo_cambio` conditional and runs exactly one `daemon-reload` after the whole loop; a static check
that `status`'s real branch calls `_estado_unidad`/`_user_de_unidad`/`_mostrar_registros` and prints the
data directory's owner). Two pre-existing tests tightened per the deviation above.

`uv run pytest -q` on the slice branch (`feat/buzon-entrada-unidades-registros-1`), tree
`944d86578dd4728b2f423be39f369a35005de84b` (slice commit `c0fb8dc3dc40e23ee8dbc3efc3ee479f9c9efde1`):

    465 passed, 7 skipped in 50.37s

Merge commit `5d0d368811f0a14d5983969c75bc95162e0108ec` has tree
`944d86578dd4728b2f423be39f369a35005de84b`, IDENTICAL to the slice branch's own tree (`git diff
feat/buzon-entrada-unidades-registros-1 HEAD` empty), so that run was not repeated for the merge. The 7
skips are the same pre-existing Windows file-mode skips every earlier slice in this ledger has recorded;
none are new.

### Merge-policy-A classification, made by this slice

No schema or migration -- this repository has no database, same as every earlier entry. No
customer-facing bytes -- every new byte here is operator-facing: a root-owned shell script's own stdout
(`sin cambios`/`actualizada`, the drift report, `registros`' own journal output), and `deploy/host/
README.md`, read and acted on only by Carlos as this system's own administrator -- the same reasoning
`BUZON-CARPETAS-MICROSOFT-CLI-1` and `BUZON-GRAPH-502-1` recorded for their own operator-facing fields.
No cross-repo contract -- nothing here touches the Finanzas wire or the Cuenta Duna wire; every change is
this connector's own deploy script and its own systemd units. All three conditions clear, and the spec
body itself names the merge as a required step ("Full gate ..., merge to main (--no-ff), DECISIONS.md
entry") -- merged on green, no waiting.

### Attribution

Carlos delegated this as an operational gap, the same shape as `duna-pagos`' own fix, no wire, no data
shape (`ORCH-CARLOS-DELEGATES-TIER-1-1`). This slice (`BUZON-ENTRADA-UNIDADES-REGISTROS-1`) diffs every
unit this repo ships instead of blindly overwriting it, gives `status` a drift report and an automatic
failure excerpt, and adds the read-only `registros` verb Carlos needed the evening `duna-buzon.service`
failed against Microsoft's own 502 with nothing but a summary line to read.

## BUZON-ESTADO-HISTORIAL-PASADAS-1 -- a bounded per-source pass history, so `estado` outlives a bad pass (2026-11-19)

### Why

Follow-up to `BUZON-HUB-CENSO-2026-11-19`, hub principle `hit_01m7syzg15w97bzrbfz4dmc6fq` ("bulk work
stays visible after it runs"): `estado.json` is OVERWRITTEN on every `sincronizar` pass, so `estado` only
ever shows the latest one. A pass that forwarded nothing, or stopped on an auth/transport/Finanzas
failure, erased the summary of every successful pass before it -- the operator had no way to see "it was
fine yesterday, then it started failing" without reading raw logs.

### What changed

**A new module, `historial.py`**, keeps the most recent `CAPACIDAD` (20) pass summaries per source,
beside (never instead of) `estado.json` -- `fuentes/<id>/historial.jsonl`, one JSON object per line,
oldest first. `agregar_pasada` rewrites the whole file atomically (temp file in the same directory +
`os.replace`, mode 0600 where the OS supports it) every call, trimmed to the cap in that same write --
the same atomic-rewrite shape `fuentes.guardar_fuente_atomico` already uses. `leer_historial` skips a
line that is not valid JSON or is missing a required field, counting it rather than raising, so one
corrupt line never hides every other recorded pass -- and the very next `agregar_pasada` heals it away,
since every write is a full rewrite from what `leer_historial` itself could parse.

**`cli._ejecutar_una_fuente` appends to this history at both of its `estado.json` writes** (the clean
finish, and `_detenida`'s own early-stop path) -- same `Contadores`, same stop reason (`motivo`, already
a content-free, controlled-vocabulary string: "auth error=...", "finanzas enviar http=...", never
message content), same `finalizado_en` moment as the `estado.json` line just written. `estado.json`
itself is byte-for-byte unchanged, so every existing reader of it (the health check in `deploy/host/
duna-deploy-buzon`, `README.md`'s own description) keeps working with no change. `omitidos` in the
history record merges `Contadores.omitidos_por_reglas` and `omitidos_por_413_422` -- the compact table is
a trend view, not the full per-reason breakdown `estado.json`/`registro.linea_resumen` still carry for
the latest pass.

**`estado` prints the latest pass exactly as before, then a compact table of PREVIOUS passes.** The
latest entry in `historial.jsonl` duplicates what `estado.json` already showed, so the table is
`historial[:-1]`, most recent first, one line per pass:

    fuente={id} historial finalizado_en=<...> vistos=<n> reenviados=<n> duplicados=<n> omitidos=<n> errors=<motivo|->

`errors` is the pass's own stop reason, or `-` for a pass that finished clean. Default: up to 5 previous
passes (`cli.PASADAS_PREDETERMINADAS`); a new `--pasadas <n>` flag (`__main__.py`, `estado` only) widens
this, clamped to `historial.CAPACIDAD` -- never more than the file itself can hold, however large `n` is.
`--pasadas 0` suppresses the table entirely; no history yet, or exactly one pass recorded ("today" with
nothing before it), is silence, not an empty table. A corrupt history line is noted once on stderr by
count (`fuente={id} historial: {n} linea(s) descartada(s) (formato invalido)`) and never fails the
command.

`registro.py`'s own `_formato_fecha_utc` is now public (`formato_fecha_utc`) so `cli.py` stamps a
history record with the EXACT same timestamp format `linea_resumen` already uses for `finalizado_en` --
one formatting rule, not two.

**`README.md`** documents the new `historial.jsonl` file and `estado [--pasadas <n>]`'s new table.

### Shape decisions this slice made, not otherwise specified

- **`errors` carries the stop reason text, not just a count.** The spec's own compact-table column list
  names `errors` alongside five integer counts, which could have meant "a count of errors". The
  motivation line ("a pass that... failed, hides what the ones before it did") is about SEEING why a
  past pass failed, and `motivo` is already a short, content-free, controlled-vocabulary string reused
  unchanged from `estado.json`'s own `parada=` field -- the richer signal at no extra invariant risk.
- **`omitidos` merges two existing counters** (`omitidos_por_reglas`, `omitidos_por_413_422`) rather than
  adding two more columns -- the spec's own column list names one `omitidos`, and the full breakdown
  stays available in `estado.json`'s own last-pass line directly above the table.
- **Default table depth of 5**, not otherwise specified beyond "shows more" implying some smaller
  default exists. Picked as a size that fits a normal terminal without scrolling for the common case
  (one failure, a handful of successes around it), while `--pasadas <n>` reaches the full 20 on request.

### Tests, measured

22 new `def test_` functions: `tests/test_historial.py` (new file) gets 8 -- `leer_historial` with no
file, a normal round trip, a stop reason preserved, trimming at `CAPACIDAD` (oldest dropped first), a
corrupt line skipped and counted, a line missing a required field skipped and counted, a corrupt line on
disk healed away by the next `agregar_pasada`, and the file's own 0600 mode (skipped on Windows, same
convention as `test_cursor.py`). `tests/test_cli.py` gets 10 -- no history yet is silent, exactly one
pass recorded is silent, previous passes shown most-recent-first excluding the latest, a failed pass's
`errors` text shown, a clean pass's `errors=-`, the default depth of 5, `--pasadas` widening past the
default, `--pasadas` beyond the cap clamped to what is actually available, `--pasadas 0` suppressing the
table, and a corrupt line noted on stderr without failing the command. `tests/test_main.py` gets 4 --
`--pasadas` omitted passes `None`, `--pasadas <n>` passes the parsed int, a non-numeric value and a
value-less flag are both usage errors; one pre-existing test (`test_estado_se_despacha_con_la_config_del_
entorno`) widened its fake's signature to accept the new `pasadas` keyword, since nothing about that
test's own assertions changed.

`uv run pytest -q` on the slice branch (`feat/buzon-estado-historial-pasadas-1`), tree
`72134b3dcee3cf514d177a52ddb5d3e25ef972b3` (slice commit `5626f19ba5d296e4c3e880a6b765608a4556126f`):

    493 passed, 8 skipped in 350.05s (0:05:50)

The 8 skips are the same pre-existing Windows file-mode skips every earlier slice in this ledger has
recorded, plus this slice's own new `historial.jsonl` mode-0600 test skipping the same way on this
Windows development machine -- one more skip than the previous entry's 7, exactly accounting for the one
new `no_es_windows`-gated test. 472 passed on `main` before this slice (`BUZON-FUENTES-LISTAR-
RESILIENTE-1`'s own measured figure) plus 21 of this slice's 22 new tests passing (the 22nd is the new
skip) accounts for 493 exactly.

Merge commit `42dccb55e9be9822783834d298dca2198bcf9fbd` has tree `72134b3dcee3cf514d177a52ddb5d3e25ef972b3`,
IDENTICAL to the slice branch's own tree (`git diff feat/buzon-estado-historial-pasadas-1 HEAD -- .
':!DECISIONS.md'` empty), so that run was not repeated for the merge.

### Merge-policy-A classification, made by this slice

No schema or migration -- this repository has no database, same as every earlier entry. No
customer-facing bytes -- every new byte here is operator-facing: `estado`'s own stdout/stderr, read only
by Carlos running this connector's CLI by hand, and `README.md`, read only by Carlos as this connector's
own operator -- the same reasoning `BUZON-FUENTES-LISTAR-RESILIENTE-1` and `BUZON-ENTRADA-UNIDADES-
REGISTROS-1` recorded for their own operator-facing fields. No cross-repo contract -- nothing here
touches the Finanzas wire or the Cuenta Duna wire; `historial.py` and `cli.py`'s own changes are this
connector's own internal state and CLI-output logic. All three conditions clear, and the approval
("Merge on GREEN.") authorizes merging without waiting -- merged on green, no pause.

### Attribution

Main, deciding under delegation (`ORCH-CARLOS-DELEGATES-TIER-1-1`): a reversible addition to an existing
read-only operator surface (`estado`), no wire, no schema, following directly from the hub census this
slice is a named follow-up to.

## BUZON-CURSOR-REBOBINAR-CLI-1 -- `rebobinar-cursor`, dry-run by default (2026-11-19)

### Why

Follow-up to `BUZON-HUB-CENSO-2026-11-19`: there is no supported way to re-read a source from an
earlier date after a bad pass -- today that means hand-editing `cursor.json` on the server, which the
orchestrator may not do (it runs only entry-script verbs).

### What changed

**A new CLI verb, `rebobinar-cursor <fuente> <fecha ISO> [--aplicar]`** (`cli.comando_rebobinar_cursor`,
wired in `__main__.py`). DRY-RUN BY DEFAULT: validates the source and the date, then prints the current
cursor and the computed new one, writing nothing. `--aplicar` writes `cursor.json` atomically via a new
`cursor.guardar_cursor_atomico` (temp file in the same directory + `os.replace`, mode 0600 where the OS
supports it -- the same shape `fuentes.guardar_fuente_atomico` already uses for `fuente.json`;
`cursor.guardar_cursor`'s own existing truncate-in-place write is untouched, since every other caller of
it keeps that behaviour). Refuses, before writing anything: an unknown source (`FuenteInvalidaError`,
same message `fijar-carpetas` already uses), a date that is not valid ISO 8601, a date in the future,
and a date more than `LIMITE_REBOBINAR_DIAS` (90, a named constant) days before now.

**`--aplicar` also clears `cursor_proveedor.json`** (a new `cursor.borrar_cursor_proveedor`, a plain
`Path.unlink(missing_ok=True)`). This is NOT optional polish: reading `cliente_imap.py` showed that an
IMAP source's own per-folder UID watermark (`_buscar_uids`'s `cursor_previo`) takes precedence over the
date cursor whenever one is stored for that folder -- rewinding `cursor.json` alone would leave an IMAP
source's next pass resuming from its old UID, never re-reading anything, which would make the verb a
silent no-op for exactly the provider most likely to need it (a shared mailbox with folder-filed
invoices). Clearing the file forces the next pass back onto the date-based `SINCE` fallback, from the
rewound date. Microsoft (`graph.py`) and Gmail (`gmail.py`) are purely date-driven and never read this
file at all (confirmed by reading both modules' own `listar_mensajes`), so clearing it for them is a
safe no-op.

**Exposed on the host entry script** as `rebobinar-cursor <id> <fecha ISO> [--aplicar]`
(`cmd_rebobinar_cursor`), with the same argument-shape guard style as every other verb: `<id>` against
the existing `ID_FUENTE_RE`, `<fecha>` against a new `FECHA_ISO_RE` (shape only -- a bare ISO date or a
full timestamp with an optional fractional second and `Z`/offset suffix; calendar validity, the future
check and the 90-day cap are all python's own job, never duplicated in bash). Each argument reaches
python as its own argv element, never a shell string.

**The duplicate guard, measured, not assumed.** `sincronizar.ejecutar_pasada` calls
`FinanzasClient.enviar_entrada` for every message that matches this source's own rules; Finanzas answers
a message whose `id_externo` it already holds with HTTP `200` (not `201`), which `finanzas.py`'s own
`enviar_entrada` turns into `ResultadoEnvio(estado="duplicated", ...)`, and `ejecutar_pasada` counts that
as `Contadores.duplicados`, advancing the cursor past it, NEVER incrementing `Contadores.reenviados` or
calling anything that could re-send to a downstream system. This is true regardless of how the pass's
own starting cursor was set, so a rewound source's next pass can only ever re-count mail Finanzas
already holds (`duplicados`) or newly forward mail it does not (`reenviados`) -- never re-forward the
same entry twice. Read from `src/duna_buzon/finanzas.py` lines 120-131 and
`src/duna_buzon/sincronizar.py` lines 143-151; no live run against a real mailbox or a real Finanzas
instance was needed or performed, per this repository's own gate contract.

### Shape decisions this slice made, not otherwise specified

- **`--aplicar` also clears `cursor_proveedor.json`**, which the spec's own build instructions did not
  name. Not doing so would make the verb's own stated purpose ("re-read a source from an earlier date")
  silently fail for every IMAP source -- a correctness gap discovered by reading `cliente_imap.py`
  before writing a single line of CLI code, not a product-shape question, so it did not warrant
  `RULING_NEEDED`.
- **`cursor.guardar_cursor_atomico` is a new function, not a change to `guardar_cursor`.** Every other
  caller of `guardar_cursor` (`sincronizar`'s own per-pass advance, many times a day) keeps its existing
  truncate-in-place write unchanged; only this one, occasional, operator-triggered write gets the
  temp-file-then-`os.replace` treatment the spec asked for.
- **Dry-run prints `cursor antes=<...>`/`cursor despues=<...>`** (and, when present, a third line noting
  `cursor_proveedor` will also be cleared), the same two-line `antes=`/`despues=` shape
  `fijar-carpetas` already uses for `carpetas` -- one convention for every "show the change" CLI output,
  not a new one for this verb.
- **A naive (no-timezone) `<fecha>` is assumed UTC**, matching `reglas.json`'s own `desde` field and
  every other datetime this connector parses from outside input.

### Tests, measured

27 new `def test_` functions (one `grep -c '^+def test_'` over this slice's own diff against `main`):
`tests/test_cursor.py` gets 6 -- `guardar_cursor_atomico`'s round trip, no leftover `.tmp` file,
overwriting an existing cursor, and its own 0600 mode (skipped on Windows, same convention as this
file's existing tests); `borrar_cursor_proveedor` with no file present (no-op, never raises) and
removing an existing one. `tests/test_cli.py` gets 11 -- an unknown source, an invalid date, a future
date, more than 90 days back refused, exactly 90 days back accepted, dry-run prints before/after and
writes nothing (with and without a prior cursor), `--aplicar` writes atomically with no leftover `.tmp`,
`--aplicar` clears an existing `cursor_proveedor.json` for an imap source, `--aplicar` with no
`cursor_proveedor.json` present does not fail, and a naive date is read as UTC. `tests/test_main.py`
gets 5 -- missing arguments, an unrecognized third argument, dispatch without `--aplicar`, dispatch with
`--aplicar`, and an unrecognized flag in the `--aplicar` position are all usage errors or correct
dispatch. `tests/test_host_deploy_entry_script.py` gets 5 -- the subcommand is recognised, a
parametrized set of invalid shapes (missing arguments, an invalid id, an invalid date shape, a trailing
unknown argument, shell metacharacters in either positional) are refused with usage, a parametrized set
of valid shapes (a bare date, a full timestamp with `Z` or an offset, with and without `--aplicar`)
passes shape-checking and fails only on EUID, python-forwarding never interpolates into a shell string,
and `--aplicar` is translated to the python flag only inside its own `if`.

`uv run pytest -q` on the slice branch (`feat/buzon-cursor-rebobinar-cli-1`), tree
`e7068156e1dd99aa8155e50f338282f8de13dbeb` (slice commit `80c486dbc76a9ea2810c82b8cd7e42afe6d03654`):

    530 passed, 9 skipped in 280.15s (0:04:40)

The 9 skips are the same pre-existing Windows file-mode skips every earlier slice in this ledger has
recorded, plus this slice's own new `guardar_cursor_atomico` mode-0600 test skipping the same way on
this Windows development machine -- one more skip than the previous entry's 8, exactly accounting for
the one new `no_es_windows`-gated test. This slice's base (`main` at the commit this branch forked
from, `31b445dbb00eee86f32dbffba5540f662ef51465`) already carried 503 passing tests (530 minus this
slice's own 27, all of which pass), ahead of the 493 the previous ledger entry measured -- the
difference is other work landed on `main` between that entry and this slice's base, outside this
slice's own diff and not re-verified here.

### Merge-policy-A classification, made by this slice

No schema or migration -- this repository has no database, same as every earlier entry. No
customer-facing bytes -- every new byte here is operator-facing: `rebobinar-cursor`'s own stdout/stderr,
read only by Carlos (or the orchestrator, running the entry script through `sudo -n`) as this
connector's own operator, and the two `README.md` files, read only by the same operator -- the same
reasoning every earlier CLI-verb entry in this ledger recorded. No cross-repo contract -- nothing here
touches the Finanzas wire or the Cuenta Duna wire; the duplicate-guard behaviour this slice depends on
and documents is read, not changed. All three conditions clear, and the approval ("Merge on GREEN.")
authorizes merging without waiting -- merged on green, no pause.

### Attribution

Main, deciding under delegation (`ORCH-CARLOS-DELEGATES-TIER-1-1`): an operator CLI verb with a
dry-run default over existing per-source state, no wire, no schema, following directly from the hub
census this slice is a named follow-up to.

## BUZON-DEPLOY-REVIERTE-SOLO-1 -- `deploy` reverts code automatically on a failed health check (2026-11-19)

### Why

Guardrail of Carlos's standing deploy go (`ORCH-STANDING-DEPLOY-GO-HASTA-PRIMER-CLIENTE-1`):
`deploy/host/duna-deploy-buzon` already captured a `before` sha at the start of every `deploy`, but did
nothing with it -- a deploy whose final health check failed left the broken code running, requiring
Carlos (or an orchestrator) to notice and fix it by hand. `duna-pagos`' own entry script already reverts
on a failed health check; this slice ports that shape to a connector that is a timer job, not a server.

### What changed

**`deploy`'s own health check is the one pass this product already calls "health"** -- `python -m
duna_buzon estado`, run via `systemd-run` as `duna`, the same call `status` runs. Buzón has no port to
poll and no process to watch stay up, so this one pass IS the health check, unlike `duna-pagos`' own
HTTP poll. Factored this call out into a new shared `_chequear_salud` (used by `status`, `deploy`'s own
final check, and the new rollback's own re-check below), so all three agree on exactly what "healthy"
means without three copies of the same `systemd-run` invocation drifting apart.

**A new `revertir_despliegue`, called ONLY when that health check fails:** reverts the code to the
`before` sha (`runuser -u "$owner" -- git -C "$REPO" reset --hard "$antes"`), re-syncs the environment
(`runuser -u "$owner" -- env --chdir="$REPO" uv sync --frozen`), re-runs `_chequear_salud` once more, and
prints exactly one line -- `rolled back to <sha>: healthy` or `rolled back to <sha>: STILL UNHEALTHY` --
before exiting non-zero either way: a rollback that restores the pass is still a failed deploy, and
Carlos (or the orchestrator reading `deploy`'s own exit code) should not read a `0` out of this run.
`deploy --dry-run` prints this same revert plan as text (`_desc_revertir_despliegue`), alongside every
other planned step, without running any of it.

**The carried `before` sha is now REAL across autoactualizar's own re-exec**, fixing a latent bug this
slice's own work surfaced: `cmd_deploy` captured `before` via `git -C "$REPO" rev-parse HEAD` BEFORE the
pull, but on autoactualizar's re-exec (`exec "$ENTRY_INSTALADO" deploy --internal-reexec`) that same
rev-parse, run again in the fresh process, answered the commit the pull had ALREADY landed -- not the
real rollback point. This never mattered before, because nothing ever read `before` for anything; it
would have mattered the first time an entry-script change shipped in the same deploy as a bad commit.
Fixed the same way `duna-pagos` fixes it: `autoactualizar` now takes the captured `before` as its own
argument and exports it as `DUNA_AUTOACTUALIZAR_BEFORE_SHA` immediately before its `exec`; `cmd_deploy`
reads that env var first (falling back to the normal `rev-parse HEAD` when it is unset, i.e. on every
deploy that never re-execs) so the sha `revertir_despliegue` reverts to is always the one captured
before THIS run's own pull, never a pull two processes ago re-measured too late.

**This NEVER rolls back `/srv/duna/data/buzon/`** -- cursors, the token cache and `estado.json` are left
exactly as the failed run left them, confirmed by a dedicated test that walks every non-comment line of
`revertir_despliegue` and refuses any line that names `$DATOS_DIR` outside an `echo`. A bad PASS (a stale
cursor, a source that read garbage) is still `rebobinar-cursor`'s job (`BUZON-CURSOR-REBOBINAR-CLI-1`),
documented as such in both the entry script's own comment and `deploy/host/README.md`'s new section.

### Shape decisions this slice made, not otherwise specified

- **The revert is code-only, never a cursor/state rollback** -- the spec said this explicitly
  ("it never rolls back cursors or state files"), so this is not a shape decision so much as a
  confirmation that `revertir_despliegue` touches nothing under `$DATOS_DIR`, verified by the dedicated
  test above rather than left to review.
- **`_chequear_salud` is a new shared helper, replacing three near-identical `systemd-run` blocks**
  (`status`'s own check, `deploy`'s own final check, and the new rollback's re-check) with one. Not
  asked for by name in the spec, but the alternative -- three copies of the same invocation -- is exactly
  the kind of drift this slice's own guardrail exists to prevent elsewhere.
- **The carried-before-sha fix is in scope**, even though the spec's own prose only said "including the
  carried `--before-sha` on a re-exec" in passing: without it, the new rollback would have reverted to
  the WRONG commit on exactly the deploy where autoactualizar's own re-exec fires, silently undermining
  the guardrail this slice exists to add. Read as part of the build instruction, not a scope expansion.
- **Message format is plain English** (`rolled back to <sha>: healthy` / `... STILL UNHEALTHY`), matching
  the spec's own literal wording, rather than Spanish like most of this script's other operator-facing
  text -- the spec quoted these two lines verbatim, so they are reproduced verbatim.

### Tests, measured

8 new `def test_` functions (`git diff -- tests/test_host_deploy_entry_script.py | grep -c
'^+def test_'` over this slice's own diff against `main`): the subcommand/function pair is recognised;
`revertir_despliegue` is called from `cmd_deploy` ONLY inside the failed-health-check branch, and exactly
once in the whole function; `revertir_despliegue` itself reverts, re-syncs and re-checks in that order,
prints both possible one-line outcomes, and exits non-zero exactly once; `revertir_despliegue` never
touches `$DATOS_DIR` outside an `echo`; every `git` call inside it carries `runuser -u` on the same line
(the same invariant the rest of this script's own test suite already enforces); the before-sha is
exported before autoactualizar's own `exec`, read back before `cmd_deploy`'s own pull, and reaches
`autoactualizar` as its own argument; `deploy --dry-run`'s revert plan is present and every line in that
branch is still a bare `echo` (the same invariant `test_deploy_dry_run_no_ejecuta_nada_real` already
enforces for the rest of the branch); and `_desc_revertir_despliegue` itself names the reset, the sync,
both healthy/unhealthy outcomes, and says "never touches" rather than describing a deletion.

`uv run pytest -q` on the slice branch (`feat/buzon-deploy-revierte-solo-1`), tree
`9ca6d7c780298fd317a6c18420a7d2246cff8eec` (slice commit `16413010f24d1f76d64a788c88d5e4f156e177a1`):

    538 passed, 9 skipped in 427.63s (0:07:07)

The 9 skips are the same pre-existing Windows file-mode skips every earlier slice in this ledger has
recorded -- this slice added no new skip-gated test. 538 is exactly 530 (this slice's own base,
`main` at `8a2d16000bc7f3cf8d05f1a89bbce3117b190686`, the tip `BUZON-CURSOR-REBOBINAR-CLI-1-M` left) plus
this slice's own 8 new tests, all of which pass -- no drift between the two measurements.

### Merge-policy-A classification, made by this slice

No schema or migration -- this repository has no database, same as every earlier entry. No
customer-facing bytes -- every changed and new byte here is operator-facing: the entry script's own
stdout/stderr (read only by Carlos or an orchestrator running it through `sudo -n`) and
`deploy/host/README.md` (read only by the same operator) -- the same reasoning every earlier host-deploy
entry in this ledger recorded. No cross-repo contract -- nothing here touches the Finanzas wire, the
Graph client, or any other repository's contract; the `duna-pagos` entry script this slice reads from is
read-only reference, never changed. All three conditions clear, and the approval ("Merge on GREEN.")
authorizes merging without waiting -- merged on green, no pause.

### Attribution

Main, deciding under delegation (`ORCH-CARLOS-DELEGATES-TIER-1-1`): a host-deploy-tooling-only guardrail
ported from `duna-pagos`' own entry script, no wire, no schema, directly named by the approval.

---

## BUZON-REMITENTE-SUBDOMINIO-1 -- a `remitentes` domain entry also matches its subdomains (2026-11-23)

### Why

Carlos asked to verify every Duna money movement from mail (memory 2026-11-23, `ORCH-CARLOS-DELEGATES-
TIER-1-1`). The desk's census of his Hotmail found that vendor mail does not come from the bare domain in
`reglas.json` -- it comes from a subdomain of it (`mail.anthropic.com`, `notify.cloudflare.com`,
`tm.openai.com`). `_remitente_coincide` (`src/duna_buzon/reglas.py`) matched a domain entry only when it
equalled the sender's domain exactly, so every one of those invoices was missed, not merely mis-sorted.

### What changed

One line in `_remitente_coincide`: a domain entry (no `@`) now matches the sender's domain when it EQUALS
the entry OR ENDS WITH `.` + the entry -- a label-boundary suffix check, not a bare string suffix, so
`hetzner.com` matches `mail.hetzner.com` and `billing.notificaciones.hetzner.com` but never
`nothetzner.com` (no dot before `hetzner`) or `hetzner.com.evil.io` (the entry is a prefix, not a suffix,
of that domain). A full-address entry (`x@y.com`) is untouched -- still an exact match only; casefolding
and the existing leading-`@` tolerance on the entry are untouched. `coincide`'s own module docstring and
`README.md`'s description of `fuentes/<id>/reglas.json` were updated to state the subdomain rule; neither
previously said "exact", so there was no stale wording to correct beyond adding this.

### Tests, measured

7 new `def test_` functions (`git diff -- tests/test_reglas.py | grep -c '^+def test_'` over this slice's
own diff against `main`): an equal domain still matches; a one-level subdomain matches; a two-level
subdomain matches; the look-alike with no dot before the entry does not match; the look-alike where the
entry is a domain-shaped prefix of a longer domain does not match; a full address stays exact (a
different mailbox at the matching domain does NOT match merely because the domain matches); a leading
`@` on the entry is still accepted for a subdomain match; and the whole match is still case-insensitive.

`uv run pytest -q` on the slice branch (`feat/buzon-remitente-subdominio-1`), tree
`a42a4fef920f7409f8cccc7cf5e22bfeb0488c83` (slice commit `af86c0ce6eb3f84b371dcab9b3c5c180b8528ec9`):

    545 passed, 9 skipped in 39.18s

545 is exactly 538 (this slice's own base, `main` at `0061c28a318d7640cf9df0c7181c99c4388a25bc`, the tip
`BUZON-DEPLOY-REVIERTE-SOLO-1-M` left) plus this slice's own 7 new tests, all of which pass -- no drift
between the two measurements. The 9 skips are the same pre-existing Windows file-mode skips every earlier
slice in this ledger has recorded.

### Merge-policy-A classification, made by this slice

No schema or migration -- this repository has no database. No customer-facing bytes -- `remitentes`
widening is an operator-authored matching rule in `reglas.json` (never committed, never customer-facing),
and the only prose touched is this ledger, `README.md` and a module docstring, both read only by an
operator or an orchestrator, not a customer. No cross-repo contract -- nothing here touches the Finanzas
wire, the Graph client, or any other repository's contract; which messages get selected is purely local
matching logic. All three conditions clear, and the approval ("MERGE IS APPROVED on GREEN.") authorizes
merging without waiting -- merged on green, no pause.

### Attribution

Main, deciding under delegation (`ORCH-CARLOS-DELEGATES-TIER-1-1`): a selection-matching widening, no
wire, no schema, directly named by the approval.

---

## BUZON-REGLAS-ENTRADA-1 -- passwordless `reglas`/`env-set` subcommands (2026-11-23)

### Why

Carlos, typed in the desk 23 Nov 2026 and relayed: "it would be amazing that you can run all the
commands that you want on the server yourself." Setting a source's selection rules, and the one
non-secret env flag (`BUZON_ENVIAR_ORIGEN`), both still needed Carlos's own sudo password and a hand
edit as `duna` -- narrow, logged entry-script subcommands close that gap, the same shape
`ORCH-CARLOS-HOST-SELF-SERVICE-2026-11-23` put to him and he approved, never a general sudo grant.

### What changed

Four new passwordless subcommands of `deploy/host/duna-deploy-buzon`, every one validated by shape
before the EUID check, like every subcommand before it:

- `reglas-ver <fuente>` -- read-only, prints `fuentes/<fuente>/reglas.json` as-is (never a secret).
- `reglas <fuente> [--aplicar]` -- reads a candidate `reglas.json` from stdin, capped at 64 KiB
  (`LIMITE_REGLAS_BYTES`, measured in BYTES via `wc -c`, never bash's own character count, so a
  multi-byte accented word in `palabras` is never undercounted). Validates it with the PROGRAM's own
  loader, never a shell re-implementation: `reglas.py` gained `parsear_reglas(texto)`, the same
  validation `cargar_reglas` already ran on a file's content, now runnable on an in-memory string;
  `__main__.py` gained `validar-reglas`, the ONE command dispatched BEFORE `Config` is ever built, so
  it needs no environment variable and no secret at all -- the entry script runs it as `duna` via
  `runuser`, with neither env file sourced. Refuses whatever the loader refuses and prints its error;
  otherwise prints a unified diff (`diff -u`, process substitution, no temp file for the dry-run path)
  and stops there unless `--aplicar` is given. `--aplicar` backs up the old file to
  `reglas.json.<UTC stamp>.bak` (duna:duna mode 0600), writes the new one atomically (temp file in the
  same directory, then `mv`, duna:duna mode 0600), and logs one line via `logger -t duna-deploy-buzon`
  -- the source id, the old and new file's sha256, and the backup's name, never the rules' own content.
- `env-tiene <CLAVE>` -- works for ANY key shaped like an environment variable name (`CLAVE_ENV_RE`),
  including a secret's own name, and prints only `definida`/`no definida` against
  `/srv/duna/env/buzon.env` -- never the value.
- `env-set <CLAVE> <VALOR> [--aplicar]` -- writes that same file, but ONLY for a key listed in a new
  allowlist this repo ships, `deploy/host/env-permitidas.json` (seeded with exactly
  `BUZON_ENVIAR_ORIGEN` -> pattern `^(0|1)$`), installed by `autoactualizar` beside the entry script
  itself (root:root mode 0644) the same way it already installs the entry script and its sudoers
  fragment -- `autoactualizar` now diffs and installs THREE files, not two, and only reports "up to
  date" when all three match. A hard, in-script denylist on the key's own NAME
  (`TOKEN|SECRET|PASSWORD|CLAVE|_KEY|CIFRADO|CLIENT_ID|DSN|PRIVATE`) is checked FIRST, before the
  allowlist lookup, so a future allowlist entry that merely looks like a secret's name can never be
  used to write one through this path. Dry-run by default, prints the current and proposed value
  (safe: only an allowlisted, denylist-cleared key ever reaches that point); `--aplicar` backs up the
  env file, changes EXACTLY the one matching line (or appends it if the key was not set before)
  atomically, and logs one line -- never the value. No restart needed: the timer's next scheduled pass
  reads the file fresh.

Neither `reglas`/`reglas-ver` nor `env-tiene`/`env-set` ever reads `credencial.bin`, the cursor, or
`buzon-conector.env` (the token env file) -- only `reglas.json` and `buzon.env`, neither of which this
repository's own invariants treat as secret.

### Tests, measured

38 new `def test_` functions across `tests/test_reglas.py`, `tests/test_cli.py`, `tests/test_main.py`
and `tests/test_host_deploy_entry_script.py` (`git diff -- tests/ | grep -c '^+def test_'` over this
slice's own diff against `main`), several parametrized into more collected items: `parsear_reglas`'s
own validation (valid text, invalid JSON, a missing field, an invalid date, and that `cargar_reglas`
now delegates to it byte-for-byte); `comando_validar_reglas` (valid stdin, invalid JSON, a missing
field, prints nothing on success); `main(["validar-reglas"])` dispatching with every env variable
unset AND with `Config.desde_entorno` itself replaced by a function that raises if ever called --
proving the bypass is structural, not incidental; every new subcommand's argument-shape table (missing
args, a bad source id, a lowercase/space/semicolon-laden env key, a value with mixed arities, a literal
newline in an env value) refused with `usage`, and every shape-valid form failing ONLY on the EUID
check, never on usage; the pure helpers directly (`_excede_limite_bytes` on a multi-byte string,
`_clave_es_secreta` against every denylist token and a clean key, `_patron_permitido` against a real
and a missing key/file, `_valor_env_actual`/`_env_tiene_clave`, `_escribir_env_con_clave` replacing one
line and appending when absent, never touching a look-alike comment line); `cmd_reglas`/`cmd_env_set`
text-inspected for no `eval` and no `cat`; `autoactualizar`'s own text-inspected for the third
(`cambio_permitidas`) compare-and-install; the allowlist file's own existence and JSON shape; and both
READMEs documenting the new subcommands, including the exact example the spec named.

`uv run pytest -q` on the slice branch (`feat/buzon-reglas-entrada-1`), on the final tree (after the
`cat`-literal self-correction in a comment, caught by this repo's own
`test_los_archivos_de_entorno_nunca_se_leen_con_cat`):

    614 passed, 9 skipped in 76.64s

623 total (614 + 9) against this slice's own base (`main` at
`962cd1039cda46b73d9b30a1eaeb2a26e1dcf789`, 554 total: `BUZON-REMITENTE-SUBDOMINIO-1-M`'s own recorded
545 passed + 9 skipped) -- 623 - 554 = 69 new collected test items, consistent with 38 new function
defs once parametrization is counted. No drift: every test passes, nothing pre-existing broke.

### Merge-policy-A classification, made by this slice

No schema or migration -- this repository has no database. No customer-facing bytes -- every new byte
is operator/orchestrator-facing: a root-owned host script only Main, the desk or Carlos himself ever
invoke over SSH, its own README, and this ledger; no customer of Duna's products ever sees any of it.
No cross-repo contract -- nothing here touches the Finanzas wire, the Graph client, or any other
repository's contract; `validar-reglas` and the four new entry-script subcommands are purely local to
this connector's own data (`reglas.json`) and its own non-secret env flag. All three conditions clear,
and the approval ("MERGE IS APPROVED on GREEN.") authorizes merging without waiting.

### Attribution

Main, deciding under delegation (`ORCH-CARLOS-HOST-SELF-SERVICE-2026-11-23`, `ORCH-CARLOS-DELEGATES-
TIER-1-1` for the shape): narrow, logged host-tooling subcommands directly named by the approval, never
a general sudo grant.

---

## BUZON-ALERTA-AUTH-1 -- a dead credential becomes visible (2026-11-23)

### Why

Carlos asked through the desk 23 Nov 2026, his own words: "All this keys to fully access the emails, does
it expire? How to do it for normal users later?" The desk asked for an alert when a source stays in auth
error for more than one day. Before this slice, a dead credential (an app password revoked, an OAuth
refresh token expired or revoked) only printed `parada=auth error=...` (or `parada=graph listar
http=401`) to the journal on every pass, forever -- nobody was told (`ORCH-CARLOS-DELEGATES-TIER-1-1`,
"Anything off the normal order flow raises a flag").

### What changed

A new per-source marker, `fuentes/<id>/alerta.json` (`src/duna_buzon/alerta.py`): `auth_fallando_desde`,
a UTC timestamp, set on the FIRST auth failure of an unbroken run and kept across passes until one
authenticates again. `cli.py`'s own `_ejecutar_una_fuente` decides when a pass counts: the early
`SinCuentaError`/`SinCredencialError`/`ClaveInvalidaError` family (no provider built yet) and
`ResultadoPasada.detenida_por_autenticacion` (`ProveedorAutenticacionError`, or Finanzas answering
`unauthorized`) both SET it; a clean pass, a rules error (a token WAS obtained that pass), or a permanent
Finanzas rejection all CLEAR it (auth plainly worked); a transient failure
(`detenida_por_error_transitorio`) touches neither, per the build instructions. No second on-disk store
was added beyond this one small file -- `estado.json`, `historial.jsonl` and `cursor.json` stayed exactly
as they were, since none of them had anywhere to carry a value that must survive a pass which never even
reaches the point where they are written.

A new config setting, `BUZON_ALERTA_AUTH_HORAS` (`config.py`, default 24, must be a positive integer,
refused by name like every other invalid setting) -- the threshold is DATA, never a literal in `cli.py`.
`estado` now prints, per source past that threshold: `fuente=<id> ALERTA credencial caida desde <ts> (<n>
h) -- reconectar: <how>`, `<how>` being `entrar --fuente <id>` for a `local`-origen microsoft source, a
new app password followed by that same `entrar` for `local`-origen imap, or reconnecting in Cuenta Duna
for an `origen="cuenta"` source (`cli.py`'s own `_remedio_credencial`) -- never a token, password or
address beyond the source's own id. `sincronizar`'s own stderr journal line for a pass that stops on auth
now also carries `alerta=si` once past the threshold, computed from the SAME shared helper
(`cli._estado_alerta`) `estado` and the new `alertas` subcommand use, so all three agree on exactly what
counts as in alerta. `alertas [--json]` (`__main__.py`, `cli.comando_alertas`) prints a JSON list, `[]`
when none, exit 0 either way -- `[{"fuente", "tipo": "auth", "desde", "horas", "remedio"}, ...]`.
`deploy/host/duna-deploy-buzon` gained a matching `alertas` subcommand, read-only, running `python -m
duna_buzon alertas --json` as `duna` via the same `systemd-run --pipe --quiet --wait` shape `status` uses
for its own health check -- the installed sudoers rule is a bare script path with no argument
restriction, so this needed NO sudoers change, confirmed unchanged below.

Both READMEs (`README.md`, `deploy/host/README.md`) document what the alert means, the per-kind remedy,
the new `alerta.json` file and `BUZON_ALERTA_AUTH_HORAS` setting, and that Main relays an `alertas --json`
result to Carlos's own desk at session start.

### Not built, named instead

A console line surfacing the same alert in the admin UI needs a Buzón-to-console read that does not
exist today -- the smallest wire for that is a follow-up (`BUZON-ALERTA-CONSOLE-LINE-1`, open below), not
built by this slice: the spec's own build instructions asked only to coin it in this report, not build it.

### Tests, measured

38 new `def test_` functions (`git diff main -- tests/ | grep -c '^+def test_'` over this slice's own
diff against `main`), several parametrized into more collected items: `alerta.py`'s own
set/keep-first/clear/no-op-clear behaviour in isolation (`tests/test_alerta.py`); `BUZON_ALERTA_AUTH_HORAS`
parsing (default, a valid positive integer, zero/negative/non-numeric/empty) in `tests/test_config.py`;
in `tests/test_cli.py` -- an early auth exception marks the alert and a second one keeps the FIRST
moment; a mid-pass `detenida_por_autenticacion` marks it; a transient stop touches neither a fresh nor an
existing marker; a rules error, a clean pass and a permanent rejection each clear a pre-existing marker
(auth worked that pass); `_remedio_credencial` for each of the three source kinds; `estado` under,
at, and over the threshold, and with a configured non-default threshold; neither `estado` nor `alertas`
ever print a planted `FINANZAS_BUZON_TOKEN` value; `alertas --json`'s exact shape, its empty case with no
sources and with sources none of which are in alerta, and that a broken `fuente.json` is silently
skipped rather than failing the command; the sincronizar journal line carrying `alerta=si` only for an
auth stop that has passed the threshold, never for one that has not, and never for a transient stop even
when an old, already-past-threshold marker sits on disk. In `tests/test_main.py` -- `alertas` dispatches
with the env-built `Config`, accepts an optional `--json`, and refuses any other argument. In
`tests/test_host_deploy_entry_script.py` -- `alertas` is a recognized subcommand, refuses an extra
argument, passes its own shape check and fails only on EUID, runs read-only via `systemd-run` with no
`logger`/`mv`/`install` call in its own body, and the sudoers fragment still holds exactly its one
pre-existing rule.

`.venv/Scripts/python.exe -m pytest --junit-xml=.gate-out/gate.xml -q` on the slice branch
(`feat/buzon-alerta-auth-1`), on the final tree (commit `4f60073`, after removing one stray leftover
assertion caught by the gate's own first run):

    656 passed, 9 skipped in 146.03s

665 total (656 + 9) against this slice's own base (`main` at
`350b0fd24df6426aefe95a7be54125c5d2949a8e`, 623 total: `BUZON-REGLAS-ENTRADA-1-M`'s own recorded 614
passed + 9 skipped) -- 42 new collected items, more than the 38 new function defs once this slice's own
parametrized cases (`BUZON_ALERTA_AUTH_HORAS` validation, `alertas`'s argument-shape table) are counted.
No drift: the gate's FIRST run on this tree caught one stray leftover assertion this slice's own authoring
left in a new test (`test_parada_no_auth_nunca_lleva_alerta_si_aunque_haya_alerta_vieja`); fixed and
re-run GREEN before this entry, both runs on the gate's own JUnit XML, never inferred from an exit code.
The 9 skips are the same pre-existing Windows file-mode skips every earlier slice in this ledger has
recorded.

### Merge-policy-A classification, made by this slice

No schema or migration -- this repository has no database. No cross-repo contract -- nothing here
touches the Finanzas wire, the Graph client, or any other repository's contract; `alerta.json` is this
connector's own new on-disk file, never sent anywhere. Customer-facing bytes DID change: the new ALERTA
line in `estado`, the `alerta=si` journal suffix, and the new `alertas --json` output are all bytes an
OPERATOR reads (Carlos over SSH, Main and the desk over `sudo -n`) -- never a customer of a Duna product,
but the dispatch's own definition of this condition is broader than that ("a byte a customer, operator or
owner reads"), so this slice records the condition as tripped rather than narrowing it the way two earlier
entries in this ledger did. The approval's own words -- "MERGE IS APPROVED on GREEN." -- are an explicit,
in-words authorization to merge past a tripped condition once the gate is green, so this slice merges
without waiting, same as the two before it, but on the broader reading of this one condition rather than
a claim that it did not trip.

### Attribution

Main, deciding under delegation (`ORCH-CARLOS-DELEGATES-TIER-1-1`): a visibility feature over existing
failure signals, no wire, no schema, directly named by the approval.

## BUZON-ESTADO-A-CUENTA-1 -- a status heartbeat per pass to Cuenta Duna (2026-11-25)

### Why

Carlos, through Main: «Why? I thought we had connected my hotmail, my gmail and the @duna.solutions
mail» (`ORCH-BUZON-FUENTES-EN-CUENTA-SHAPE-2026-11-25`). A mailbox this connector reads by itself (its
own `local`-origen credential, or one pulled through Cuenta Duna) is invisible inside Cuenta -- Cuenta
only ever listed the sources IT connected. Cuenta should show every mailbox that reaches the person's
books, with where it is connected, so Main decided the shape in
`../dev-protocol/contracts/buzon-estado-a-cuenta-v1.md` and split it in two: Cuenta's own receiver
(`CUENTA-API-BUZON-ESTADO-RECEPTOR-1`, not yet built) and this slice, Buzón's own sender, built now
against that contract and switched off until the receiver is live.

### What changed

`cuenta.py`'s `CuentaClient` gained `enviar_estado(fuentes, *, enviado_en)` -- `POST
/servicio/buzon/estado/{sub}`, the same HTTP Basic `buzon` client credential `listar_fuentes` already
uses, restating the contract's own wire verbatim: a body of exactly `{"fuentes": [...], "enviado_en":
"<UTC>"}`, each row exactly `{"fuente_id", "proveedor", "direccion_enmascarada", "etiqueta", "origen",
"ultimo_paso_en", "vistos", "reenviados", "alerta"}` -- never a subject, a sender, a token, or the real
address. `enmascarar_direccion` (public, directly tested) builds `"c***@hotmail.com"` from
`"carlos@hotmail.com"`, the contract's own example; it is the ONLY place an address is masked, so no
caller can forget to. A non-204 or a transport failure raises the SAME `CuentaPullError` `listar_fuentes`
already raises -- widened with a `paso` field (default `"fuentes"`, so every existing call and test keeps
its exact `motivo` string unchanged) so this new call's own failures read `cuenta estado http=...` /
`cuenta estado error=...`, distinct from the pull's `cuenta fuentes ...`.

`cli.py`'s `comando_sincronizar` calls this ONCE, at the very end of every pass (after the per-source
loop, whatever its own outcome), through a new `_enviar_estado_a_cuenta`: a no-op unless BOTH
`Config.enviar_estado_cuenta` (the new switch) and `Config.cuenta_configurada` hold. This connector
serves exactly one person today, so that one configured `sub` is the "owner sub" for every source it
has, local or Cuenta-sourced alike -- the contract's "a fuente with no sub is not sent" read at the whole
deployment's level: no Cuenta Duna configured at all means no sub to push under, switch or no switch.
`_estado_wire_para` builds each source's own row from `modulo_fuentes.listar_fuentes` (the FULL set, active
and inactive, local and `origen="cuenta"` alike) and that source's own `historial.jsonl` (its LATEST
recorded pass -- this run's own fresh `vistos`/`reenviados` for a source that just ran, since
`_agregar_historial` already wrote them earlier in this same pass; the previous run's for one that did
not run this time, never a fabricated 0/0, which would read as "checked, found nothing" rather than "did
not run"). A source with no `historial.jsonl` at all (never completed a single pass) is left out of THIS
heartbeat, not sent with made-up numbers -- it joins the next one the moment its first pass finishes.
`origen` maps `local` -> `"buzon"`, `cuenta` -> `"cuenta"`, per the contract's own two values.
`alerta="reingreso_requerido"` for exactly the two reconnect-needed cases this connector already tracks --
an `origen="cuenta"` source Cuenta Duna no longer returns (`activa=False`), or a `local` source past its
own `BUZON_ALERTA_AUTH_HORAS` threshold (the SAME `cli._estado_alerta` helper `estado`/`alertas` already
share) -- `None` otherwise; the contract's own `"error"` value is never sent, since this connector has no
other failure it can attribute to one source today. A failed push is caught here exactly like
`_pull_fuentes_cuenta`'s own failure path: one `cuenta: <motivo>` line on stderr, the pass's own exit code
untouched.

A new config setting, `BUZON_ENVIAR_ESTADO_CUENTA` (`config.py`), off unless set to exactly `1` or
`true`, same shape as `BUZON_ENVIAR_ORIGEN` -- off for every deployment before this slice, and meant to
stay off until `CUENTA-API-BUZON-ESTADO-RECEPTOR-1` deploys. `README.md` documents the setting, the
route, every field, the masking, the two `origen`/`alerta` values this connector actually sends, and that
a never-run source is left out until its first pass.

### Tests, measured

19 new `def test_` functions (`git diff main -- tests/ | grep -c '^+def test_'`), two of them
parametrized into 7 more collected items (`BUZON_ENVIAR_ESTADO_CUENTA`'s on/off value table, mirroring
`BUZON_ENVIAR_ORIGEN`'s own in `tests/test_config.py`). In `tests/test_cuenta.py` -- `enmascarar_direccion`
against the contract's own example and an address with no `@`; `enviar_estado`'s route and `sub`, HTTP
Basic auth, a 204 raising nothing, a 404 and a transport error each raising `CuentaPullError` with
`paso="estado"` and the matching `motivo`; the request body carries EXACTLY the nine contract fields and
nothing else, and never the unmasked address even in the raw bytes on the wire; `listar_fuentes`'s own
existing `motivo` strings are unchanged (`paso` defaults to `"fuentes"`). In `tests/test_cli.py` -- the
switch off means `enviar_estado` is never called even though Cuenta is otherwise fully configured (the
existing pull still runs); switched on, a clean local pass's heartbeat carries that source's own id,
provider, unmasked-until-the-client address, label, `origen="buzon"`, this pass's `vistos`/`reenviados`,
and no alert; a Cuenta-pulled source's own row carries `origen="cuenta"`; a revoked Cuenta source still
appears (from its last pass's own historial) with `alerta="reingreso_requerido"` while an unaffected local
source in the same heartbeat carries no alert; an inactive, never-run source is left out of the heartbeat
entirely, with no `historial.jsonl` written for it at all; `enviar_estado` raising leaves the pass GREEN,
with the one `cuenta: cuenta estado http=500` line on stderr.

`.venv/Scripts/python.exe -m pytest --junit-xml=.gate-out/gate.xml -q` on the slice branch
(`feat/buzon-estado-a-cuenta-1`), on the final tree (commit `756f0709623a700b16009f76558a70587de51047`):

    682 passed, 9 skipped in 917.56s

691 total (682 + 9) against this slice's own base (`main` at `abdc84b82c215ea4891f33eb9219421fa71312d3`,
665 total: `BUZON-ALERTA-AUTH-1`'s own recorded 656 passed + 9 skipped) -- 26 new collected items, matching
the 19 new function defs plus the 7 extra parametrized cases counted above. No drift: this run's own
first attempt was moved to the harness's background past its foreground cap; a stale `.gate-out/gate.xml`
already sitting on disk (from an unrelated earlier local run, mtime 2026-11-23) was never read as this
run's result -- this entry's figures come only from the file once its own mtime advanced past that
leftover's, confirming it as THIS run's write. The 9 skips are the same pre-existing Windows file-mode
skips every earlier slice in this ledger has recorded.

### Merge-policy-A classification, made by this slice

No schema or migration -- this repository has no database. No cross-repo contract change: this slice only
CONSUMES Cuenta Duna's own, already-decided `buzon-estado-a-cuenta-v1.md` (Main's own, written before
this slice started, in `../dev-protocol`) -- it defines or changes no contract any other repository
reads, the file itself is untouched by this diff, and the Finanzas buzon-v1 wire is untouched too, same
reasoning `BUZON-FUENTES-PULL-1` recorded for its own, symmetric pull. Customer-facing bytes DID change,
on the broader reading `BUZON-ALERTA-AUTH-1` already adopted in this same ledger: the new `cuenta: cuenta
estado ...` stderr line and the new README section are bytes an OPERATOR reads (Carlos, Main, the desk) --
never a customer of a Duna product, and the feature itself stays off by default, but this slice records
the condition as tripped rather than narrowing it. The approval's own words -- "MERGE IS APPROVED on
GREEN. Main deploys (the switch stays off)." -- are an explicit, in-words authorization to merge past a
tripped condition once the gate is green, so this slice merges without waiting, same as `BUZON-ALERTA-
AUTH-1` before it.

### Attribution

Main, deciding under delegation (`ORCH-CARLOS-DELEGATES-TIER-1-1`): the sender half of a two-repository
contract it already wrote, additive and switched off, directly named by the approval.

---

## BUZON-IMAP-MENSAJE-AISLADO-1 -- one broken folder or one unparseable message no longer wedges an IMAP source to zero forwards (2026-11-26)

### Why, and what this slice chose over the repository's own named follow-up

Overnight direction from Carlos (via the desk, 25 Nov 2026 ~22:45): "keep everything moving... don't let
anything go cold," pick the most valuable OPEN follow-up this repository's own ledger names, preferring a
real defect -- "a source that silently sees nothing" was named explicitly -- over hygiene.

This repository's `DECISIONS.md` names exactly ONE still-open follow-up with an id:
`BUZON-ALERTA-CONSOLE-LINE-1` (coined in `BUZON-ALERTA-AUTH-1`, above). It was NOT built here: it needs a
Buzón-to-console wire that does not exist today, in a repository (the admin console) outside this one and
outside `touches:` -- a cross-repo contract and a new outward-facing surface, exactly what this slice's own
approval ruled out ("NOT allowed: ... a new outward-facing behaviour"). Named as the one candidate this
slice considered and set aside, per the spec's own instruction to name such a case and move to the next.

With that follow-up disqualified, this slice read the code behind the spec's own weighted data point
(duna-zoho having seen 0 mail since 24 Nov 19:30 while its folders list fine) rather than inventing
unrelated work: `cliente_imap.py`'s `listar_mensajes` (`src/duna_buzon/cliente_imap.py`, read before this
slice changed it) is a GENERATOR that iterates every included folder and, within each, every matching UID.
Before this slice, EITHER `self._examinar_carpeta` (one folder's own `EXAMINE`) or the per-message
`_fetch_uno`/`_parsear_internaldate`/`_parsear_cabeceras`/`_particionar_bodystructure`
(`_parsear_bodystructure`) raising -- a clean server `NO`/`BAD` for one specific folder or message, or this
module's own hand-written IMAP grammar failing to walk one message's malformed `BODYSTRUCTURE`/headers/
`INTERNALDATE` -- propagated straight out of the generator, UNCAUGHT, at the exact point it was reached.
`sincronizar.py`'s own `for mensaje in proveedor.listar_mensajes(...)` has a SINGLE try/except around the
whole loop, turning that into a whole-pass stop (`detenida_por_error_transitorio`); invariant 5 then
forbids persisting the cursor past it. The next pass resumes from the SAME cursor, re-lists the SAME
folder or message FIRST (sorted order), hits the SAME wall, and stops at the SAME point again -- meaning
every folder or message that would have come AFTER the broken one, in EVERY future pass, never even gets
attempted. `carpetas <id>` (the operator's own diagnostic) never goes through `EXAMINE` at all (only
`LIST`), so a source in exactly this state shows its folders listing fine while forwarding nothing, forever
-- precisely the shape of the data point this slice was asked to weigh. This was found by reading the code
against that symptom, not reproduced against a live mailbox (never attempted, never permitted by this
slice's own approval).

### The fix, and the design call this slice made

Reused a principle this repository already applies one layer up, in its own words
(`cliente_imap.py`'s `_listar_carpetas_crudo`): "one odd entry from a server must never stop every other
[one] from being read." `listar_mensajes` now wraps each folder's own `EXAMINE`+`SEARCH` pair, and each
message's own FETCH+parse, in its own `try`/`except`: a failure skips exactly that ONE folder or ONE
message, counts it (`self.omitidos_por_error_proveedor`, a new public attribute, read by `cli.py` the same
`getattr(proveedor, "...", 0)` way `GraphClient.reintentos_usados`/`top_lista_usado` already are), and
CONTINUES -- every other folder, and every other message, still gets processed and forwarded in the SAME
pass.

The caught tuple (`_ERRORES_DE_CONTENIDO_AISLABLES`) is deliberately narrow:
`ClienteImapErrorTransitorio` (this module's own clean "the server said NO/BAD to THIS command" or
"this field could not be parsed" signal), `ValueError`/`IndexError`/`LookupError`/`UnicodeError` (exactly
the exception shapes `_parsear_valor`'s own hand-written grammar and `_particionar_bodystructure`'s
positional field access can raise on a malformed structure -- the same `IndexError` `_parsear_linea_list`
already catches for one `LIST` line), and `email.errors.MessageError` (the stdlib `email` module's own
parse failure on a malformed header). Deliberately EXCLUDED: `OSError` and any other bare `Exception` --
a genuine transport failure (a broken socket, `imaplib.IMAP4.abort`) still propagates UNCAUGHT and stops
the whole pass exactly as before this slice, because a connection problem is not specific to one folder or
message and treating it as a permanent, skippable one would be wrong. `tests/test_cliente_imap.py::
test_conexion_rota_durante_fetch_sigue_deteniendo_toda_la_pasada` proves this split directly, against a
hand-rolled connection whose FETCH raises `OSError`.

A folder-level skip leaves that folder's own stored cursor entry UNTOUCHED (retried, not lost, the next
time the folder's own problem clears). A message-level skip does NOT force the per-folder cursor past that
UID either -- deliberately: the resume search is always `"UID", f"{n}:*"` (to infinity, never a single
next UID), so a genuinely newer message arriving after the broken one is found and forwarded on every
pass regardless of whether the broken UID's own entry ever moves; the broken UID is simply retried, and
re-skipped, harmlessly, on every future pass. This keeps invariant 5 exactly as strict as before: nothing
here lets the cursor advance past a message that was never actually stored or examined successfully --
only past messages that WERE.

### Visibility, with the smallest wire possible

`omitidos_por_error_proveedor` is surfaced in `estado`'s own line and `registro.linea_resumen`'s summary
(`registro.py`), but ONLY when nonzero -- unlike `reintentos`, which is informative even at 0, a skip count
is the common, uninteresting case for every existing source (nothing in this repository's history has ever
triggered it), so printing it unconditionally would have forced an edit to every existing exact-match test
asserting a summary line, for a field that is 0 everywhere today. Folded into `historial.jsonl`'s own
`omitidos` total (`cli.py`'s `_agregar_historial`, alongside `omitidos_por_reglas`/`omitidos_por_413_422`)
rather than given its own historial field, matching that file's own stated design ("a trend view, not the
full breakdown"). No new on-disk file, no wire field, no change to the Finanzas or Cuenta Duna contracts --
this is purely a local counter on `ClienteImap`, read exactly the way two existing Graph-only counters
already are.

### Tests, measured

5 new `def test_` in `tests/test_cliente_imap.py`: a message whose `INTERNALDATE` names an unparseable
month is skipped while a newer, good message in the same folder is still forwarded
(`test_mensaje_con_internaldate_invalida_se_omite_y_no_bloquea_el_resto`); a message the fake server
answers `NO` to on FETCH is skipped the same way
(`test_fallo_de_fetch_en_un_mensaje_no_bloquea_los_siguientes`); a message with an unwalkable
`BODYSTRUCTURE` (`IndexError` on an empty parts list) is skipped the same way
(`test_bodystructure_ilegible_se_omite_y_no_bloquea_el_resto`); a folder the fake server always answers
`NO` to on `EXAMINE` is skipped while a healthy sibling folder is still read
(`test_examine_fallido_en_una_carpeta_no_bloquea_las_demas`); and the negative case above proving a
transport-level `OSError` still propagates and stops the whole pass
(`test_conexion_rota_durante_fetch_sigue_deteniendo_toda_la_pasada`). The fake server
(`_ServidorImapFalso`) gained two fault-injection knobs for this (`carpetas_examine_falla`,
`uids_fetch_falla`), both defaulting to empty so every pre-existing test is unaffected.

New `tests/test_registro.py` (3 tests): `omitidos_por_error_proveedor` is absent from the line when 0
(byte-identical to every line this function has ever produced), present and exactly placed when nonzero,
and coexists correctly alongside `reintentos`/`top`.

2 new `def test_` in `tests/test_cli.py`: a provider exposing a nonzero `omitidos_por_error_proveedor`
gets it printed in `estado`'s own line and folded into `historial.jsonl`'s `omitidos` total
(`test_sincronizar_registra_omitidos_por_error_proveedor_en_estado_y_en_historial`); a provider with no
such attribute (every existing Microsoft/Gmail fake, including `_ProveedorFalso`) never gets a guessed
value printed (`test_sincronizar_sin_omitidos_de_proveedor_no_lo_imprime`).

### Gate, measured

`.venv/Scripts/python.exe -m pytest --junit-xml=.gate-out/gate.xml -q` on this slice's own final tree
(slice commit `e04380a`, tree `d740da75dd65c08148b3c1b1214ad17e14abe8f7`), run in the background past the
600s foreground cap and read back from the artifact rather than inferred from an exit code:

    692 passed, 9 skipped in 657.68s

(`gate.xml`'s own `testsuite` attributes: `tests="701" failures="0" errors="0" skipped="9"` -- 701 - 9 =
692 passed.) Reconciled
against this slice's own base (`main` at `e6e4906029350ecdf8d0dcfd4788dc879b8155a5`, 691 total:
`BUZON-ESTADO-A-CUENTA-1`'s own recorded 682 passed + 9 skipped): 701 - 691 = 10 new collected items,
matching the 7 new `def test_` lines `git diff main -- tests/` shows against tracked files PLUS the 3 more
in the new, untracked-until-this-commit `tests/test_registro.py` (git diff against an untracked file shows
nothing, so that file's own 3 defs are counted by hand) -- no drift, no parametrization multiplier needed
since none of these new tests are parametrized. The 9 skips are the same pre-existing Windows file-mode
skips every earlier slice in this ledger has recorded; none are new.

Tree identity proven before writing this entry, so the gate above was not re-run for the merge: merge
commit `7bfe082` has tree `d740da75dd65c08148b3c1b1214ad17e14abe8f7`, IDENTICAL to the slice branch's own
tree at commit `e04380a`, and `git diff HEAD feat/buzon-siguiente-seguimiento-2026-11-26 --stat` against
the merge commit was empty.

### Merge-policy-A classification, made by this slice

No schema or migration -- this repository has no database, and no on-disk file layout changed (the new
counter is an in-memory attribute on `ClienteImap`, never persisted on its own). No cross-repo contract or
DTO change -- nothing here touches the Finanzas or Cuenta Duna wire; `omitidos_por_error_proveedor` is a
local counter, read by this connector's own `cli.py`, never sent anywhere. Judged NO customer-facing bytes,
same reasoning `BUZON-CONECTOR-1` through `BUZON-FUENTE-WIRE-CORREO-1` already recorded (the narrower
reading, not the broader one `BUZON-ALERTA-AUTH-1`/`BUZON-ESTADO-A-CUENTA-1` adopted for a NEW alert
surface): the one new conditionally-printed field in `estado`'s own line is the same category of operator
diagnostic as every count already there, not a new kind of surface, and this repository's only reader
remains Carlos acting as his own system's administrator. All three conditions clear, so this slice merges
on green without stopping for approval.

### Attribution

Carlos's own overnight direction (via the desk, `ORCH-CARLOS-DELEGATES-TIER-1-1`) asked for the most
valuable open follow-up, weighted toward a real defect over hygiene, and named the one live data point
(duna-zoho silent since 24 Nov 19:30) to weigh it against. This slice read `BUZON-ALERTA-CONSOLE-LINE-1`
(the only named, open follow-up) and set it aside as needing a cross-repo wire outside this slice's
approval, then found and fixed the defect behind that data point by reading `cliente_imap.py` and
`sincronizar.py` against it: the per-folder and per-message isolation in `listar_mensajes`, the
`omitidos_por_error_proveedor` counter and its wiring through `registro.py`/`cli.py`, and the tests above.

### Open follow-ups, ranked

1. **`BUZON-ALERTA-CONSOLE-LINE-1`** (named by `BUZON-ALERTA-AUTH-1`, still open): a console line
   surfacing a source's own dead-credential alert in the admin UI, needing a Buzón-to-console wire that
   does not exist yet -- cross-repo, outside this repository's own `touches:` and this slice's approval.
   Highest-ranked because it is the only follow-up this ledger already names with an id and a clear owner
   (the console/admin-ui repository) still to build the receiving half.
2. **A genuine IMAP transport failure (`OSError`/`imaplib.IMAP4.abort`) is still uncaught and untranslated**
   at every call site in `cliente_imap.py` -- it propagates as a raw Python exception rather than a clean
   `ClienteImapErrorTransitorio`, risking an uncontrolled traceback in the journal (invariant 2 names
   logs as counts-only) instead of the safe, class-name-only `motivo` string every other failure path in
   this connector already produces. `_conexion_activa` already translates `OSError` for `conectar`/
   `login`; `_examinar_carpeta`/`_buscar_uids`/`_fetch_uno` do not. Not built here: this slice deliberately
   kept the one new catch narrow (content/protocol-level only) so a genuine connection break keeps
   stopping the whole pass, per invariant 5 -- but the exception TYPE it propagates as should still be
   this module's own, not imaplib's raw one. Reversible, small, no behavior change beyond the exception's
   own class.
3. **FIN-API-BUZON-ORIGEN-OBS-1 is resolved** (`BUZON-ORIGEN-WIRE-1` built the `origen` field) and every
   other follow-up this ledger ever named before this entry is closed by a later slice -- re-verified
   against the code while compiling this list, not merely against the ledger's own prose.

## BUZON-IMAP-FECHA-DROP-Y-SILENCIO-1 -- the UID watermark over the date, carpetas casefold, a silence alert (2026-11-26)

### Why

`BUZON-ZOHO-CURSOR-QUIETO-INV-1` (an OBSERVED investigation run on `edfb9dc`, writing nothing of its own)
found a code path that explains duna-zoho's 35 h of `vistos=0` with no error anywhere: a message whose
`INTERNALDATE` stays old after a server-side filing is found by the per-folder UID search, then dropped,
uncounted, by the global date filter (`cliente_imap.py:783-788`, a bare `continue`), wedging the folder's
own UID watermark and the connector's single global cursor together. Main's own host check
(`carpetas duna-zoho`) showed every one of that source's folders (`INBOX`, `Archive`, `Newsletter`,
`Notification`, `Snoozed`, `Templates`) already read (`leida=si`), ruling OUT the folder-name casefold bug
as THIS source's own cause -- but that casefold gap was still a real defect in its own right, and the
investigation named three follow-ups. Carlos, through Main (`ORCH-CARLOS-DELEGATES-TIER-1-1`), asked for
all three built as one slice: the fixes a silent loss of mail, and adds the alert his standing default
calls for ("anything off the normal flow raises a flag"). Forwards only mail the source's own rules
already allow -- no rule, no `reglas.json`, no wire shape changed.

### Fix 1: the UID watermark, never the date, is the truth about "new in this folder"

Before this slice, `listar_mensajes` fetched every candidate UID (from either the UID-resume search or the
date-bounded `SINCE` fallback), parsed its `INTERNALDATE`, and dropped it with a bare `continue` whenever
that date was earlier than the pass's own date cursor (`desde`) -- correct reasoning for the `SINCE`
fallback (day granularity can return a message from the right day but before the exact timestamp), wrong
for a genuine UID resume: every UID a resume search returns is, by construction, already strictly newer
than the last one this folder's own watermark remembers, so its own date is irrelevant to whether it is
new. A server-side filing rule (Zoho moving a message between folders, for instance) can leave
`INTERNALDATE` at the message's ORIGINAL received time, long before `desde`, even though its UID in the
folder it landed in is brand new -- exactly the shape the investigation named. That drop happened before
`mapa_cursor` was ever touched for that UID, so the folder's own watermark never advanced past it either:
the identical drop repeated, forever, on every future pass, with nothing printed or logged (invariant 2
already forbids logging the message itself, and nothing here ever classified the drop as a skip worth
counting at all).

`listar_mensajes` now computes, once per folder, `usa_resumen_por_uid = cursor_previo is not None and
cursor_previo[0] == uidvalidity` -- exactly the same condition `_buscar_uids` already uses internally to
choose between the two search shapes, so the two never disagree. The date comparison applies ONLY when
`usa_resumen_por_uid` is `False`; a genuine UID resume never drops a message on its date, full stop. Where
the fallback DOES skip a message on date, the skip is counted (`ClienteImap.omitidos_por_fecha_proveedor`,
a new public attribute, read by `cli.py` exactly the way `omitidos_por_error_proveedor` already is) and the
folder's own watermark (`mapa_cursor[nombre_carpeta]`) still advances past it, exactly like a forwarded
message would -- never a bare, silent `continue`. Surfaced in `registro.linea_resumen` and `estado`'s own
line only when nonzero (same convention as `omitidos_por_error_proveedor`), and folded into
`historial.jsonl`'s `omitidos` total (`cli.py`'s `_agregar_historial`).

### Fix 2: `_carpeta_en_lista` casefolds both sides (`BUZON-IMAP-CARPETAS-CASEFOLD-1`)

`_carpeta_excluida` already casefolds every flag and name comparison it makes; `_carpeta_en_lista` (the
`carpetas` allow-list match `BUZON-IMAP-CARPETAS-INCLUIDAS-1` added) did not, so an operator-entered
`carpetas` entry like `"legal"` would never match a server folder actually named `"Legal"`. Now both the
exact-match and the delimiter-boundary child match casefold both sides before comparing -- the exclusion
rules (which always run first, and already casefolded) are unaffected, so this can only ever let a
`carpetas` entry match a folder name it was clearly meant to, never pull in a folder the exclusion rules
already dropped.

### Fix 3: a durable "last pass with vistos>0" marker raises and clears a silence alert

The auth alert (`BUZON-ALERTA-AUTH-1`) only ever catches a credential that stops working outright. A
source can run cleanly, pass after pass, authenticating fine and genuinely finding nothing -- `vistos=0`,
no error anywhere -- for a reason that is never an auth failure: Fix 1's own bug (before this slice), a
mailbox-side rule moving everything into an unread folder, or a `reglas.json` that now excludes everything.
`historial.jsonl` cannot answer "has this source gone quiet" on its own: it is bounded to
`historial.CAPACIDAD` (20) entries, which for a source syncing every 10 minutes holds only about 3h20 of
history -- far too short to notice a mailbox quiet for most of a day.

`alerta.py` gains a second, separate marker alongside `auth_fallando_desde`: `fuentes/<id>/visto.json`
holding `ultimo_visto_en`, the UTC instant of the MOST RECENT pass that saw at least one message. Unlike
the auth marker (set once per failing run, cleared only by a clean pass), this one is unconditional --
`cli.py`'s `_ejecutar_una_fuente` calls `marcar_ultimo_visto` every time `resultado.contadores.vistos > 0`,
regardless of what that pass went on to do with those messages (forward, skip by rule, or stop partway
through on a transient Finanzas error) -- the mailbox HAD mail this pass, so it is not silent, whatever
happened to that mail afterwards. A source with no marker at all has never once seen a message -- not "a
source that used to see mail" -- so `cli._estado_silencio` never calls that silence; there has to have
been mail before there can be a drought.

`Config` gains `alerta_silencio_horas` (`BUZON_ALERTA_SILENCIO_HORAS`, default 24 -- the same threshold as
the auth alert, and a reasonable floor for a business mailbox that gets at least one message a day: long
enough that a quiet weekend never fires, short enough that a connector gone silent for a whole business day
is flagged the next morning), validated by the same positive-integer rule `BUZON_ALERTA_AUTH_HORAS` already
uses (`config.py`'s new shared `_horas_positivas_desde_entorno` helper, so the two never drift in how they
reject a bad value). `comando_alertas` lists a `"tipo": "silencio"` entry, `desde`/`horas`/`remedio` in the
exact same shape as an `"auth"` entry (`_remedio_silencio` names only the source id, never a credential:
check the mailbox is still receiving mail, check `reglas.json` is not excluding everything), for any source
past that threshold that is NOT already in an auth alert -- an auth failure already explains the silence,
so the two never double up on the same underlying cause. There is no separate "clear" step: the marker
simply moves forward past the threshold the instant a pass sees mail again, so the alert disappears on its
own, by construction, same as `alerta_auth_horas`'s own comparison always has.

### Tests, measured

4 new `def test_` in `tests/test_cliente_imap.py`: a message with an old `INTERNALDATE` but a UID past the
stored watermark is forwarded once and never repeated on a second pass
(`test_mensaje_con_fecha_antigua_pero_uid_nuevo_en_resumen_se_ve_y_se_reenvia_una_vez`); the `SINCE`
fallback's own date skip is counted and still advances the watermark, proven by a SECOND folder (sorted
after the skipped one) yielding a message whose own `cursor_proveedor` snapshot already carries the first
folder's advanced watermark even though that folder never yielded anything itself
(`test_fecha_antigua_en_fallback_since_se_cuenta_y_avanza_el_cursor`); and two casefold tests, a flat
`carpetas` entry and one across the delimiter boundary
(`test_carpetas_coincide_sin_importar_mayusculas_o_minusculas`,
`test_carpetas_hijo_anidado_coincide_sin_importar_mayusculas_o_minusculas`).

3 new `def test_` in `tests/test_registro.py`, same shape as the existing `omitidos_por_error_proveedor`
trio: `omitidos_por_fecha_proveedor` absent at 0, present and exactly placed when nonzero, and coexisting
correctly alongside `omitidos_por_error_proveedor`.

3 new `def test_` in `tests/test_alerta.py`: `cargar_ultimo_visto_en` is `None` with no marker;
`marcar_ultimo_visto` writes and reads back the exact moment; a second call moves the marker FORWARD
(unlike `marcar_auth_fallando`'s own set-once behavior).

3 new `def test_` (one parametrized, 5 cases) in `tests/test_config.py`, the same shape as the existing
`BUZON_ALERTA_AUTH_HORAS` trio: `alerta_silencio_horas` defaults to 24, accepts a positive integer, and
rejects `"0"`/`"-1"`/a non-integer/a float-looking string by naming the variable (empty treated as unset).

10 new `def test_` in `tests/test_cli.py`: `omitidos_por_fecha_proveedor` wired through `estado`'s line and
`historial.jsonl`'s `omitidos` total, same shape as the existing `omitidos_por_error_proveedor` pair
(`test_sincronizar_registra_omitidos_por_fecha_proveedor_en_estado_y_en_historial`, and the existing
`test_sincronizar_sin_omitidos_de_proveedor_no_lo_imprime` extended to also assert the new field is absent
at zero); a pass with `vistos>0` marks `ultimo_visto_en` to that pass's own moment
(`test_pasada_con_vistos_marca_el_ultimo_visto`); a pass with `vistos=0` leaves it untouched
(`test_pasada_sin_vistos_no_marca_el_ultimo_visto`); a pass that saw mail but then stopped on a transient
error still marks it (`test_pasada_con_vistos_pero_detenida_igual_marca_el_ultimo_visto`); `alertas --json`
lists a silent source, stays empty under the threshold, never calls a never-seen source silent, clears the
instant a pass sees mail again, and never double-lists a source already in an auth alert
(`test_alertas_json_lista_la_fuente_en_silencio`,
`test_alertas_json_sin_pasar_el_umbral_de_silencio_queda_vacio`,
`test_alertas_json_fuente_que_nunca_vio_correo_nunca_esta_en_silencio`,
`test_alertas_json_silencio_se_limpia_tras_una_pasada_con_correo`,
`test_alertas_json_auth_tiene_prioridad_sobre_silencio_para_la_misma_fuente`); and the Finanzas token still
never appears in `alertas`' own output with a silencio entry present
(`test_estado_y_alertas_nunca_imprimen_el_token_finanzas_con_silencio`).

### Gate, measured

`uv run pytest -q --junitxml=.gate-out/gate_report.xml` on this slice's own final tree (slice commit
`6b803cb`, tree `2055d30eb40488c9ff179a45abd1fc4b8f911f52`), run in the background past the 600s foreground
cap and read back from the artifact rather than inferred from an exit code:

    testsuite tests="728" failures="0" errors="0" skipped="9"

728 - 9 = 719 passed. Reconciled against this slice's own base (`main` at
`edfb9dcd7f998d40a43bd9aacceadae694b1ad85`, 701 total per `BUZON-IMAP-MENSAJE-AISLADO-1`'s own recorded
figure): 728 - 701 = 27 new collected items, matching the 23 new `def test_` lines above once the one
parametrized `test_config.py` case (5 values, 1 already counted as a def) is expanded: 4 + 3 + 3 +
(2 plain + 5 parametrized) + 10 = 27 -- no drift, no guessed number. The 9 skips are the same pre-existing
Windows file-mode skips every earlier slice in this ledger has recorded; none are new.

Tree identity proven before writing this entry, so the gate above was not re-run for the merge: merge
commit `dafcd72d0a750309984513b09f86cb23e1e2e6d2` has tree `2055d30eb40488c9ff179a45abd1fc4b8f911f52`,
IDENTICAL to the slice branch's own tree at commit `6b803cbb21e0bd92b629d65f258ae534b56a47bc`, and
`git diff HEAD feat/buzon-imap-fecha-drop-y-silencio-1 --stat` against the merge commit was empty.

### What the first pass after deploy should show on duna-zoho, if Fix 1 was the cause

If the investigation's own candidate explanation was right, the first `sincronizar` pass after this slice
deploys should show duna-zoho's own stuck folder (whichever of `INBOX`/`Archive`/`Newsletter`/
`Notification`/`Snoozed`/`Templates` was holding the wedged message) with `vistos` finally greater than
zero and `reenviados` (or `duplicados`/`omitidos_por_reglas`, depending on that message's own content and
rules) accounting for it, exactly ONCE -- the stuck message forwarded (or correctly skipped by rule) a
single time, never repeated on the pass after. `historial.jsonl`'s own trend table should show that one
pass's `vistos` rising off the 35h-long run of zeros, with no `omitidos_por_fecha_proveedor` necessarily
appearing at all for duna-zoho specifically (Fix 1 removes a drop that happened on the UID-RESUME path;
`omitidos_por_fecha_proveedor` only ever counts a drop on the `SINCE` fallback, a different, narrower case).
If `vistos` for duna-zoho is STILL zero after this deploys, Fix 1 was not the whole explanation and the
silence alert (Fix 3) is what should catch it next, once `BUZON_ALERTA_SILENCIO_HORAS` passes.

### Merge-policy-A classification, made by this slice

No schema or migration -- this repository has no database; `fuentes/<id>/visto.json` is a new per-source
file on disk, the same category `BUZON-ALERTA-AUTH-1`'s own `alerta.json` and `BUZON-FUENTES-ESQUEMA-1`'s
`cursor_proveedor.json` were already classified as. No cross-repo contract or DTO change -- nothing here
touches the Finanzas or Cuenta Duna wire; `omitidos_por_fecha_proveedor` and the `"silencio"` alert type
are both local to this connector's own CLI output, never sent to another repository. Customer-facing bytes
DID change, on the SAME broader reading `BUZON-ALERTA-AUTH-1` recorded rather than the narrower one earlier
entries used: the new `omitidos_por_fecha_proveedor=<n>` field in `estado`'s line, and the new
`"tipo": "silencio"` entries in `alertas --json`, are bytes an OPERATOR reads (Carlos over SSH, Main and
the desk over `sudo -n`) -- never a customer of a Duna product, but this is a NEW alert surface, the same
kind of addition `BUZON-ALERTA-AUTH-1` chose not to narrow. The approval's own words -- "MERGE IS APPROVED
on GREEN." -- are, exactly as they were for that entry, an explicit, in-words authorization to merge past a
tripped condition once the gate is green, so this slice merges without waiting, on the broader reading of
this one condition rather than a claim that it did not trip.

### Attribution

Carlos, through Main, delegated Tier 1 work under `ORCH-CARLOS-DELEGATES-TIER-1-1`, naming
`BUZON-ZOHO-CURSOR-QUIETO-INV-1`'s own three follow-ups to be built as one slice. This slice read
`cliente_imap.py`'s own `listar_mensajes` against that investigation's exact line numbers, confirmed the
UID-resume-vs-date-fallback distinction already lived in `_buscar_uids`'s own branching condition, and
reused it rather than inventing a new one; read `_carpeta_excluida` against `_carpeta_en_lista` to find the
missing casefold; and read `alerta.py`'s own set-once marker shape before deciding the silence marker
needed the opposite (unconditional, move-forward) behavior, rather than reusing that same function.

### Open follow-ups, ranked

1. **`BUZON-ALERTA-CONSOLE-LINE-1`** (named by `BUZON-ALERTA-AUTH-1`, still open, now also covering
   `"tipo": "silencio"`): a console line surfacing a source's own alert in the admin UI, needing a
   Buzón-to-console wire that does not exist yet -- cross-repo, outside this repository's own `touches:`.
2. **A genuine IMAP transport failure (`OSError`/`imaplib.IMAP4.abort`) is still uncaught and untranslated**
   at every call site in `cliente_imap.py` (named by `BUZON-IMAP-MENSAJE-AISLADO-1`, still open) --
   unrelated to this slice's own three fixes, not touched here.
3. **A source already in a silence alert that THEN also starts failing auth** reports only `"tipo": "auth"`
   (auth takes priority, by this slice's own design, to avoid a redundant entry for the same underlying
   cause) -- but the moment auth starts succeeding again while the mailbox is STILL genuinely quiet (a
   `reglas.json` excluding everything, say), the silence alert resumes from whatever `ultimo_visto_en`
   already held, which could already be well past the threshold, surfacing immediately rather than needing
   a fresh 24h. This is the CORRECT behavior (the silence is real and has been ongoing), named here only so
   a future reader does not mistake it for a bug.

---

## BUZON-IMAP-CONEXION-ROTA-1 -- a genuine IMAP connection break gets its own exception type (2026-11-26)

### Why

Carlos's own overnight direction (via the desk, `ORCH-CARLOS-DELEGATES-TIER-1-1`), relayed through the
dispatch `BUZON-SIGUIENTE-SEGUIMIENTO-2026-11-26-B`: pick the most valuable OPEN follow-up this
repository's own ledger names, preferring a real defect in mail delivery over hygiene.
`BUZON-IMAP-FECHA-DROP-Y-SILENCIO-1`'s own ranked list (above) named three: `BUZON-ALERTA-CONSOLE-LINE-1`
(cross-repo, needs a Buzón-to-console wire that does not exist, outside this repository's own `touches:`
and this slice's approval -- set aside, matching `BUZON-IMAP-MENSAJE-AISLADO-1`'s own earlier reasoning for
the same item); item 3 (the silence-after-auth-recovery ordering), which that same entry already recorded
as CORRECT behavior, not a bug -- nothing to fix. Item 2, first named by `BUZON-IMAP-MENSAJE-AISLADO-1`,
was the one buildable, reversible defect left: verified still open against the code (`cliente_imap.py`'s
`_examinar_carpeta`/`_buscar_uids`/`_fetch_uno` had no translation around their own `conexion.select`/
`conexion.uid` calls) before building anything.

### The gap, verified against the code

`_conexion_activa` already translates a connection-level `OSError` from `_construir_conexion` into
`ClienteImapErrorTransitorio("conectar", ...)`. Every OTHER call into `ConexionImapProtocolo` --
`_listar_carpetas_crudo`'s `conexion.list()`, `_examinar_carpeta`'s `conexion.select(...)`, `_buscar_uids`'s
`conexion.uid("SEARCH", ...)`, `_fetch_uno`'s `conexion.uid("FETCH", ...)` -- had no such translation: a
broken socket (`OSError`) or imaplib's own `abort` (the server closed the connection, or sent something
imaplib's own parser could not make sense of) propagated as imaplib's RAW exception, all the way out of
`listar_mensajes`, past `sincronizar.ejecutar_pasada`'s own `except ProveedorErrorTransitorio` (which an
`OSError`/`imaplib.IMAP4.abort` is not), as an UNHANDLED exception -- a traceback Python itself would print
to the journal, risking the server's own unparsed response bytes reaching stdout/stderr, which invariant 2
forbids ("no ... Graph message id is ever printed or logged anywhere in this codebase" -- the same
principle this provider's own docstring already states for `OSError`/`abort`, just never enforced at these
four sites). `_listar_carpetas_crudo` was not named by `BUZON-IMAP-MENSAJE-AISLADO-1`'s own follow-up text
(which named only `_examinar_carpeta`/`_buscar_uids`/`_fetch_uno`), but reading it found the identical gap,
so it is fixed here too rather than left as a fourth, now-visible instance of the same bug.

### Fix, and the design question it had to answer

The follow-up's own text asked for "the exception TYPE it propagates as should still be this module's own,
not imaplib's raw one ... no behavior change beyond the exception's own class." Reusing the EXISTING
`ClienteImapErrorTransitorio` for this would have been wrong: that class is exactly what `listar_mensajes`'s
own per-folder catch (`except ClienteImapErrorTransitorio:`, skip one folder) and the per-message catch
(`_ERRORES_DE_CONTENIDO_AISLABLES`, skip one message) are built to swallow as an ISOLATABLE problem
(`BUZON-IMAP-MENSAJE-AISLADO-1`) -- translating a genuine connection break into that same type would have
let a broken connection be silently retried as "just this one folder" or "just this one message," instead
of stopping the whole pass as invariant 5 and that slice's own design require. A new SIBLING class,
`ClienteImapConexionError(ClienteImapError, ProveedorErrorTransitorio)`, is a `ProveedorErrorTransitorio`
(so `sincronizar.py`'s existing top-level catch still stops the whole pass with a clean, class-name-only
motivo, exactly the `GraphErrorTransitorio`/`ClienteImapErrorTransitorio` shape already produces) but is
NEVER a `ClienteImapErrorTransitorio` and never one of `_ERRORES_DE_CONTENIDO_AISLABLES`, so neither catch
can mistake it for either kind of isolatable failure. A new helper, `_invocar_conexion(paso, llamada)`,
wraps each of the four call sites, catching exactly `(OSError, imaplib.IMAP4.abort)` and re-raising
`ClienteImapConexionError(paso, detalle=type(exc).__name__)` -- the same `paso` name each site's own
existing protocol-failure raise already uses (`"list"`/`"examinar"`/`"buscar"`/`"fetch"`), so nothing about
the stop reason's own vocabulary changes. The now-redundant local `import imaplib` inside
`_construir_conexion_ssl_real` was removed in favour of the one new module-level import this helper needs.

### Tests, measured

`tests/test_cliente_imap.py`: a fake `_ConexionQueRompe` whose one named call raises either `OSError` or
`imaplib.IMAP4.abort`, parametrized over both exception types and all four call sites
(`test_fallo_de_conexion_se_traduce_y_sigue_deteniendo_toda_la_pasada`, 8 cases) -- asserts
`ClienteImapConexionError` is raised (never the raw exception), is a `ProveedorErrorTransitorio`, carries
the expected `paso` and the exception's own class name as `detalle`, that `str(...)` is exactly
`"imap <paso> detalle=<ClaseExcepcion>"` (never the raw exception's own text), and that
`omitidos_por_error_proveedor` stays `0` (never counted as an isolatable skip). The one pre-existing test
this replaces (`test_conexion_rota_durante_fetch_sigue_deteniendo_toda_la_pasada`, which asserted the OLD,
now-superseded `pytest.raises(OSError)`) was rewritten into this parametrized form rather than left
alongside it, since it tested exactly the one case (`fetch`, `OSError`) this new test already covers.

### Gate, measured

`.venv/Scripts/python.exe -m pytest --junit-xml=.gate-out/gate.xml -q` on this slice's own final tree
(commit `8133671e100e71407e6120ee67baac96ee147011`, tree `c08f14bcb48c69477879c909eec123630ccf0072`), run in
the background past the 600s foreground cap and read back from the artifact rather than inferred from an
exit code:

    testsuite tests="735" failures="0" errors="0" skipped="9"

735 - 9 = 726 passed. Reconciled against this slice's own base (`main` at
`8c2afc15329591e230ddb8d11e24fcf25dd020cb`, 728 total per `BUZON-IMAP-FECHA-DROP-Y-SILENCIO-1`'s own
recorded figure): 735 - 728 = 7 new collected items, matching the diff exactly -- the new parametrized test
collects 8 items (2 exception types x 4 call sites) and the one replaced test removes 1, net +7, no drift.
The `.gate-out/gate.xml` path itself was found already populated with a STALE file from an earlier slice's
own run before this one started (mtime predating this run); this entry's own figures are read from the
file only AFTER confirming its mtime moved past this run's own start, never from the stale copy.

Tree identity proven before writing this entry, so the gate above was not re-run for the merge: merge
commit (recorded below) has tree `c08f14bcb48c69477879c909eec123630ccf0072`, IDENTICAL to the slice branch
`feat/buzon-siguiente-seguimiento-2026-11-26-b`'s own tree at commit
`8133671e100e71407e6120ee67baac96ee147011`, and `git diff HEAD feat/buzon-siguiente-seguimiento-2026-11-26-b
--stat` against the merge commit was empty. The 9 skips are the same pre-existing Windows file-mode skips
every earlier slice in this ledger has recorded; none are new.

### Merge-policy-A classification, made by this slice

No schema or migration -- this repository has no database, and no on-disk file layout changed (the new
exception class and the new helper are in-memory only; nothing new is persisted). No cross-repo contract or
DTO change -- nothing here touches the Finanzas or Cuenta Duna wire; this diff is entirely internal to
`cliente_imap.py`'s own exception handling. No customer-facing bytes: unlike `BUZON-ALERTA-AUTH-1` or
`BUZON-IMAP-FECHA-DROP-Y-SILENCIO-1`, which each added a NEW printed surface (an alert line, an alert
type), this slice reuses the EXISTING `"imap <paso> detalle=<X>"` string shape every other `ClienteImapError`
subclass already produces and the existing `"parada: ..."` line format `BUZON-MOTIVO-DE-PARADA-1` already
built -- the only change is that this shape is now reachable for a connection break that used to crash
uncaught instead, never a new kind of line an operator did not already see from a sibling failure. All
three conditions clear, so this slice merges on green without stopping for approval.

### Attribution

Carlos's own overnight direction (via the desk, `ORCH-CARLOS-DELEGATES-TIER-1-1`) asked for the most
valuable open follow-up. This slice re-verified `BUZON-IMAP-FECHA-DROP-Y-SILENCIO-1`'s own ranked list
against the code, set aside the cross-repo item and the already-correct-behavior item, and built the one
remaining buildable defect first named by `BUZON-IMAP-MENSAJE-AISLADO-1`: the new `ClienteImapConexionError`
type, the `_invocar_conexion` helper, its wiring into all four unwrapped call sites (including
`_listar_carpetas_crudo`, found while reading the other three), and the parametrized regression test.

### Open follow-ups, ranked

1. **`BUZON-ALERTA-CONSOLE-LINE-1`** (named by `BUZON-ALERTA-AUTH-1`, still open, covering both the `"auth"`
   and `"silencio"` alert types): a console line surfacing a source's own alert in the admin UI, needing a
   Buzón-to-console wire that does not exist yet -- cross-repo, outside this repository's own `touches:`.
   Still the only follow-up with a clear owner (the console/admin-ui repository) yet to build its half.
2. **A source already in a silence alert that THEN also starts failing auth** reports only `"tipo": "auth"`
   until auth clears, at which point the silence alert can surface immediately rather than needing a fresh
   24h -- recorded by `BUZON-IMAP-FECHA-DROP-Y-SILENCIO-1` as CORRECT behavior, not a bug; kept here only so
   a future reader does not have to re-derive that.
3. Every other follow-up this ledger ever named before this entry is closed by a later slice, re-verified
   against the code while compiling this list (`_listar_carpetas_crudo`'s own matching gap, found while
   building this slice, is fixed by this same entry, not left open).

## BUZON-CARPETAS-CONTEO-1 -- `carpetas` gets per-folder counts, the silence alert gets a floor (2026-11-26)

### Why

`BUZON-IMAP-FECHA-DROP-Y-SILENCIO-1` (8c2afc1) deployed, and `duna-zoho` still read `vistos=0` on every
pass (08:00 and 08:10 UTC, 26 Nov), its cursor unmoved since 2026-11-24T19:30:08Z. The date-filter drop was
real but not shown to be the whole cause, and there was no way to tell "no mail arrived" apart from "mail
arrived and we are not seeing it" from the outside -- `carpetas <fuente>` printed a folder's name, flags and
`leida=si/no`, never a count. This slice gives it the numbers that tell the two apart, investigates whether
a source whose cursor predates 8c2afc1 drops mail on its first post-deploy pass, and checks whether the
silence alert can ever raise for a source that has NEVER once seen mail.

### `carpetas` gets per-folder counts (BUZON-CARPETAS-CONTEO-1)

For every folder a source WOULD read, `carpetas <fuente>` now also prints that folder's own MESSAGES,
UIDNEXT and UIDVALIDITY (`cliente_imap.ClienteImap._examinar_carpeta`'s own EXAMINE response already
carries all three; the EXISTS count was parsed and added, nothing new issued), this source's own stored
per-folder UID watermark (`marca`, read from the SAME `_cursor_inicial_mapa` a live pass would resume from,
never updated by this diagnostic), and how many messages carry a UID past it (`nuevos`), via one new
`ClienteImap.diagnosticar_carpeta` method and its own `_contar_uids_desde` helper -- one more `UID SEARCH`,
sharing `_buscar_uids`'s own empty-range guard, never a FETCH, so no content is ever read. A folder with no
stored watermark, or one naming a UIDVALIDITY the server no longer has, prints `marca=sin marca`/
`nuevos=sin marca` for the latter field (no count means anything against a UIDVALIDITY a live pass itself
would not resume from). An excluded folder (`leida=no`) is never EXAMINEd and prints no counts, unchanged.
One folder's own broken EXAMINE/SEARCH (`ClienteImapErrorTransitorio`) is reported on its own line and
never stops the rest of the report (`comando_carpetas`, `cli.py`), the same per-folder isolation
`listar_mensajes` already gives a live pass (`BUZON-IMAP-MENSAJE-AISLADO-1`) -- a genuine connection break
still stops the whole command.

**The line that answers Main's own question**: `nuevos=0` on every folder a source reads means no mail
arrived -- every folder's watermark is caught up with the server's own UIDNEXT. `nuevos` > 0 on any folder
while `sincronizar` keeps reporting `vistos=0` for that source means mail IS there and this connector is
not seeing it -- worth investigating. `marca=sin marca` means this source has never stored a watermark for
that folder yet, so nothing is counted against it.

### The pre-8c2afc1 first-pass question, read against the code -- no defect found

Read `cliente_imap.py`'s `_buscar_uids`: a folder's own `cursor_previo` comes from `ClienteImap.
__init__`'s `_cursor_inicial_mapa`, parsed once from `cursor_proveedor_inicial` by `_analizar_cursor_multi`.
For ANY folder with no entry there (never synced under this provider at all, or a folder this
`BUZON-FUENTE-IMAP-CARPETAS-1` slice can newly read but an older, `INBOX`-only source never did),
`cursor_previo` is `None`, and `_buscar_uids` has exactly two branches: resume by UID when `cursor_previo` is
set and its own UIDVALIDITY matches, or `SINCE _formato_fecha_imap(desde)` otherwise -- `desde` being the
pass's own date cursor (`cursor.json`, minus the 10-minute overlap, `sincronizar.ejecutar_pasada`'s own
`desde_con_solape`). There is no third branch, and no path that starts from UIDNEXT or from UID zero. A
source whose cursor predates 8c2afc1 (the OLD bare `"<uidvalidity>:<uid>"` string, read as `INBOX`'s own
cursor by `_analizar_cursor_multi`'s own backward-compat branch) resumes `INBOX` exactly where it left off
and falls back to the stored date cursor for every OTHER folder it can now read -- never skipping mail that
arrived after the cursor date, never re-forwarding what `INBOX` already covered. This is already proven by
two EXISTING tests, re-read and re-run rather than duplicated:
`test_sin_cursor_previo_usa_since_por_fecha` (no cursor at all -> `SINCE`, never `UIDNEXT`/zero) and
`test_cursor_proveedor_formato_antiguo_se_lee_como_inbox` (the old bare-string format resumes `INBOX` by
UID, issuing no `SINCE` search at all) -- joined by `test_uidvalidity_distinta_cae_de_nuevo_a_since` and
`test_tres_carpetas_con_trash_y_cambio_de_uidvalidity` for the mixed multi-folder case. The "forward nothing
twice" duplicate guard is proven by `test_mensaje_con_fecha_antigua_pero_uid_nuevo_en_resumen_se_ve_y_se_
reenvia_una_vez`'s own second-pass assertion (the watermark, once advanced, is never re-yielded) and, at the
Finanzas layer, `test_sincronizar.py::test_reenvio_duplicado_por_el_solape_se_cuenta_como_duplicado`. No
defect found on this path, so no fix was built and no new test duplicates this existing coverage.

### The silence-alert floor -- a real defect, fixed

Read `cli.py`'s `_estado_silencio` and `alerta.py`'s own `visto.json`: a source that has NEVER once seen a
message has no `ultimo_visto_en` marker at all, and `_estado_silencio` returned `None` UNCONDITIONALLY for
it, by the design `BUZON-ALERTA-SILENCIO-1` itself documented ("there has to have been mail before there
can be a drought"). That reads correctly for a source whose mailbox has always been empty of anything this
connector would forward -- but it also means a source that is BROKEN from day one (excluded folders, a
`reglas.json` that excludes everything, a stuck connection) can run empty pass after empty pass FOREVER and
never raise, the exact blind spot `BUZON-ALERTA-SILENCIO-1` was built to close, just left open for the one
case it has no prior sighting to measure from. Fixed with a new marker, `fuentes/<id>/primera_pasada.json`
(`alerta.cargar_primera_pasada_en`/`marcar_primera_pasada`): set ONCE, at the very start of
`cli._ejecutar_una_fuente`, on the first pass this connector EVER attempts for a source -- whatever that
pass does afterwards -- and kept forever after, the same set-once shape `marcar_auth_fallando` already
uses. `_estado_silencio` now falls back to it whenever `ultimo_visto_en` is absent, so a source that has
produced nothing from the very first pass still raises past `BUZON_ALERTA_SILENCIO_HORAS` instead of
staying invisible; a source that has never even attempted a pass yet (neither marker exists) still stays
un-alerted, since there is nothing yet to measure from. `ultimo_visto_en`, when present, still takes
priority -- a source that DID once see mail measures silence from that sighting, never from the older
first-pass marker.

### Tests, measured

`tests/test_cliente_imap.py` (4 new): `diagnosticar_carpeta` with a matching stored watermark counts UIDs
past it correctly (`test_diagnosticar_carpeta_con_marca_cuenta_los_uids_por_encima`); with no stored
watermark at all, both `marca` and `nuevos` are `None`
(`test_diagnosticar_carpeta_sin_marca_es_sin_marca`); with a watermark naming a DIFFERENT UIDVALIDITY,
`marca` still reports the stale stored pair but `nuevos` stays `None`
(`test_diagnosticar_carpeta_con_uidvalidity_distinta_no_cuenta`); and a folder already caught up with
UIDNEXT issues no `UID SEARCH` at all, `nuevos=0`
(`test_diagnosticar_carpeta_al_dia_no_emite_busqueda`).

`tests/test_alerta.py` (3 new): `cargar_primera_pasada_en`/`marcar_primera_pasada` round-trip, and a second
`marcar_primera_pasada` call conserves the FIRST moment (set-once, mirroring `marcar_auth_fallando`'s own
existing tests).

`tests/test_cli.py` (7 new): `comando_carpetas` prints the new fields for an included folder with a stored
watermark, and `marca=sin marca nuevos=sin marca` for one with none
(`test_comando_carpetas_con_marca_imprime_conteos`); an excluded folder prints no counts and is never
EXAMINEd (`test_comando_carpetas_excluida_no_imprime_conteos`); `comando_alertas` raises `"silencio"` for a
source with only a `primera_pasada_en` marker past the threshold
(`test_alertas_json_fuente_que_nunca_vio_correo_pero_ya_fue_pasada_cae_en_silencio_tras_el_umbral`) and
stays empty before it
(`test_alertas_json_fuente_que_nunca_vio_correo_antes_del_umbral_queda_vacio`); `ultimo_visto_en` outranks
an older `primera_pasada_en` (`test_alertas_json_ultimo_visto_tiene_prioridad_sobre_primera_pasada`); and
`comando_sincronizar` writes `primera_pasada.json` on its first pass and never overwrites it on a later one
(`test_sincronizar_marca_primera_pasada_en_la_primera_corrida`,
`test_sincronizar_no_sobrescribe_primera_pasada_en_corridas_siguientes`). The pre-existing
`test_alertas_json_fuente_que_nunca_vio_correo_nunca_esta_en_silencio` (a source with NEITHER marker) was
re-read and left as is -- still correct, since neither marker existing means this source has never even
attempted a pass.

### Gate, measured

`uv run pytest -q` on this slice's own final tree (branch `feat/buzon-carpetas-conteo-1` at commit
`7ed30e680d563c66029b75a977822a387e28e0b4`, tree `e998c1d7eb178c26eb38a6acd7f8fbcd54361f1c`), run in the
foreground, moved to the background by the harness past its own cap and read back from its own output file
rather than inferred from an exit code:

    740 passed, 9 skipped in 663.24s (0:11:03)

749 total. Reconciled against this slice's own base (`main` at
`f182fd88c06b7000bf013af8ad1a28829b03862e`, 735 total per `BUZON-IMAP-CONEXION-ROTA-1`'s own recorded
figure): 749 - 735 = 14 new collected items, matching the 14 new tests listed above exactly (4 + 3 + 7), no
drift. The 9 skips are the same pre-existing Windows file-mode skips every earlier slice in this ledger has
recorded; none are new.

Tree identity proven before writing this entry, so the gate above was not re-run for the merge: merge
commit (recorded below) has tree `e998c1d7eb178c26eb38a6acd7f8fbcd54361f1c`, IDENTICAL to the slice branch
`feat/buzon-carpetas-conteo-1`'s own tree at commit `7ed30e680d563c66029b75a977822a387e28e0b4`, and
`git diff HEAD feat/buzon-carpetas-conteo-1 --stat` against the merge commit was empty.

### Merge-policy-A classification, made by this slice

No schema or migration -- this repository has no database; the one new on-disk file
(`primera_pasada.json`) follows the exact shape `visto.json`/`alerta.json` already established, not a new
kind of persistence. No cross-repo contract or DTO change -- nothing here touches the Finanzas or Cuenta
Duna wire; the diff is internal to `cliente_imap.py`'s own diagnostic method, `cli.py`'s own `carpetas`/
`_estado_silencio`, and `alerta.py`'s own marker pair. Customer-facing bytes: the ONLY printed surface this
slice changes is `carpetas <fuente>`'s own operator-diagnostic output (new fields appended after the
existing `leida=si/no`, never replacing it) and `alertas --json`'s own `"silencio"` entries now also
appearing for a source that previously could never produce one -- both read exclusively by Carlos himself
(over `sudo -n duna-deploy-buzon carpetas <id>`/`alertas`) and by Main's own session-start hub check, never
by an end customer of any Duna product; this slice's own approval (`ORCH-CARLOS-DELEGATES-TIER-1-1`)
explicitly describes and authorizes exactly this diagnostic output as "reversible," and the dispatch's own
body states in words "MERGE IS APPROVED on GREEN. Main deploys." All three conditions clear on the diff
itself, independent of that explicit authorization, so this slice merges on green without stopping for
approval.

### Attribution

`ORCH-CARLOS-DELEGATES-TIER-1-1` dispatched this slice directly (not through the ranked-follow-up list the
two prior slices drew from) to answer `duna-zoho`'s own `vistos=0` mystery. All four numbered build items
were completed: the `carpetas` counts (1), the pre-8c2afc1 first-pass investigation, which found no defect
to fix (2), the silence-alert floor, which found and fixed a real one (3), and this gate and ledger entry
(4).

### Open follow-ups, ranked

1. **`BUZON-ALERTA-CONSOLE-LINE-1`** (unchanged from the prior entry, still open, cross-repo, outside this
   repository's own `touches:`): a console line surfacing a source's own alert (now including a
   `primera_pasada`-floored silencio) in the admin UI.
2. **Whether `duna-zoho`'s own `vistos=0` is "no mail" or "mail we are not seeing"** is not something this
   slice could answer from inside the repository alone -- it needs `carpetas duna-zoho` RUN against the
   real mailbox, which only an operator with host access can do. This slice built the tool that answers it
   (`nuevos` per folder); running it against the live source is the next step, owned by whoever has host
   access, not by a future code slice.
