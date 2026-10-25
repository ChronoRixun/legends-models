# SPDX-License-Identifier: MIT
"""Progress output: human text, or one JSON object per line (--json) for the launcher."""
from __future__ import annotations

import json
import sys
import time


API_VERSION = 1  # bump on any incompatible change to the JSON-lines events (LAUNCHER-API.md)


class Reporter:
    def __init__(self, json_mode=False, stream=None):
        self.json = json_mode
        self.stream = stream or sys.stdout
        self.started = time.monotonic()

    def emit(self, event, **data):
        if self.json:
            record = {'event': event, 'api': API_VERSION, 't': round(time.monotonic() - self.started, 3), **data}
            self.stream.write(json.dumps(record, sort_keys=False, default=str) + '\n')
        else:
            text = data.get('message') or ' '.join(f'{k}={v}' for k, v in data.items() if not isinstance(v, (dict, list)))
            self.stream.write(f'[{event}] {text}\n')
        self.stream.flush()

    def stage(self, stage, message='', **data):
        self.emit('stage', stage=stage, message=message or stage, **data)

    def progress(self, done, total, message='', **data):
        self.emit('progress', done=done, total=total, message=message or f'{done}/{total}', **data)

    def warn(self, message, **data):
        self.emit('warning', message=message, **data)

    def error(self, message, code, **data):
        self.emit('error', message=message, code=code, **data)

    def result(self, payload):
        if self.json:
            self.emit('result', **payload)
        else:
            self.stream.write(json.dumps(payload, indent=2, default=str) + '\n')
            self.stream.flush()
