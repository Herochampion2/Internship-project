#!/usr/bin/env python3
"""Convenience entry point: ``python run_pipeline.py``.

Equivalent to ``python -m fleet_compliance`` and to the ``fleet-compliance`` console
script. Run ``--help`` for the full list of switches.

A plain run needs no arguments and no data: the demo registers described in
Chapter 4 of the report are generated on the fly, classified against today's date
and written to ``output/``.
"""

from __future__ import annotations

from fleet_compliance.cli import run

if __name__ == "__main__":
    raise SystemExit(run())
