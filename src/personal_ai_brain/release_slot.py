"""Activate immutable, operator-reviewed releases; no command execution.

The model selects an existing numeric generation via the ordinary effect
contract. A policy digest pins every approved release manifest. Only the
service-owned current pointer changes; existing generations remain available
for a separately approved rollback.
"""
from __future__ import annotations
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import uuid

from .effect_plan import EffectGrant
from .approved_effect import EffectDenied

MAX_FILES = 128
MAX_BYTES = 32 * 1024 * 1024
MAX_MANIFEST = 32768
HASH = r'[0-9a-f]{64}'
NAME = r'[a-z][a-z0-9-]{0,39}'
PART = r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}'

def deny():
    raise EffectDenied('effect_denied')

def unique(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            deny()
        out[key] = value
    return out

def parse_json(raw):
    return json.loads(raw, object_pairs_hook=unique,
                      parse_constant=lambda _: deny())

def check_dir(path, owner, *, private=False):
    info = path.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != owner
            or info.st_mode & (0o077 if private else 0o022)):
        deny()

def immutable_file(path, owner, limit):
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), 'rb') as f:
        info = os.fstat(f.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != owner or info.st_nlink != 1
                or info.st_mode & 0o6022 or info.st_size > limit):
            deny()
        data = f.read(limit+1)
    if len(data) > limit:
        deny()
    return data

def verify_release(directory, manifest_hash, owner):
    check_dir(directory, owner)
    raw = immutable_file(directory/'manifest.json', owner, MAX_MANIFEST)
    if hashlib.sha256(raw).hexdigest() != manifest_hash:
        deny()
    manifest = parse_json(raw)
    if (type(manifest) is not dict or set(manifest) != {'schema_version','files'}
            or manifest['schema_version'] != '0.1' or type(manifest['files']) is not dict
            or not 1 <= len(manifest['files']) <= MAX_FILES):
        deny()
    files = manifest['files']
    if any(type(p) is not str or not re.fullmatch(PART+r'(?:/'+PART+r')*',p)
           or p == 'manifest.json' or type(h) is not str or not re.fullmatch(HASH,h)
           for p,h in files.items()):
        deny()
    seen = set()
    total = 0
    def walk_error(_):
        deny()
    for root, directories, names in os.walk(directory, followlinks=False, onerror=walk_error):
        root = Path(root)
        check_dir(root, owner)
        # A bounded directory tree also rejects hidden/unmanifested directories.
        for name in directories:
            child = root/name
            check_dir(child, owner)
            rel = child.relative_to(directory).as_posix()+'/'
            if not any(p.startswith(rel) for p in files):
                deny()
        for name in names:
            path = root/name
            rel = path.relative_to(directory).as_posix()
            if rel == 'manifest.json':
                continue
            if rel not in files or rel in seen:
                deny()
            data = immutable_file(path, owner, MAX_BYTES-total)
            total += len(data)
            if hashlib.sha256(data).hexdigest() != files[rel]:
                deny()
            seen.add(rel)
    if seen != set(files):
        deny()

class ReleaseSlots:
    def __init__(self, policy, release_root: Path, state_root: Path, *, trusted_uid=0):
        if (type(policy) is not dict or set(policy) != {'schema_version','slots'}
                or policy['schema_version'] != '0.1' or type(policy['slots']) is not dict
                or not 1 <= len(policy['slots']) <= 32):
            deny()
        check_dir(release_root, trusted_uid)
        check_dir(state_root, os.geteuid(), private=True)
        self.release_root = release_root
        self.state_root = state_root
        self.trusted_uid = trusted_uid
        self.slots = {}
        self.grants = {}
        digest = hashlib.sha256(json.dumps(policy,sort_keys=True,separators=(',',':'),
                                           ensure_ascii=True).encode()).hexdigest()
        for name, slot in policy['slots'].items():
            if (type(name) is not str or not re.fullmatch(NAME,name)
                    or type(slot) is not dict or set(slot) != {'target_id','generations'}
                    or type(slot['target_id']) is not int or not 1 <= slot['target_id'] <= 2147483647
                    or type(slot['generations']) is not dict or not 1 <= len(slot['generations']) <= 32):
                deny()
            versions = slot['generations']
            for generation, hash_value in versions.items():
                if (type(generation) is not str or not re.fullmatch(r'[1-9][0-9]{0,9}',generation)
                        or int(generation)>2147483647 or type(hash_value) is not str
                        or not re.fullmatch(HASH,hash_value)):
                    deny()
            resource = 'release/'+name
            self.slots[resource] = (name,slot['target_id'],dict(versions))
            self.grants[resource] = EffectGrant(resource,'activate_release','owner',
                                                'generation','positive_int',digest)

    def _slot(self, resource, target):
        item = self.slots.get(resource)
        if item is None or type(target) is not int or item[1] != target:
            deny()
        name, _, versions = item
        check_dir(self.release_root/name,self.trusted_uid)
        state = self.state_root/name
        check_dir(state,os.geteuid(),private=True)
        return name, versions, state

    def _release(self,name,versions,generation):
        if type(generation) is not int or str(generation) not in versions:
            deny()
        directory = self.release_root/name/str(generation)
        verify_release(directory, versions[str(generation)],self.trusted_uid)
        return directory

    @contextmanager
    def _lock(self,state):
        fd = os.open(state/'lock',os.O_RDWR|os.O_NOFOLLOW|os.O_NONBLOCK)
        try:
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                    or info.st_nlink != 1 or info.st_mode & 0o077):
                deny()
            fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            yield
        finally:
            os.close(fd)

    def _current(self,name,versions,state):
        path = state/'current'
        if not stat.S_ISLNK(path.lstat().st_mode):
            deny()
        value = os.readlink(path)
        for generation in versions:
            if value == str(self.release_root/name/generation):
                self._release(name,versions,int(generation))
                return int(generation)
        deny()

    def read_current(self,resource_id,target_id):
        name,versions,state = self._slot(resource_id,target_id)
        with self._lock(state):
            return {'id':target_id,'generation':self._current(name,versions,state)}

    def review_transition(self,plan):
        name,versions,state = self._slot(plan['resource_id'],plan['target_id'])
        grant = self.grants[plan['resource_id']]
        if (plan['operation'] != grant.operation or plan['field'] != grant.field
                or plan['principal'] != grant.principal or plan['policy_version'] != grant.policy_version
                or type(plan['expected']) is not int or type(plan['desired']) is not int
                or plan['expected'] == plan['desired']):
            deny()
        with self._lock(state):
            if self._current(name,versions,state) != plan['expected']:
                deny()
            self._release(name,versions,plan['desired'])
            return {'expected_manifest':versions[str(plan['expected'])],
                    'desired_manifest':versions[str(plan['desired'])]}

    def apply_cas(self,plan):
        name,versions,state = self._slot(plan['resource_id'],plan['target_id'])
        with self._lock(state):
            if self._current(name,versions,state) != plan['expected']:
                deny()
            desired = self._release(name,versions,plan['desired'])
            temporary = state/('.current-'+uuid.uuid4().hex)
            os.symlink(str(desired),temporary)
            try:
                os.replace(temporary,state/'current')
                fd = os.open(state,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
            finally:
                temporary.unlink(missing_ok=True)
