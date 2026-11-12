# duna-buzon

The mailbox connector: it reads one or more mailboxes -- today Carlos's Hotmail via Microsoft Graph, read-only --
and posts the financial items it finds to Duna Finanzas' inbox (buzon-v1, `POST http://127.0.0.1:8010/buzon/entradas`).
Runs on duna-prod-1 as `duna`. Nothing is exposed to the internet.

Created by Main (dev-protocol) on 2026-11-06, ORCH-BUZON-REPO-1. Built end to end by BUZON-CONECTOR-1; made
multi-source by BUZON-FUENTES-ESQUEMA-1; gained a generic IMAP provider by BUZON-FUENTE-IMAP-1, widened from
that provider's own `INBOX`-only start to every folder by BUZON-FUENTE-IMAP-CARPETAS-1, then narrowed back down
to a named allow-list for the shared-mailbox case by BUZON-IMAP-CARPETAS-INCLUIDAS-1; gained sources Carlos
(or, eventually, any user) connects through Cuenta Duna's own OAuth -- Gmail and Microsoft alike, with a
fresh token pulled every pass, never stored here -- by BUZON-FUENTES-PULL-1. See `CLAUDE.md` for the gate and
the invariants, `DECISIONS.md` for the ledger, and `deploy/host/README.md` for the operator steps.

## Shape

A `uv`-managed Python 3.13 package, `duna_buzon`, run as `uv run python -m duna_buzon <comando>`.

Every mailbox this connector reads is a SOURCE, independent of every other: its own credential (or, for a
Cuenta Duna-connected source, no stored credential at all), its own sync cursor, its own matching rules,
under `$BUZON_DATOS/fuentes/<id>/`:

```
fuentes/<id>/fuente.json           -- {id, proveedor, direccion, etiqueta, token_finanzas_ref, activa, host, port, origen,
                                       carpetas}
fuentes/<id>/credencial.bin        -- the signed-in account's own credential, AES-256-GCM encrypted (an MSAL token
                                       cache for "microsoft"; the app password itself for "imap") -- ABSENT for
                                       origen="cuenta" (see below): that source's token is never written to disk.
fuentes/<id>/cursor.json           -- this source's own sync cursor (a timestamp)
fuentes/<id>/cursor_proveedor.json -- this source's own provider-defined resume token, if it has one (IMAP's
                                       JSON object mapping folder name to that folder's own
                                       "<uidvalidity>:<uid>" pair; absent for Microsoft and Google)
fuentes/<id>/reglas.json           -- this source's own matching rules (never committed; see `reglas.py`)
fuentes/<id>/estado.json           -- this source's own last-pass summary line
```

`host`/`port` are only set for `proveedor == "imap"` (`None` otherwise).

`proveedor` is `"microsoft"` (Microsoft Graph), `"imap"` (a generic read-only IMAP mailbox over SSL, app-password
sign-in -- first used for `legal@duna.solutions` on Zoho) or `"google"` (Gmail, read through the Gmail API --
`gmail.py`). `origen` is `"local"` (the default -- Carlos ran `entrar`, this connector
holds its own credential: `microsoft` or `imap` only) or `"cuenta"` (the source is one Carlos connected through
Cuenta Duna's own OAuth -- `microsoft` or `google` only; see "Cuenta Duna sources" below). An IMAP source reads
every selectable folder, not only `INBOX` -- it skips a folder the server marks `\Trash`/`\Junk`/`\Sent`/`\Drafts`/
`\All`, or, when the server says nothing about it, a folder named like one of those (`cliente_imap.py`), the same
parity with the Microsoft source (which already reaches every folder via `/me/messages`) that
`BUZON-FUENTE-IMAP-CARPETAS-1` closed. An IMAP source can also be narrowed to only ONE of those folders, or a
named few -- `carpetas` below. Every entry every source
posts to Finanzas carries the wire's `fuente` field as the one constant value `"correo"` -- it is the CHANNEL, not
a source's own identity, and Finanzas rejects anything else (`Literal["correo", "sms", "notificacion"]`,
`src/duna_finanzas/web/buzon/schemas.py:37`). A source's own identity (`"<proveedor>:<id>"`,
`FuenteConfig.etiqueta_registro`) stays local to this connector's own logs and status output
(`BUZON-FUENTE-WIRE-CORREO-1`, correcting `BUZON-FUENTES-ESQUEMA-1`, which briefly sent it over the wire and would
have had every entry rejected with a `422`).

### Cuenta Duna sources (BUZON-FUENTES-PULL-1)

Carlos (or, eventually, any Duna user) can connect a mailbox through Cuenta Duna's own screen instead of running
`entrar` here -- Cuenta Duna owns that OAuth round trip end to end (`CUENTA-API-FUENTES-CONECTADAS-1`); this
connector never sees a refresh token or talks to Google/Microsoft's own OAuth endpoints for these sources. Every
`sincronizar` pass starts with ONE pull, `GET /servicio/fuentes/{sub}` (HTTP Basic, client `buzon`) against Cuenta
Duna, which returns every source still connected, each with a FRESH, short-lived access token --
`src/duna_buzon/cuenta.py`'s `CuentaClient`. That token lives in memory for the one pass that pulled it and is
NEVER written to disk anywhere, by invariant.

Each Cuenta-connected source is kept under `fuentes/cuenta-<id>/`, `<id>` being Cuenta Duna's own id for it --
`fuente.json` is rewritten on every successful pull (never a `credencial.bin`); `cursor.json`/`cursor_proveedor.json`/
`reglas.json` persist exactly like any local source's. The moment Cuenta Duna stops listing a source (revoked, or
the pull itself failed), that source is simply not run that pass -- a successful pull additionally marks it
INACTIVE, without touching its cursor or its rules, so a later reconnect resumes it unchanged. A failed or
unconfigured pull never stops the LOCAL sources: they run exactly as if Cuenta Duna did not exist.

`"google"` sources are read through a new Gmail API provider (`src/duna_buzon/gmail.py`), behind the same
provider interface `proveedor.py` already names for Graph/IMAP; `id_externo` is the RFC 822 `Message-ID` header,
exactly like the IMAP provider. `"microsoft"` sources pulled this way use the SAME `GraphClient` a local Microsoft
source uses, just with a Cuenta Duna-pulled token instead of this connector's own device-code sign-in.

The commands:

- `agregar-fuente imap --id <id> --direccion <direccion> --host <host> --token-finanzas <ref> [--port <puerto>]
  [--etiqueta <etiqueta>] [--carpeta <nombre> ...]` -- writes a new IMAP source's `fuente.json` (`--port` defaults to
  993). `--carpeta` is repeatable and sets `carpetas` (see below); omitted, the source reads every selectable folder,
  today's behaviour exactly. Never signs in -- `entrar --fuente <id>` is the separate next step.
- `entrar [--fuente <id>]` -- signs in to ONE LOCAL source. For a Microsoft source, by device code, in Carlos's own
  browser -- no password ever passes through this program. For an IMAP source, by reading its app password from
  STDIN with no echo (`getpass`, never a command-line argument) and verifying it with a real `LOGIN` before ever
  storing it, encrypted, in that source's own `credencial.bin`. `--fuente` can be omitted while exactly one source
  is configured. Refuses a `origen="cuenta"` source with a one-line error -- connect or reconnect those at Cuenta
  Duna's own screen, never here.
- `sincronizar` -- first pulls every Cuenta Duna-connected source (see above), then one pass over every ACTIVE
  source, independently: pulls new mail, decides which messages match that source's own rules, builds and POSTs
  the matches to Finanzas, advances that source's own cursor past whatever finished. One source failing (expired
  auth, a network error) logs its id and stop reason and does not stop the others; the pass exits non-zero if any
  source failed. Meant to run every 10 minutes under `duna-buzon.timer` on the host.
- `estado` -- prints every source's own cursor and last-pass counts. Nothing personal.
- `fuentes` -- lists every configured source (id, provider, address, label, active/inactive). No secrets.
- `carpetas <id>` -- read-only, for ONE imap source: signs in with its already-stored app password and prints
  every folder the server's `LIST` returns, with its flags and whether this source would read it today. Never
  prints message content or the credential itself -- see "Narrowing an IMAP source to specific folders" below.

### Narrowing an IMAP source to specific folders (BUZON-IMAP-CARPETAS-INCLUIDAS-1)

A `fuente.json` for `proveedor == "imap"` can carry an optional `carpetas` list: when present, this source reads
ONLY folders whose LIST name equals one of its entries or sits under one of them in the server's own hierarchy
(`entrada` + the server's own delimiter + anything) -- still minus Trash/Junk/Sent/Drafts/All-Mail, which are
never read regardless. Absent (the default, and every `fuente.json` written before this slice) means today's
behaviour exactly: every selectable folder. A non-list or an empty list is rejected as an invalid source when it
is loaded, not discovered later mid-sync.

This exists for the shared-mailbox case: `legal@duna.solutions`, `hola@duna.solutions` and
`soporte@duna.solutions` are not separate logins on Zoho -- they are SHARED MAILBOXES inside Carlos's own
`carlos@duna.solutions` login. Reading them means signing in as `carlos@` over IMAP, and `carlos@`'s own personal
folders (`INBOX`, `Sent`, whatever Carlos files privately) must never be read into the COMPANY's Finanzas just
because the same login happens to see them too. Setting `carpetas` to the shared mailbox's own top folder keeps
this source to exactly that:

```
agregar-fuente imap --id legal-duna --direccion carlos@duna.solutions --host imap.zoho.com \
  --token-finanzas carlos --carpeta "Legal"
```

Before the first sync (or after changing `carpetas`), `carpetas legal-duna` shows exactly what Zoho exposes and
under what name, without touching the cursor or sending anything to Finanzas:

```
$ uv run python -m duna_buzon carpetas legal-duna
carpeta='INBOX' banderas=- leida=no
carpeta='Legal' banderas=- leida=si
carpeta='Legal/Facturas' banderas=- leida=si
carpeta='Sent' banderas=\Sent leida=no
```

Changing `carpetas` on an existing source takes effect the next `sincronizar` pass: a folder's own stored cursor
(`cursor_proveedor.json`) is kept exactly where it was while that folder stays included, and dropped the moment it
is not -- never carried forward silently for a folder this source no longer reads.

The FIRST time any command runs against a data directory still in the single-mailbox layout
(`cache.bin`/`cursor.json`/`reglas.json`/`estado.json` directly under `$BUZON_DATOS`), it migrates itself
automatically and idempotently into `fuentes/hotmail-carlos/`, preserving the exact same cursor -- no mail is
re-sent, none is skipped. See `deploy/host/README.md` for what this means operationally.

Configuration comes from the environment only: `BUZON_GRAPH_CLIENT_ID`, `BUZON_CLAVE_CIFRADO`,
`FINANZAS_BUZON_TOKEN`, `BUZON_DATOS` (default `/srv/duna/data/buzon`), `BUZON_FINANZAS_URL` (default
`http://127.0.0.1:8010`, refused if it is not loopback). These stay global, shared by every source -- there is one
person and one Finanzas token today (`token_finanzas_ref` in each `fuente.json` already names which one, ready for
when that changes).

Four more, for Cuenta Duna sources (`BUZON-FUENTES-PULL-1`), OPTIONAL AS A GROUP -- a deployment that has not
connected any source through Cuenta Duna yet sets none of them, and this connector works exactly as before, pulling
nothing: `BUZON_CUENTA_URL` (Cuenta Duna's own base URL -- `http://` or `https://`, no loopback requirement, since
Cuenta Duna is a separate service, not this connector's own loopback peer like Finanzas), `BUZON_CUENTA_CLIENT_ID` /
`BUZON_CUENTA_CLIENT_SECRET` (this connector's own HTTP Basic credentials for Cuenta Duna's `servicio` client,
product `buzon`), `BUZON_CUENTA_SUB` (the person's own Cuenta Duna `sub`). The moment ANY one of these four is set,
all four are required -- a half-configured group is refused by name at startup, never silently treated as "not
configured".

One more, `BUZON_ENVIAR_ORIGEN` (`BUZON-ORIGEN-WIRE-1`) -- **turn on only after Finanzas Release 37 is live.** Off
unless set to exactly `1` or `true`. On, every entry's `meta` carries an `origen` field: the source's own identity
(`fuentes.FuenteConfig.etiqueta_registro`, `"<proveedor>:<id>"`, e.g. `imap:legal-duna` or
`google:cuenta-<uuid>`) -- never a credential, and distinct from the wire's `fuente` field, which stays `"correo"`
regardless. Off (the default), the request is byte-identical to today's. Finanzas merged the `origen` field
(`FIN-API-BUZON-ORIGEN-1`) but its `EntradaBuzonMeta` schema is `extra="forbid"`, so sending it before Release 37
deploys gets every entry refused with a `422` -- hence off by default. A manual (`agregar-fuente`) source id over
48 characters is refused at add time; any label that still exceeds the wire's own 64-character cap at send time
(only possible for a Cuenta-pulled source, never one this connector adds itself) is logged and sent without
`origen`, rather than losing the entry.

## Gate

```
uv run pytest
```
