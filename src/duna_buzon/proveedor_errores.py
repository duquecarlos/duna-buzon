"""Provider-agnostic failure categories every mailbox provider's own
exceptions sit under, so `sincronizar.py`'s one pass-execution loop can
react to a failure without knowing which provider raised it. A provider's
own exception type still carries everything specific to it (an HTTP
status, an IMAP step, ...) in its own message; this module only names the
two buckets `ejecutar_pasada` reacts to.
"""

from __future__ import annotations


class ProveedorAutenticacionError(Exception):
    """The provider's own credential or token is no longer good. The pass
    stops; the cursor does not advance past what was not finished."""


class ProveedorErrorTransitorio(Exception):
    """A transient failure talking to the provider -- connection, timeout,
    or the provider's own equivalent of a 5xx. The pass stops; the next
    scheduled run retries."""
