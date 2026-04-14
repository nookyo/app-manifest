"""DD ↔ AMv2 public API.

Re-exports the two conversion functions and their testable helpers.
Implementation lives in:
  - _dd_to_amv2.py   (DD → AMv2)
  - _amv2_to_dd.py   (AMv2 → DD)
"""

from app_manifest.services._dd_to_amv2 import (
    convert_dd_to_amv2,
    _full_chart_name_to_helm_ref,
)
from app_manifest.services._amv2_to_dd import (
    convert_amv2_to_dd,
    _purl_to_docker_artifact_ref,
    _purl_to_helm_artifact_ref,
)

__all__ = [
    "convert_dd_to_amv2",
    "convert_amv2_to_dd",
    "_full_chart_name_to_helm_ref",
    "_purl_to_docker_artifact_ref",
    "_purl_to_helm_artifact_ref",
]
