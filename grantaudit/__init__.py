import sys

if sys.version_info < (3, 10):
    raise RuntimeError(
        f"GrantAudit needs Python 3.10+, but this is Python {sys.version.split()[0]} ({sys.executable}). "
        "Create a venv with a newer interpreter, e.g.: python3.12 -m venv .venv && source .venv/bin/activate"
    )
