# SPDX-License-Identifier: MIT
"""Exit codes (stable contract for the launcher; see LAUNCHER-API.md)."""
OK = 0
INTERNAL = 1          # unexpected error (bug); traceback on stderr
USAGE = 2             # bad arguments
MISSING_INPUT = 3     # port / source install / community pack / helper not found
PARTIAL = 10          # pack written, but some requested characters were refused (gate) and stay stock
NOTHING_WRITTEN = 11  # every requested character was refused; previous pack left untouched
DOWNLOAD = 20         # community download failed or hash/allow-list check refused the archive
VERIFY_MISMATCH = 30  # verify found missing/changed files
STALE = 31            # verify: files intact, but the port was rebuilt since the pack was made (offer Rebuild)
UNSAFE = 40           # refused: output inside a source install, or a folder legends-models does not own
CANCELLED = 130


class ToolError(Exception):
    def __init__(self, message, code=INTERNAL, **data):
        super().__init__(message)
        self.code = code
        self.data = data
