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

The script (and the two systemd unit files it installs) change only when re-installed -- a `git pull`
on the repository does NOT update `/usr/local/sbin/duna-deploy-buzon` by itself. After changing
`deploy/host/duna-deploy-buzon` or its `.sudoers` fragment, re-run the exact same three lines above.
A change to `deploy/systemd/duna-buzon.service` or `.timer` takes effect the next time `deploy` runs
(it re-installs both unconditionally).

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
```

`status` is read-only: the owner's own `git` HEAD, both units' `systemctl is-active`, and a health run
of `python -m duna_buzon estado` under `systemd-run`. `deploy` pulls and `uv sync --frozen` as the
repository's owner, creates `/srv/duna/data/buzon` (mode 0700, owned by `duna`) if it does not already
exist -- and never deletes it -- installs `duna-buzon.service`/`duna-buzon.timer`, enables the timer,
and finishes with the same health check. `entrar` is the one subcommand for CARLOS HIMSELF, run
directly in his own SSH terminal (never through an automated, no-tty session): it signs in
interactively and prints Microsoft's own device-code URL to that terminal.

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

## Trust boundary

Whoever can push to this repository's `main` can run code as root on this host, as today -- this
script only changes WHO can trigger that (`carlos`, with no password, instead of needing one each
time), never WHAT a deploy is allowed to do.
