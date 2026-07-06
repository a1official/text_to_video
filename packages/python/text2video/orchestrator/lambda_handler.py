from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from text2video.orchestrator.control_plane import lambda_handler as control_plane_lambda_handler


def lambda_handler(event: Mapping[str, Any], context: Any = None) -> dict[str, Any]:
    return control_plane_lambda_handler(event, context)
