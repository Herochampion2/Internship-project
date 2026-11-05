"""Automated Fleet Compliance and Expiry Alert Pipeline.

Backend utility built during the summer training at Lawyered (Sproutech
Solutions Private Limited), 8 June 2026 - 27 July 2026.

Public API::

    from fleet_compliance import main, PipelineConfig
"""

from fleet_compliance.pipeline import (
    CRITICAL_THRESHOLD_DAYS,
    PipelineConfig,
    add_compliance_status,
    append_critical_to_txt,
    dispatch_api_alert,
    export_json,
    load_csv_file,
    load_excel_file,
    main,
    merge_and_clean,
)

__version__ = "1.0.0"
__author__ = "Yash Aggarwal"

__all__ = [
    "CRITICAL_THRESHOLD_DAYS",
    "PipelineConfig",
    "__version__",
    "add_compliance_status",
    "append_critical_to_txt",
    "dispatch_api_alert",
    "export_json",
    "load_csv_file",
    "load_excel_file",
    "main",
    "merge_and_clean",
]
