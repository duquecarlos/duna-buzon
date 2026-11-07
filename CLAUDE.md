# duna-buzon

The mailbox connector. It reads Carlos's Hotmail READ-ONLY through Microsoft Graph and posts the
financial items it finds to Duna Finanzas' inbox, buzon-v1: `POST http://127.0.0.1:8010/buzon/entradas`.
It runs on `duna-prod-1` as `duna`, with nothing exposed to the internet. See `README.md` for the shape
and `deploy/host/README.md` for the operator steps.

## Gate

```
uv run pytest
```

One command, no network beyond what `uv` itself needs to resolve packages from PyPI. Every test fakes
Graph and Finanzas with `httpx.MockTransport` and fakes MSAL behind a seam (`auth.py`'s
`construir_app`) -- nothing in this suite reaches the internet, a real Microsoft account, or a real
Finanzas instance.

## Invariants (this repository's own, never relaxed by a later slice)

1. **The mailbox is read-only.** Every call this program makes to Microsoft Graph is a GET --
   `src/duna_buzon/graph.py`'s `GraphClient` has no `post`/`patch`/`delete` method anywhere in it, by
   construction, and `tests/test_graph.py::test_todas_las_llamadas_a_graph_son_get` proves it from the
   outside. No move, delete, flag, mark-read, or send, ever.
2. **Logs say counts, never content.** `src/duna_buzon/registro.py`'s one summary line per pass carries
   only integers. No sender, subject, body, filename, attachment byte, token, cache, or Graph message id
   is ever printed or logged anywhere in this codebase --
   `tests/test_registro_marcador.py::test_el_marcador_no_aparece_en_stdout_ni_en_los_logs` proves it with
   a unique marker planted in all four of those places at once.
3. **Finanzas is reached over loopback only.** `src/duna_buzon/config.py`'s `Config.desde_entorno`
   refuses any `BUZON_FINANZAS_URL` that is not an `http://` URL on `127.0.0.1`/`::1`/`localhost` --
   before this program ever builds an HTTP client.
4. **Which messages get forwarded is DATA, not code.** `src/duna_buzon/reglas.py` holds the matching
   logic; `$BUZON_DATOS/reglas.json` holds every real sender, bank, and employer name. No real address,
   domain, or name belonging to a real person or institution ever appears in this repository's own
   source, tests, or commit history ("template, not tenant") -- `deploy/reglas.example.json` ships only
   generic placeholders.
5. **The token cache and the cursor are the only state on disk, and both are protected.**
   `$BUZON_DATOS/cache.bin` is AES-256-GCM-encrypted under `BUZON_CLAVE_CIFRADO` (`src/duna_buzon/
   cripto.py`), a fresh nonce every write; the data directory is mode 0700 and every file in it is mode
   0600 where the OS supports that (skipped, with a reason, on Windows). The cursor
   (`$BUZON_DATOS/cursor.json`) never advances past a message a pass could not finish because of a
   failure -- only past one that was stored or deliberately, permanently skipped.
6. **A missing environment variable is named, never its value.** `src/duna_buzon/config.py`'s
   `ConfigError` always names the variable; no caught exception anywhere in this codebase prints a
   secret's value.

## Tier 1 (a change here needs the investigation-first discipline)

- `src/duna_buzon/auth.py` -- sign-in and the encrypted token cache.
- `src/duna_buzon/reglas.py` -- the selection rules (which messages get read and forwarded at all).
- `src/duna_buzon/finanzas.py` -- the Finanzas client (the wire is Finanzas' own contract; read it at a
  ref in `../finanzas/duna-finanzas-api` before changing anything here).
- `deploy/host/` -- the root-owned entry script and its sudoers fragment.
