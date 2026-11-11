# duna-buzon

The mailbox connector: it reads one or more mailboxes -- today Carlos's Hotmail via Microsoft Graph, read-only --
and posts the financial items it finds to Duna Finanzas' inbox (buzon-v1, `POST http://127.0.0.1:8010/buzon/entradas`).
Runs on duna-prod-1 as `duna`. Nothing is exposed to the internet.

Created by Main (dev-protocol) on 2026-11-06, ORCH-BUZON-REPO-1. Built end to end by BUZON-CONECTOR-1; made
multi-source by BUZON-FUENTES-ESQUEMA-1; gained a generic IMAP provider by BUZON-FUENTE-IMAP-1 -- see `CLAUDE.md`
for the gate and the invariants, `DECISIONS.md` for the ledger, and `deploy/host/README.md` for the operator steps.

## Shape

A `uv`-managed Python 3.13 package, `duna_buzon`, run as `uv run python -m duna_buzon <comando>`.

Every mailbox this connector reads is a SOURCE, independent of every other: its own credential, its own sync
cursor, its own matching rules, under `$BUZON_DATOS/fuentes/<id>/`:

```
fuentes/<id>/fuente.json           -- {id, proveedor, direccion, etiqueta, token_finanzas_ref, activa, host, port}
fuentes/<id>/credencial.bin        -- the signed-in account's own credential, AES-256-GCM encrypted (an MSAL token
                                       cache for "microsoft"; the app password itself for "imap")
fuentes/<id>/cursor.json           -- this source's own sync cursor (a timestamp)
fuentes/<id>/cursor_proveedor.json -- this source's own provider-defined resume token, if it has one (IMAP's
                                       "<uidvalidity>:<uid>"; absent for Microsoft)
fuentes/<id>/reglas.json           -- this source's own matching rules (never committed; see `reglas.py`)
fuentes/<id>/estado.json           -- this source's own last-pass summary line
```

`host`/`port` are only set for `proveedor == "imap"` (`None` for Microsoft).

`proveedor` is `"microsoft"` (Microsoft Graph, read-only, device-code sign-in) or `"imap"` (a generic read-only IMAP
mailbox over SSL, app-password sign-in -- first used for `legal@duna.solutions` on Zoho). Every entry every source
posts to Finanzas carries the wire's `fuente` field as the one constant value `"correo"` -- it is the CHANNEL, not
a source's own identity, and Finanzas rejects anything else (`Literal["correo", "sms", "notificacion"]`,
`src/duna_finanzas/web/buzon/schemas.py:37`). A source's own identity (`"<proveedor>:<id>"`,
`FuenteConfig.etiqueta_registro`) stays local to this connector's own logs and status output
(`BUZON-FUENTE-WIRE-CORREO-1`, correcting `BUZON-FUENTES-ESQUEMA-1`, which briefly sent it over the wire and would
have had every entry rejected with a `422`).

The commands:

- `agregar-fuente imap --id <id> --direccion <direccion> --host <host> --token-finanzas <ref> [--port <puerto>]
  [--etiqueta <etiqueta>]` -- writes a new IMAP source's `fuente.json` (`--port` defaults to 993). Never signs in --
  `entrar --fuente <id>` is the separate next step.
- `entrar [--fuente <id>]` -- signs in to ONE source. For a Microsoft source, by device code, in Carlos's own
  browser -- no password ever passes through this program. For an IMAP source, by reading its app password from
  STDIN with no echo (`getpass`, never a command-line argument) and verifying it with a real `LOGIN` before ever
  storing it, encrypted, in that source's own `credencial.bin`. `--fuente` can be omitted while exactly one source
  is configured.
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
