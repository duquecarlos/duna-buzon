# Host deploy entry script (BUZON-CONECTOR-1)

Under Main's `contracts/host-deploy-v1.md` (ORCH-HOST-DEPLOY-V1) and its sensitive-data amendment
(`contracts/host-deploy-v1-amendment-sensitive-data.md`). `duna-deploy-buzon` (this directory) is a
root-owned entry script, installed once by hand, that a session can then call with `sudo -n` and no
password, for this one fixed command only.

## Install (once)

```
sudo install -o root -g root -m 755 deploy/host/duna-deploy-buzon /usr/local/sbin/duna-deploy-buzon
sudo install -o root -g root -m 440 deploy/host/duna-deploy-buzon.sudoers /etc/sudoers.d/duna-deploy-buzon
sudo visudo -c
```

`visudo -c` checks every file under `/etc/sudoers.d/`, not just this one -- a syntax error anywhere in
that directory would otherwise lock out `sudo` for everyone on the host, so always run it after
installing or editing a fragment there.

## Update

As of `HOST-AUTOACTUALIZAR-BUZON-1`, `deploy` installs its own newer copy of this script and its
sudoers fragment BY ITSELF, as the first step after the pull and before anything that depends on the
new code (`autoactualizar`, inside `deploy/host/duna-deploy-buzon`):

1. Compares the just-pulled `deploy/host/duna-deploy-buzon`, its `.sudoers` fragment, and
   `deploy/host/env-permitidas.json` (`env-set`'s own allowlist, `BUZON-REGLAS-ENTRADA-1`) against what
   is actually installed (`/usr/local/sbin/duna-deploy-buzon`, `/etc/sudoers.d/duna-deploy-buzon`,
   `/usr/local/sbin/duna-deploy-buzon-env-permitidas.json`). All three identical: prints `entry script:
   up to date` and the rest of `deploy` continues unchanged. `env-permitidas.json` is installed mode
   0644, owned by root -- it names no secret, only which keys `env-set` may write and the pattern each
   one's value must match.
2. If the sudoers fragment changed, it REFUSES before installing anything unless the new fragment is
   EXACTLY one `NOPASSWD` rule on this one path -- no wildcard, no second command, no `ALL` -- and
   `visudo -cf` accepts it.
3. Prints the full unified diff of every file about to change.
4. Keeps the previous installed copies (`/usr/local/sbin/duna-deploy-buzon.prev`,
   `/etc/sudoers.d/.duna-deploy-buzon.prev` -- the leading dot keeps it outside `#includedir`'s own
   pattern, so it is never itself read as a rule).
5. Installs the new copies (`install -o root -g root`, mode 0755 for the script, mode 0440 for the
   sudoers fragment), then RE-EXECS itself under the newly installed script -- once, never looping --
   so the rest of THAT SAME `deploy` runs under the new code.

`sudo -n /usr/local/sbin/duna-deploy-buzon rollback-script` restores both files from their `.prev`
copies, undoing autoactualizar's most recent install; it refuses by name if neither `.prev` copy exists.

`deploy --dry-run` prints this whole plan (the comparison, the diff, the planned backup and install, the
re-exec) without changing anything.

**Bootstrap (still manual, every time):** autoactualizar only runs INSIDE `deploy`, so the FIRST time a
new version of this script exists -- including the very first install, and this `HOST-AUTOACTUALIZAR-
BUZON-1` version itself landing on a host that only has an older copy installed -- it still needs the
exact three `sudo install`/`visudo -c` lines above, run by hand, once. From the SECOND `deploy` after
that onward, autoactualizar keeps the installed copy current on its own.

A change to `deploy/systemd/duna-buzon.service` or `.timer` takes effect the next time `deploy` runs:
as of `BUZON-ENTRADA-UNIDADES-REGISTROS-1`, each unit is diffed against what is installed and only
copied if it differs, printed as `sin cambios` or `actualizada`, with exactly one `systemctl
daemon-reload` after both -- this is unrelated to autoactualizar, which only concerns this entry script
and its sudoers fragment.

## Automatic rollback on a failed health check (BUZON-DEPLOY-REVIERTE-SOLO-1)

Buzón is a timer job, not a server, so `deploy`'s own health check -- the same `python -m duna_buzon
estado` run `status` also runs -- IS its health: one pass, not a port to poll. When that check fails,
`deploy` itself reverts the CODE to the `before` sha it captured at the start of this run (carried
across autoactualizar's own re-exec, so a changed entry script never loses the real rollback point),
re-syncs the environment (`uv sync --frozen`) and re-runs that same check once more, printing exactly
one line -- `rolled back to <sha>: healthy` or `rolled back to <sha>: STILL UNHEALTHY` -- and exiting
non-zero either way: a rollback that restores the pass is still a failed deploy. `deploy --dry-run`
prints this revert plan too, without running any of it.

This NEVER rolls back `/srv/duna/data/buzon/` -- cursors, the token cache and `estado.json` are left
exactly as this run left them. This rollback only undoes a bad CODE change; a bad PASS (a stale cursor,
a source that read garbage) is `rebobinar-cursor`'s job (see "Re-reading a source from an earlier date"
below), not this one's.

## Remove

```
sudo rm -f /usr/local/sbin/duna-deploy-buzon
sudo rm -f /etc/sudoers.d/duna-deploy-buzon
sudo visudo -c
```

## Use

```
sudo -n /usr/local/sbin/duna-deploy-buzon status
sudo -n /usr/local/sbin/duna-deploy-buzon deploy [--dry-run]
sudo -n /usr/local/sbin/duna-deploy-buzon entrar
sudo -n /usr/local/sbin/duna-deploy-buzon rollback-script
sudo -n /usr/local/sbin/duna-deploy-buzon carpetas <id>
sudo -n /usr/local/sbin/duna-deploy-buzon fijar-carpetas <id> <carpeta>...
sudo -n /usr/local/sbin/duna-deploy-buzon fijar-carpetas <id> --todas
sudo -n /usr/local/sbin/duna-deploy-buzon registros <unidad> [n]
sudo -n /usr/local/sbin/duna-deploy-buzon rebobinar-cursor <id> <fecha ISO> [--aplicar]
sudo -n /usr/local/sbin/duna-deploy-buzon reglas-ver <fuente>
sudo -n /usr/local/sbin/duna-deploy-buzon reglas <fuente> [--aplicar]
sudo -n /usr/local/sbin/duna-deploy-buzon env-tiene <clave>
sudo -n /usr/local/sbin/duna-deploy-buzon env-set <clave> <valor> [--aplicar]
```

`status` is read-only: the owner's own `git` HEAD, `duna-buzon.timer`'s `systemctl is-active`,
`duna-buzon.service`'s `systemctl is-active` (automatically followed by its own last 30 redacted
journal lines when it is `failed`), a drift line per unit (`coincide`/`distinta`/`falta`, comparing
`/etc/systemd/system/<unit>` against this repository's own `deploy/systemd/<unit>`), the installed
`User=` of `duna-buzon.service`, the owner and mode of `/srv/duna/data/buzon`, and a health run of
`python -m duna_buzon estado` under `systemd-run`. `deploy` autoactualizes itself (see "Update" above),
pulls and `uv sync --frozen` as the repository's owner, creates `/srv/duna/data/buzon` (mode 0700, owned
by `duna`) if it does not already exist -- and never deletes it -- then, for each of
`duna-buzon.service`/`duna-buzon.timer`, diffs the repository's own copy against what is installed
under `/etc/systemd/system/`, installs only the ones that differ, and prints each as `sin cambios` or
`actualizada` (`BUZON-ENTRADA-UNIDADES-REGISTROS-1`) -- then runs exactly one `systemctl daemon-reload`,
`systemctl enable --now duna-buzon.timer`, and finishes with the same health check. `entrar` is the one
subcommand for CARLOS HIMSELF, run directly in his own SSH terminal (never through an automated, no-tty
session): it signs in interactively and prints Microsoft's own device-code URL to that terminal.
`rollback-script` restores this entry script and its sudoers fragment from the `.prev` copies
autoactualizar kept, undoing its most recent install. `carpetas <id>` and `fijar-carpetas <id> ...` are
`BUZON-CARPETAS-MICROSOFT-CLI-1`'s operator tooling over an existing source's `carpetas` setting -- see
"Choosing and setting `carpetas`" below. `registros <unidad> [n]` is read-only: see "Reading a unit's
own log (`registros`)" below. `rebobinar-cursor <id> <fecha> [--aplicar]` rewinds one source's own
cursor to re-read from an earlier date -- dry-run by default: see "Re-reading a source from an earlier
date (`rebobinar-cursor`)" below. `reglas-ver <fuente>` and `reglas <fuente> [--aplicar]` are
`BUZON-REGLAS-ENTRADA-1`'s operator tooling over an existing source's `reglas.json` -- see "Setting a
source's own rules (`reglas`)" below. `env-tiene <clave>` and `env-set <clave> <valor> [--aplicar]` are
the same slice's tooling over `/srv/duna/env/buzon.env` -- see "Setting a non-secret env variable
(`env-set`)" below.

## Operator steps (first deploy, by Carlos)

1. Create `/srv/duna/env/buzon.env` (mode 600, owned by `duna`) with:
   - `BUZON_GRAPH_CLIENT_ID=<the public client id Carlos registered for this connector>`
   - `BUZON_CLAVE_CIFRADO=<32 random bytes, base64>` -- generate it piped, never typed into argv:

     ```
     openssl rand -base64 32 | sudo -u duna tee /srv/duna/env/buzon.env.key
     ```

     then fold the `BUZON_GRAPH_CLIENT_ID=...` and `BUZON_CLAVE_CIFRADO=...` lines into
     `/srv/duna/env/buzon.env` by hand, and remove the temporary `.key` file.
2. Run Finanzas' own token-issuing script (`../finanzas/duna-finanzas-api/scripts/emitir_token_buzon.py`,
   on duna-prod-1) to mint this connector's `FINANZAS_BUZON_TOKEN`, and write it into
   `/srv/duna/env/buzon-conector.env` (mode 600, owned by `duna`) as `FINANZAS_BUZON_TOKEN=...`. That
   script is Finanzas' own, not this repository's.
3. Write `/srv/duna/data/buzon/reglas.json` (mode 600, owned by `duna`) from this repository's
   `deploy/reglas.example.json` -- replace every placeholder sender with a real one, and set `desde` to
   where the first sync should start reading from.
4. `sudo -n /usr/local/sbin/duna-deploy-buzon deploy` once, to install the units and create the data
   directory.
5. `sudo -n /usr/local/sbin/duna-deploy-buzon entrar`, in Carlos's own terminal: open the printed URL,
   enter the code, sign in with his own Microsoft account.
6. Confirm `sudo -n /usr/local/sbin/duna-deploy-buzon status` reports the timer active and the health
   check passing. The timer then runs a pass every 10 minutes on its own.

## Turning on `origen` (BUZON-ORIGEN-WIRE-1)

**Turn on only after Finanzas Release 37 is live** -- Finanzas merged the `origen` field
(`FIN-API-BUZON-ORIGEN-1`) but its `EntradaBuzonMeta` schema is `extra="forbid"`, so this connector sending it
before that release deploys gets every entry refused with a `422`. Until then, leave it off (the default).

Once Release 37 is live, add `BUZON_ENVIAR_ORIGEN=1` to `/srv/duna/env/buzon.env` (not a secret -- no need for
the piped-generation dance step 1 above uses for the encryption key) and restart the next scheduled pass, or run
one by hand with `systemd-run` as in `status`'s own health check. Every entry's `meta` then carries `origen`: the
source's own identity (`imap:<id>` or `google:cuenta-<uuid>`), used for exact-label routing on Finanzas' side.

## No backup subcommand

`/srv/duna/data/buzon/` holds only `fuentes/<id>/credencial.bin` (the encrypted MSAL token cache or
IMAP app password, one per source), `cursor.json`, `cursor_proveedor.json` (for IMAP, a JSON object
keyed by folder name, one `"<uidvalidity>:<uid>"` pair per folder), and `estado.json` -- all
re-creatable: `entrar --fuente <id>` rebuilds a source's own credential, and an empty or stale
`cursor.json`/`cursor_proveedor.json` only costs a few minutes of re-read overlap, which Finanzas' own
idempotency on `id_externo` absorbs for free. There is nothing here worth a dedicated backup path.

## Multi-source migration (BUZON-FUENTES-ESQUEMA-1)

As of this slice, `/srv/duna/data/buzon/` holds one subdirectory per mailbox source,
`fuentes/<id>/{fuente.json,credencial.bin,cursor.json,reglas.json}`, instead of those files sitting
directly under the data root. The FIRST `sincronizar`, `estado`, `entrar` or `fuentes` run against an
existing data directory still in the old (pre-`BUZON-FUENTES-ESQUEMA-1`) layout migrates it
automatically into `fuentes/hotmail-carlos/`, preserving the exact same cursor -- no mail is re-sent,
none is skipped. This needs nothing from the operator: the next scheduled `sincronizar` (or `deploy`'s
own health check, which runs `estado`) does it on its own, once, and is a no-op on every run after
that. `entrar` with no `--fuente` keeps working unchanged as long as exactly one source is configured,
which is the state migration leaves a single-mailbox install in.

## Adding an IMAP source (BUZON-FUENTE-IMAP-1) -- e.g. `legal@duna.solutions` on Zoho

1. Create a Zoho app password for `legal@duna.solutions`: sign in to Zoho Mail as that account, go to
   **Settings -> Security -> App passwords**, and make sure **IMAP access** is enabled for the account
   first (**Settings -> Mail Accounts -> IMAP Access**) -- Zoho's own app passwords only work once IMAP
   is turned on. Generate the app password and keep it somewhere Carlos can paste from in step 3; it is
   never typed anywhere in a terminal history or a file in this repository.
2. On the host, as `duna`:

   ```
   uv run python -m duna_buzon agregar-fuente imap --id legal-duna --direccion legal@duna.solutions \
       --host imap.zoho.com --token-finanzas carlos
   ```

   This writes `fuentes/legal-duna/fuente.json` (port defaults to 993, IMAP-over-SSL) and does NOT sign
   in yet.
3. Still as `duna`, in Carlos's own terminal (never through an automated, no-tty session, exactly like
   `entrar` for a Microsoft source):

   ```
   uv run python -m duna_buzon entrar --fuente legal-duna
   ```

   This prompts for the app password with no echo (`getpass`), verifies it with a real IMAP `LOGIN`
   against `imap.zoho.com:993`, and only then encrypts and stores it in
   `fuentes/legal-duna/credencial.bin`. A wrong app password is reported and nothing is stored.
4. Confirm with `uv run python -m duna_buzon fuentes` (lists `legal-duna proveedor=imap ...
   estado=activa`) and `uv run python -m duna_buzon estado` (reports its cursor once a pass has run).
   The next scheduled `sincronizar` picks it up automatically -- `duna-buzon.timer` already covers every
   active source, not just the Microsoft one.

This mailbox is read ONLY: `EXAMINE`, never `SELECT`; `BODY.PEEK[...]`, never `BODY[...]`; no
flag/move/delete command is ever sent (`src/duna_buzon/cliente_imap.py`; proven from the outside by
`tests/test_cliente_imap.py` against a fake IMAP server) -- for EVERY folder this reads, not only
`INBOX` (`BUZON-FUENTE-IMAP-CARPETAS-1`): every selectable folder except one the server marks
`\Trash`/`\Junk`/`\Sent`/`\Drafts`/`\All`, or, failing that, one named like one of those -- so mail
Zoho's own rules file into a folder (invoices, contracts) is read exactly like `INBOX` is, matching
the Microsoft source's own every-folder reach.

## When Graph can't list the whole mailbox (BUZON-GRAPH-502-1)

As of `BUZON-GRAPH-TIMEOUT-1` and `BUZON-GRAPH-502-1`, a Graph `/me/messages` LIST call that fails with
500/502/503/504 is retried in-pass (same budget as 429), and -- if it still fails after that -- the
connector retries the SAME page once at `$top=10`, then once more at `$top=1`, before finally giving up
on that pass (`estado.json` records which `$top` got through, or, failing every size, the smallest one
tried, next to `parada=graph listar http=<code>`). This handles a mailbox that is merely slow or
momentarily unavailable on its own.

If a mailbox keeps failing even at `$top=1` -- Graph genuinely cannot answer `/me/messages` as a whole
for it, as happened against `hotmail-carlos`'s own full mailbox -- narrow that source to specific
folders with the `carpetas` setting on `fuentes/<id>/fuente.json`, the same field `agregar-fuente imap`
already supports, now also read for `proveedor: "microsoft"`. Each entry is whatever Graph itself
accepts as a mail folder: a well-known name (`inbox`, `archive`, `sentitems`, ...) or a folder id. The
connector then reads each named folder on its own, in the order given, via `/me/mailFolders/{carpeta}/
messages` instead of one `/me/messages` call across the whole mailbox -- a much cheaper query for Graph
to answer. Leaving `carpetas` out (the default, and every Microsoft `fuente.json` before this slice)
keeps today's behaviour exactly: one `/me/messages` call reaching every folder.

## Choosing and setting `carpetas` (BUZON-CARPETAS-MICROSOFT-CLI-1)

As of this slice there is a CLI for `carpetas`, for an EXISTING imap or microsoft source, so nobody
needs to edit `fuente.json` by hand as `duna` any more:

```
sudo -n /usr/local/sbin/duna-deploy-buzon carpetas <id>
```

Read-only, and never prints the credential: for an `imap` source this runs `python -m duna_buzon
carpetas <id>` (the server's own `LIST`, with flags and whether this source would read each folder
today); for a `microsoft` source it runs `python -m duna_buzon carpetas-graph <id>` (every top-level
Graph mail folder's display name, id, and well-known name where one applies). Use its output to pick
the folder names (or ids) to narrow the source to, then:

```
sudo -n /usr/local/sbin/duna-deploy-buzon fijar-carpetas <id> <carpeta>...
sudo -n /usr/local/sbin/duna-deploy-buzon fijar-carpetas <id> --todas
```

The first form SETS `carpetas` to exactly the folders listed (each one its own argument -- never typed
as a single comma-joined string); the second REMOVES `carpetas` entirely, back to today's behaviour
(every folder in one call/LIST). Both run as `duna`, write `fuente.json` atomically, print the before
and after lists, validate the source id exists first, and never touch `credencial.bin` or the cursor.
Takes effect on the next scheduled pass, or run one by hand with `systemd-run` as in `status`'s own
health check. A source id is `[a-z0-9-]+` and a folder name is `[A-Za-z0-9_ .-]+` or a Graph id -- a
folder name outside that shape (for instance an IMAP folder path with a `/` in it) needs `python -m
duna_buzon fijar-carpetas` run directly as `duna`, bypassing this root-owned wrapper's narrower
allowlist.

## Reading a unit's own log (`registros`)

As of `BUZON-ENTRADA-UNIDADES-REGISTROS-1`, there is a read-only verb to read a unit's own journal
without SSH'ing in to run `journalctl` by hand:

```
sudo -n /usr/local/sbin/duna-deploy-buzon registros <unidad> [n]
```

`<unidad>` is `duna-buzon.service` or `duna-buzon.timer` -- a fixed allowlist, never a pattern. `n` is a
positive integer, capped at 200, defaulting to 40 when left out. This runs exactly `journalctl -u
<unidad> -n <n> --no-pager -o cat` -- the unit name is passed as `journalctl`'s own `-u` argument, never
built into a shell string -- and pipes the result through a redaction filter before printing it: the
credentials half of any `postgres://`/`postgresql://` URL, and the value of any
`...SECRET=`/`...TOKEN=`/`...PASSWORD=`/`...KEY=` assignment, each become `***`. `status` runs this same
redacted `journalctl` call on its own, automatically, for the last 30 lines of `duna-buzon.service`
whenever that unit's `systemctl is-active` reports `failed` -- so a failing pass is diagnosable from
`status` alone, without a separate `registros` call.

## Re-reading a source from an earlier date (`rebobinar-cursor`)

As of `BUZON-CURSOR-REBOBINAR-CLI-1`, there is an operator verb to re-read a source from an earlier
date after a bad pass -- before this, the only way was hand-editing `cursor.json` on the server:

```
sudo -n /usr/local/sbin/duna-deploy-buzon rebobinar-cursor <id> <fecha ISO> [--aplicar]
```

DRY-RUN BY DEFAULT: prints the source's current cursor and the computed new one, writes nothing.
`--aplicar` writes `cursor.json` atomically and also clears `cursor_proveedor.json` -- necessary for an
IMAP source, whose own per-folder UID watermark would otherwise override the rewound date on the very
next pass; a safe no-op for Microsoft and Gmail sources, which never read that file. Refuses an unknown
source, a date that is not valid ISO 8601, a date in the future, and a date more than 90 days back.
Re-forwarding mail already sent is NOT this command's own job to prevent: the next pass's duplicate
guard (Finanzas answers a re-seen message with `200`, not `201`, on `id_externo`) counts it as
`duplicados`, never `reenviados`. Takes effect on the next scheduled pass, or run one by hand with
`systemd-run` as in `status`'s own health check.

## Setting a source's own rules (`reglas`)

As of `BUZON-REGLAS-ENTRADA-1`, Main and the desk can read and set a source's own `reglas.json` without
Carlos's sudo password -- before this, the only way was hand-editing the file on the server as `duna`.
Rules are not secret: `reglas.json` holds only sender addresses/domains and matching keywords, never a
credential.

```
sudo -n /usr/local/sbin/duna-deploy-buzon reglas-ver <fuente>
sudo -n /usr/local/sbin/duna-deploy-buzon reglas <fuente> --aplicar < reglas.json
```

`reglas-ver <fuente>` is read-only: it prints `fuentes/<fuente>/reglas.json` as-is. `reglas <fuente>
[--aplicar]` reads a CANDIDATE `reglas.json` from stdin (at most 64 KiB), validates it with this
connector's own loader (`duna_buzon.reglas.parsear_reglas`, run as `duna` via `python -m duna_buzon
validar-reglas` -- the one python subcommand that builds no `Config` and touches no env file, so this
never needs a secret), and prints a unified diff of the existing file against the candidate. Without
`--aplicar` it stops there, writing nothing. With `--aplicar`, it backs up the existing file to
`reglas.json.<UTC stamp>.bak` beside it, writes the new one atomically (temp file, then rename) as
duna:duna mode 0600, and logs one line via `logger -t duna-deploy-buzon` -- the source id, the old and
new file's sha256, and the backup's name, never the rules' own content. Example:

```
sudo -n /usr/local/sbin/duna-deploy-buzon reglas duna-zoho --aplicar < reglas.json
```

Takes effect on the next scheduled pass, or run one by hand with `systemd-run` as in `status`'s own
health check. Both subcommands refuse an unknown source, and `reglas` also refuses invalid JSON (with
the loader's own error) and stdin over the 64 KiB cap -- before writing anything.

## Setting a non-secret env variable (`env-set`)

Same slice: a narrow, passwordless way to read and change ONE allowlisted variable in
`/srv/duna/env/buzon.env` -- today only `BUZON_ENVIAR_ORIGEN` (see "Turning on `origen`" above).

```
sudo -n /usr/local/sbin/duna-deploy-buzon env-tiene <clave>
sudo -n /usr/local/sbin/duna-deploy-buzon env-set <clave> <valor> [--aplicar]
```

`env-tiene <clave>` works for ANY key shaped like an environment variable name, including a secret's own
name, and prints only `definida`/`no definida` -- never the value. `env-set <clave> <valor>
[--aplicar]` writes `/srv/duna/env/buzon.env`, but ONLY for a key listed in this repository's own
`deploy/host/env-permitidas.json` (installed by autoactualizar, mapping each allowed key to the one
regex its value must match -- seeded with exactly `BUZON_ENVIAR_ORIGEN` and pattern `^(0|1)$`), and a
hard, in-script denylist on the key's own NAME (any key containing `TOKEN`, `SECRET`, `PASSWORD`,
`CLAVE`, `_KEY`, `CIFRADO`, `CLIENT_ID`, `DSN` or `PRIVATE`) is checked FIRST and wins even over the
allowlist -- so a future allowlist entry that merely looks like a secret's name can never be used to
write one through this path. Dry-run by default: prints the current and the proposed value (safe to
show, since only an allowlisted, denylist-cleared key ever reaches this point). `--aplicar` backs up
`buzon.env` to `buzon.env.<UTC stamp>.bak`, changes EXACTLY the one matching line (or appends it if the
key was not set before) atomically, and logs one line via `logger -t duna-deploy-buzon` naming the key
and the backup -- never the value. No restart is needed: the timer's next scheduled pass reads the file
fresh.

## Trust boundary

Whoever can push to this repository's `main` can run code as root on this host, as today -- this
script only changes WHO can trigger that (`carlos`, with no password, instead of needing one each
time), never WHAT a deploy is allowed to do.
