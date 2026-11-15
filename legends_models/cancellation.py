# SPDX-License-Identifier: MIT
"""Cooperative launcher cancellation; publication finishes once it has begun."""
import os
from pathlib import Path


def requested():
    path = os.environ.get('LEGENDS_MODELS_CANCEL')
    return bool(path and Path(path).is_file())


def check():
    if requested():
        raise KeyboardInterrupt('cancelled by the launcher')
