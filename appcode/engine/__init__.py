"""
The server-side engine: every thread and every piece of state lives here.

pytincture re-executes BFF modules (``services/*_api.py``) on every call, so
nothing long-lived may live in them. They import from ``engine`` inside their
methods instead, and ``engine.boot.start_engine()`` (called once from
service.py) starts the threads. Nothing under ``ui/`` or the page may import
this package: it is never shipped to the browser (tests/test_isolation.py).
"""
