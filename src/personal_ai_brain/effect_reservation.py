"""Durable host-side, single-use effect reservations; no new task queue."""
import json
import os
from pathlib import Path
import re
import stat

from .approved_effect import EffectDenied, EffectUncertain

class EffectReservations:
    def __init__(self, directory: Path):
        info = directory.lstat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid()
                or info.st_mode & 0o077):
            raise EffectDenied('effect_denied')
        self.directory = directory

    def __call__(self, request_id, plan_hash):
        if (not isinstance(request_id, str) or not re.fullmatch(
                r'[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}', request_id)
                or not isinstance(plan_hash, str) or not re.fullmatch(r'[0-9a-f]{64}', plan_hash)):
            raise EffectDenied('effect_denied')
        directory = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            info = os.fstat(directory)
            if info.st_uid != os.geteuid() or info.st_mode & 0o077:
                raise EffectDenied('effect_denied')
            name = request_id + '.json'
            try:
                fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600, dir_fd=directory)
            except FileExistsError:
                # Never reclaim a partial or already used reservation. Even
                # identical replay is uncertain at this lower boundary.
                raise EffectUncertain('effect_outcome_uncertain') from None
            try:
                with os.fdopen(fd, 'w', encoding='ascii') as stream:
                    json.dump({'request_id':request_id, 'plan_hash':plan_hash}, stream)
                    stream.write('\n')
                    stream.flush()
                    os.fsync(stream.fileno())
                os.fsync(directory)
            except OSError:
                raise EffectUncertain('effect_outcome_uncertain') from None
        finally:
            os.close(directory)
