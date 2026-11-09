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
