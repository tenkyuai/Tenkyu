"""Tenkyu: stop free-tier and credit farming in AI apps."""
from .decision_log import DecisionLog
from .guard import ALLOW, BLOCK, REVIEW, Decision, Guard, Signup, ip_key
from .http import client_ip, signup_from_request
from .signals import Signal, load_domains, normalize_email
from .store import BlindStore, MemoryStore

__all__ = ["DecisionLog", "Guard", "Signup", "Decision", "Signal", "MemoryStore", "BlindStore", "SQLiteStore", "PostgresStore", "normalize_email", "ip_key", "load_domains", "signup_from_request", "client_ip", "ALLOW", "REVIEW", "BLOCK"]
__version__ = "0.3.0"


def __getattr__(name):
    # SQLiteStore is imported on first use, so `import tenkyu` works on Pythons built without sqlite3
    # (Pyodide, some slim containers) as long as the SQLite store is not used.
    if name == "SQLiteStore":
        from .sqlite_store import SQLiteStore
        return SQLiteStore
    # PostgresStore needs the optional psycopg driver (pip install "tenkyu[postgres]"), which it imports itself
    if name == "PostgresStore":
        from .postgres_store import PostgresStore
        return PostgresStore
    raise AttributeError(f"module 'tenkyu' has no attribute {name!r}")
