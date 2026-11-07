# duna-buzon

The mailbox connector: it reads ONE person's mailbox (Carlos's Hotmail, via Microsoft Graph, read-only) and posts the
financial items it finds to Duna Finanzas' inbox (buzon-v1, `POST http://127.0.0.1:8010/buzon/entradas`). Runs on
duna-prod-1 as `duna`. Nothing is exposed to the internet.

Created by Main (dev-protocol) on 2026-11-06, ORCH-BUZON-REPO-1. Built end to end by BUZON-CONECTOR-1 -- see
`CLAUDE.md` for the gate and the invariants, `DECISIONS.md` for the ledger, and `deploy/host/README.md` for the
operator steps.

## Shape

A `uv`-managed Python 3.13 package, `duna_buzon`, run as `uv run python -m duna_buzon <comando>`:

- `entrar` -- Carlos signs in once, by device code, in his own browser. No password ever passes through this
  program.
- `sincronizar` -- one pass: pull new mail from Graph, decide which messages match a rule
  (`$BUZON_DATOS/reglas.json`), build and POST the matches to Finanzas, advance the cursor past whatever finished.
  Meant to run every 10 minutes under `duna-buzon.timer` on the host.
- `estado` -- prints the cursor and the last pass's counts. Nothing personal.

Configuration comes from the environment only: `BUZON_GRAPH_CLIENT_ID`, `BUZON_CLAVE_CIFRADO`,
`FINANZAS_BUZON_TOKEN`, `BUZON_DATOS` (default `/srv/duna/data/buzon`), `BUZON_FINANZAS_URL` (default
`http://127.0.0.1:8010`, refused if it is not loopback).

## Gate

```
uv run pytest
```
