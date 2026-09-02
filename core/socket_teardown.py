"""Cross-thread socket interruption that never frees a file descriptor.

Closing a socket from a thread that does not own it is unsafe in this
process. The owning thread may be inside ``SSL_read``/``SSL_write`` with the
raw descriptor number captured by OpenSSL. ``close()`` releases that number
immediately, the next ``open()`` anywhere in the process (typically a SQLite
store) receives the same number, and the still-running TLS call then reads
the database file as TLS record bytes and writes a TLS alert record into it.
That is the production corruption signature documented in
``docs/SQLITE_CORRUPTION_DIAGNOSTICS.md``.

``shutdown(SHUT_RDWR)`` interrupts the owner just as fast: pending reads
return EOF and pending writes fail, while the descriptor number stays
reserved until the owning thread closes the socket in its own ``finally``.
"""

from __future__ import annotations

import logging
import socket

logger = logging.getLogger(__name__)


def shutdown_socket(sock) -> bool:
    """Shut both directions of ``sock`` down without closing it.

    Returns True when the shutdown call succeeded. A missing socket, an
    object without ``shutdown``, or a socket that is already disconnected
    returns False; the owner thread will observe the failure itself.
    """
    if sock is None:
        return False
    shutdown = getattr(sock, "shutdown", None)
    if shutdown is None:
        return False
    try:
        shutdown(socket.SHUT_RDWR)
    except OSError as exc:
        logger.debug("socket shutdown skipped: %s", exc)
        return False
    return True


def abort_http_connection(conn) -> bool:
    """Interrupt an in-flight ``http.client`` connection owned by another thread.

    The streaming thread keeps ``conn`` and closes it in its own ``finally``;
    this helper only makes its blocked read or write return immediately.
    """
    if conn is None:
        return False
    return shutdown_socket(getattr(conn, "sock", None))


__all__ = ["abort_http_connection", "shutdown_socket"]
