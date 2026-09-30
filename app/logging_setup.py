"""
Minimal logging setup for the app.

Until now nothing configured logging, so a WARNING reached `docker logs` as bare
message text (Python's fallback handler): no time, no level, no source, and INFO
messages vanished. This gives every WARNING and above one line with a timestamp, the
level and the logger name.

The level is WARNING on purpose: failures are what must never be missed, and INFO from
libraries (every outgoing HTTP request) would bury them. It only adds a handler when the
root logger has none, so a host that configures logging itself (tests, uvicorn config)
is left alone.
"""
import logging

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def configure_logging() -> None:
    logging.basicConfig(level=logging.WARNING, format=LOG_FORMAT)
