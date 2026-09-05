"""Durable local exclusion; ambiguous attempts are never automatically retried."""
import json
import os
import re
from pathlib import Path
import time
import uuid

from .contract import Refused, read_bounded, decode, sha


def write_new(path, value):
    with Path(path).open('x', encoding='utf-8') as handle:
        json.dump(value, handle, ensure_ascii=False, separators=(',', ':'))
        handle.flush()
        os.fsync(handle.fileno())


class Attempt:
    def __init__(self, directory, node, blob):
        self.root = Path(directory)
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.root.is_symlink():
            raise Refused('IO_FAILED')
        self.key = node + '-' + sha(blob)
        self.reservation = self.root / (self.key + '.reserved.json')
        self.result = self.root / (self.key + '.result.json')
        self.lock = self.root / 'node.busy.json'
        self.id = uuid.uuid4().hex

    def cached(self):
        if self.result.exists():
            reservation = decode(read_bounded(self.reservation))
            identity = reservation.get('attempt_id', '')
            if not re.fullmatch('[0-9a-f]{32}', identity) or reservation.get('request_key') != self.key:
                raise Refused('IO_FAILED')
            journal = decode(read_bounded(self.root / (identity + '.runtime.json')))
            blob = read_bounded(self.result, 16_384)
            if journal.get('attempt_id') != identity or journal.get('result_sha256') != sha(blob):
                raise Refused('IO_FAILED')
            return decode(blob, 16_384)
        if self.reservation.exists():
            raise Refused('UNAVAILABLE_NODE')
        return None

    def claim(self):
        record = {'attempt_id': self.id, 'request_key': self.key, 'created_unix': time.time(),
                  'state': 'RESERVED_OR_AMBIGUOUS'}
        try:
            write_new(self.lock, record)
        except FileExistsError:
            raise Refused('UNAVAILABLE_NODE') from None
        try:
            write_new(self.reservation, record)
        except BaseException:
            self.lock.unlink()
            raise

    def finish(self, result, runtime, release):
        # Stage then rename: a crash never exposes half a cached result. Keep
        # reservation forever; failed persistence deliberately keeps the lock.
        temporary = self.root / (self.id + '.result.pending')
        write_new(temporary, result)
        write_new(self.root / (self.id + '.runtime.json'),
                  {**runtime, 'result_sha256': sha(read_bounded(temporary, 16_384))})
        os.replace(temporary, self.result)
        if release:
            self.lock.unlink()
