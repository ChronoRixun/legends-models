# SPDX-License-Identifier: MIT
"""legends-models: optional character-model mods for Legends Classic, built on the player's PC
from games the player owns. Never ships, downloads or bundles game assets."""
__version__ = '0.3.0'   # the numeric release base; a release tag may add -beta.N / -rc.N (tools/release_metadata.py)


def _frozen_version(base):
    """The full release version (e.g. 0.3.0-beta.1) of a frozen exe, from the build-info.json tools/freeze.py bundles."""
    import json
    import sys
    from pathlib import Path
    if not getattr(sys, 'frozen', False):
        return None
    try:
        info = json.loads((Path(__file__).resolve().parent / 'data' / 'build-info.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    version = info.get('version') if isinstance(info, dict) else None
    return version if isinstance(version, str) and (version == base or version.startswith(base + '-')) else None


__version__ = _frozen_version(__version__) or __version__
