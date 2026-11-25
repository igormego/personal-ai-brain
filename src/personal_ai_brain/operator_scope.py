"""Operator-owned scope grants for the existing offline Work Runner.

No caller-selected host paths, network, shell service, keys or sudo. Root
provisions directories and policy outside Git. A write grant permits arbitrary
changes *inside that directory*; it must never be an unreviewed production mount.
"""
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import stat

POLICY = Path('/etc/personal-ai-brain/builder/operator-scopes.json')
ROOT = Path('/var/lib/personal-ai-brain/operator-grants')
MAX_POLICY = 16384

class ScopeDenied(ValueError):
    pass

def validate_selector(scope_id, access):
    if (type(scope_id) is not str or not re.fullmatch(r'[a-z][a-z0-9-]{0,47}', scope_id)
            or type(access) is not str or access not in ('read', 'write')):
        raise ScopeDenied('invalid_scope_selector')

def _protected(path, *, directory):
    """All ancestors must be real root-owned, non-writable directories."""
    if not path.is_absolute() or '..' in path.parts:
        raise ScopeDenied('invalid_policy_path')
    for parent in reversed(path.parents):
        info = parent.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise ScopeDenied('untrusted_scope_ancestor')
    info = path.lstat()
    kind = stat.S_ISDIR if directory else stat.S_ISREG
    if not kind(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise ScopeDenied('untrusted_scope_path')
    return info

def _read_policy(path):
    _protected(path, directory=False)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0
                or info.st_mode & 0o022 or info.st_nlink != 1 or info.st_size > MAX_POLICY):
            raise ScopeDenied('invalid_scope_policy')
        raw = stream.read(MAX_POLICY + 1)
    if len(raw) > MAX_POLICY:
        raise ScopeDenied('invalid_scope_policy')
    return json.loads(raw, object_pairs_hook=_unique)

def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ScopeDenied('duplicate_scope_policy_key')
        result[key] = value
    return result

@dataclass(frozen=True)
class ScopeGrant:
    scope_id: str
    access: str
    read_dir: Path
    write_dir: Path | None
    preview: bool

    def mount_arguments(self):
        args = ['--ro-bind', str(self.read_dir), '/workspace/resources']
        if self.write_dir is not None:
            args.extend(['--bind' if self.access == 'write' else '--ro-bind',
                         str(self.write_dir), '/workspace/changes'])
        return args

def load_scope(scope_id, access, *, policy_path=POLICY, root=ROOT):
    validate_selector(scope_id, access)
    try:
        policy = _read_policy(policy_path)
        if (type(policy) is not dict or set(policy) != {'schema_version', 'scopes'}
                or policy['schema_version'] != '0.1' or type(policy['scopes']) is not dict
                or len(policy['scopes']) > 64):
            raise ScopeDenied('invalid_scope_policy')
        for key, spec in policy['scopes'].items():
            validate_selector(key, 'read')
            if (type(spec) is not dict or set(spec) != {'write', 'preview'}
                    or any(type(v) is not bool for v in spec.values())):
                raise ScopeDenied('invalid_scope_policy')
        spec = policy['scopes'].get(scope_id)
        if spec is None or (access == 'write' and not spec['write']):
            raise ScopeDenied('scope_not_granted')
        scope = root / scope_id
        read_dir = scope / 'read'
        _protected(read_dir, directory=True)
        write_dir = None
        if spec['write']:
            write_dir = scope / 'write'
            _protected(scope, directory=True)
            info = write_dir.lstat()
            if (not stat.S_ISDIR(info.st_mode) or info.st_uid != 0
                    or info.st_gid != os.getegid() or info.st_mode & 0o007
                    or info.st_mode & 0o070 != 0o070):
                raise ScopeDenied('untrusted_write_directory')
        return ScopeGrant(scope_id, access, read_dir, write_dir, spec['preview'])
    except (OSError, ValueError, TypeError, RecursionError) as error:
        if isinstance(error, ScopeDenied):
            raise
        raise ScopeDenied('invalid_scope_policy') from None
