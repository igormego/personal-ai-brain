"""Bounded local messages only; no shared runner state, credentials or commands."""
import hashlib
import json
import os
from pathlib import Path
import stat
import re
import time
import uuid

from . import work_contract as contract
from .work_store import TERMINAL

BASE = '71f0d82516d059ba2d65e028742cd73b2b9ee6bc'
SURFACE = Path('/var/lib/personal-ai-brain/work-transport')
LIMIT = 8192
CAPACITY = 2048
OPERATIONS = {'work.submit', 'work.status', 'work.cancel'}

def encode(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()

def arguments(operation, value, base=BASE):
    if operation not in OPERATIONS or not isinstance(value, dict):
        raise contract.Rejected('unsupported_work_operation')
    if operation == 'work.submit':
        if set(value) != {'contract'}:
            raise contract.Rejected('invalid_work_arguments')
        task = contract.parse(encode(value['contract']))
        if task['base_ref'] != contract.allowed_base(task, base):
            raise contract.Rejected('base_not_allowlisted')
        return {'contract': task}
    if set(value) != {'task_id'}:
        raise contract.Rejected('invalid_work_arguments')
    return {'task_id': contract.task_id(value['task_id'])}

def identifier(operation, value):
    return value['contract']['task_id'] if operation == 'work.submit' else value['task_id']

class Directory:
    """Pin a root-owned mount path; operate only on canonical UUID basenames."""
    def __init__(self, path):
        # Walk every component without following a symlink, including parents.
        path = Path(path)
        if not path.is_absolute():
            raise ValueError('absolute_surface_required')
        fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
        try:
            for part in path.parts[1:]:
                next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd)
                fd = next_fd
            self.fd = fd
        except BaseException:
            os.close(fd)
            raise

    def close(self):
        os.close(self.fd)

    def names(self):
        return os.listdir(self.fd)

    def read(self, task):
        name = contract.task_id(task) + '.json'
        try:
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=self.fd)
        except FileNotFoundError:
            return None
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > LIMIT:
                raise ValueError('invalid_transport_file')
            raw = stream.read(LIMIT + 1)
        if len(raw) > LIMIT:
            raise ValueError('transport_size')
        value = json.loads(raw, object_pairs_hook=contract.unique_object,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        if not isinstance(value, dict):
            raise ValueError('transport_object')
        return value

    def publish(self, task, value):
        name = contract.task_id(task) + '.json'
        raw = encode(value)
        if len(raw) > LIMIT:
            raise ValueError('transport_size')
        existing = self.read(task)
        if existing is not None:
            if existing != value:
                raise contract.Rejected('transport_id_conflict')
            return
        if len(self.names()) >= CAPACITY:
            raise contract.Rejected('transport_capacity')
        temporary = '.' + uuid.uuid4().hex + '.tmp'
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o640, dir_fd=self.fd)
        try:
            with os.fdopen(fd, 'wb') as stream:
                os.fchmod(stream.fileno(), 0o640)  # shared read despite process umask
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            # No replacement: concurrent publishers cannot overwrite an invocation.
            os.link(temporary, name, src_dir_fd=self.fd, dst_dir_fd=self.fd, follow_symlinks=False)
        finally:
            os.unlink(temporary, dir_fd=self.fd)
            os.fsync(self.fd)

def summary(record):
    """Project only typed fields. Never forward request text, paths or logs."""
    status = record['status']
    if status not in TERMINAL | {'submitted', 'reserved', 'running'}:
        raise ValueError('invalid_state')
    if (not isinstance(record['payload_hash'], str)
            or not re.fullmatch('[0-9a-f]{64}', record['payload_hash'])
            or type(record['attempts']) is not int or record['attempts'] not in (0, 1)
            or type(record['cancel_requested']) is not bool):
        raise ValueError('invalid_state_metadata')
    for date in (record['history'][0]['at'], record.get('started_at'), record.get('finished_at')):
        if date is not None and (not isinstance(date, str) or not re.fullmatch(
                r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})', date)):
            raise ValueError('invalid_state_timestamp')
    result = {'status': status, 'artifacts_retained': True} if status in TERMINAL else None
    if status == 'succeeded' and record['request']['task_type'] == contract.GENERIC_TYPE:
        # Metadata only: no script/input/output body or private filesystem path
        # crosses the existing control transport. Never claim semantic success.
        artifacts = record.get('output_artifacts')
        if (type(artifacts) is not list or not 1 <= len(artifacts) <= 32
                or any(type(a) is not dict or set(a) not in (
                           {'path', 'size', 'sha256'},
                           {'path', 'size', 'sha256', 'preview_text'})
                       or type(a['path']) is not str or type(a['size']) is not int
                       or type(a['sha256']) is not str
                       or ('preview_text' in a and
                           (type(a['preview_text']) is not str
                            or len(a['preview_text'].encode('utf-8')) > 512))
                       for a in artifacts)):
            raise ValueError('invalid_generic_result')
        result.update(artifact_manifest=artifacts, goal_semantics_verified=False)
    return {'task_id': contract.task_id(record['task_id']), 'status': status,
            'payload_hash': record['payload_hash'], 'attempts': record['attempts'],
            'cancel_requested': record['cancel_requested'],
            'submitted_at': record['history'][0]['at'],
            'started_at': record.get('started_at'), 'finished_at': record.get('finished_at'),
            'terminal': status in TERMINAL, 'result': result}

class Server:
    def __init__(self, store, base, surface=SURFACE):
        self.store, self.base = store, base
        self.inbox, self.outbox = Directory(surface / 'inbox'), Directory(surface / 'outbox')

    def close(self):
        self.inbox.close()
        self.outbox.close()

    def process(self, request_id):
        request = self.inbox.read(request_id)
        if request is None:
            return
        fingerprint = hashlib.sha256(encode(request)).hexdigest()
        existing = self.outbox.read(request_id)
        if existing is not None:
            if existing['request_hash'] != fingerprint:
                raise ValueError('transport_id_conflict')
            return
        response = {'schema_version': '0.1', 'request_id': request_id, 'request_hash': fingerprint}
        try:
            if (set(request) != {'schema_version', 'request_id', 'capability', 'arguments'}
                    or request['schema_version'] != '0.1' or request['request_id'] != request_id):
                raise contract.Rejected('invalid_handoff')
            operation = request['capability']
            args = arguments(operation, request['arguments'], self.base)
            task = identifier(operation, args)
            if operation == 'work.submit':
                record, created = self.store.submit(args['contract'], self.base)
                data = {**summary(record), 'created': created}
            elif operation == 'work.cancel':
                data = summary(self.store.cancel(task))
            else:
                record = self.store.load(task)
                if record is None:
                    raise contract.Rejected('unknown_task')
                data = summary(record)
            response.update(outcome='completed', data=data)
        except contract.Rejected as error:
            # All Rejected messages come from fixed internal policy constants.
            response.update(outcome='rejected', error=str(error))
        self.outbox.publish(request_id, response)

    def tick(self):
        names = self.inbox.names()
        if len(names) > CAPACITY:
            raise ValueError('transport_capacity')
        for name in sorted(names):
            if not name.endswith('.json'):
                continue
            try:
                self.process(contract.task_id(name[:-5]))
            except (ValueError, OSError, KeyError, TypeError, RecursionError):
                # Poisoned entries cannot reach execution or crash the worker.
                # Do not log attacker text or follow/repair malformed entries.
                continue

class Client:
    def __init__(self, surface=Path('/work-transport'), wait_seconds=2):
        self.surface, self.wait_seconds = surface, wait_seconds

    def invoke(self, operation, args, request_id):
        args = arguments(operation, args)
        request = {'schema_version': '0.1', 'request_id': contract.task_id(request_id),
                   'capability': operation, 'arguments': args}
        inbox, outbox = Directory(self.surface / 'inbox'), Directory(self.surface / 'outbox')
        try:
            inbox.publish(request_id, request)
            fingerprint = hashlib.sha256(encode(request)).hexdigest()
            until = time.monotonic() + self.wait_seconds
            while True:
                response = outbox.read(request_id)
                if response is not None:
                    if (response.get('schema_version') != '0.1' or response.get('request_id') != request_id
                            or response.get('request_hash') != fingerprint):
                        raise ValueError('invalid_response')
                    return response
                if time.monotonic() >= until:
                    return {'request_id': request_id, 'task_id': identifier(operation, args),
                            'outcome': 'handoff_pending', 'durable_handoff': True}
                time.sleep(0.025)
        finally:
            inbox.close()
            outbox.close()
