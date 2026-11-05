"""Allow the package to be run with ``python -m fleet_compliance [options]``."""

from __future__ import annotations

from fleet_compliance.cli import run

if __name__ == "__main__":
    raise SystemExit(run())
