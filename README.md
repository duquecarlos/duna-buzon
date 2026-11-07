# duna-buzon

The mailbox connector: it reads ONE person's mailbox (Carlos's Hotmail, via Microsoft Graph, read-only) and posts the
financial items it finds to Duna Finanzas' inbox (buzon-v1, `POST http://127.0.0.1:8010/buzon/entradas`). Runs on
duna-prod-1 as `duna`. Nothing is exposed to the internet.

Created by Main (dev-protocol) on 2026-11-06, ORCH-BUZON-REPO-1. The CLAUDE.md file, the Tier 1 list and the ledger
arrive with its first worker (BUZON-CONECTOR-1).
