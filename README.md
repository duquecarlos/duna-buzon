# duna-buzon

The mailbox connector: it reads one or more mailboxes -- today Carlos's Hotmail via Microsoft Graph, read-only --
and posts the financial items it finds to Duna Finanzas' inbox (buzon-v1, `POST http://127.0.0.1:8010/buzon/entradas`).
Runs on duna-prod-1 as `duna`. Nothing is exposed to the internet.

Created by Main (dev-protocol) on 2026-11-06, ORCH-BUZON-REPO-1. Built end to end by BUZON-CONECTOR-1; made
multi-source by BUZON-FUENTES-ESQUEMA-1 -- see `CLAUDE.md` for the gate and the invariants, `DECISIONS.md` for the
ledger, and `deploy/host/README.md` for the operator steps.

## Shape

A `uv`-managed Python 3.13 package, `duna_buzon`, run as `uv run python -m duna_buzon <comando>`.

Every mailbox this connector reads is a SOURCE, independent of every other: its own credential, its own sync
cursor, its own matching rules, under `$BUZON_DATOS/fuentes/<id>/`:

```
fuentes/<id>/fuente.json      -- {id, proveedor, direccion, etiqueta, token_finanzas_ref, activa}
fuentes/<id>/credencial.bin   -- the signed-in account's token cache, AES-256-GCM encrypted
fuentes/<id>/cursor.json      -- this source's own sync cursor
fuentes/<id>/reglas.json      -- this source's own matching rules (never committed; see `reglas.py`)
fuentes/<id>/estado.json      -- this source's own last-pass summary line
```

`proveedor` is `"microsoft"` (Microsoft Graph, read-only, device-code sign-in) today; `"imap"` is the next slice.
Every entry a source posts to Finanzas carries its own identity in the existing wire field `fuente`, as
`"<proveedor>:<id>"` (e.g. `"microsoft:hotmail-carlos"`) -- never a new wire field.

The commands:

- `entrar [--fuente <id>]` -- signs in to ONE Microsoft source, by device code, in Carlos's own browser. No password
  ever passes through this program. `--fuente` can be omitted while exactly one source is configured.
- `sincronizar` -- one pass over every ACTIVE source, independently: pulls new mail, decides which messages match
  that source's own rules, builds and POSTs the matches to Finanzas, advances that source's own cursor past
  whatever finished. One source failing (expired auth, a network error) logs its id and stop reason and does not
  stop the others; the pass exits non-zero if any source failed. Meant to run every 10 minutes under
  `duna-buzon.timer` on the host.
- `estado` -- prints every source's own cursor and last-pass counts. Nothing personal.
- `fuentes` -- lists every configured source (id, provider, address, label, active/inactive). No secrets.

The FIRST time any command runs against a data directory still in the single-mailbox layout
(`cache.bin`/`cursor.json`/`reglas.json`/`estado.json` directly under `$BUZON_DATOS`), it migrates itself
automatically and idempotently into `fuentes/hotmail-carlos/`, preserving the exact same cursor -- no mail is
re-sent, none is skipped. See `deploy/host/README.md` for what this means operationally.

Configuration comes from the environment only: `BUZON_GRAPH_CLIENT_ID`, `BUZON_CLAVE_CIFRADO`,
`FINANZAS_BUZON_TOKEN`, `BUZON_DATOS` (default `/srv/duna/data/buzon`), `BUZON_FINANZAS_URL` (default
`http://127.0.0.1:8010`, refused if it is not loopback). These stay global, shared by every source -- there is one
person and one Finanzas token today (`token_finanzas_ref` in each `fuente.json` already names which one, ready for
when that changes).

## Gate

```
uv run pytest
```
