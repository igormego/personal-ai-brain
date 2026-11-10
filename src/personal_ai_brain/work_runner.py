"""Offline, single-worker Work Runner v0.1. Administrative local CLI only."""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import time
import multiprocessing

from .work_contract import Rejected, REPOSITORY, RESOURCE_TYPE, GENERIC_TYPE, parse, timestamp, Abort
from .work_store import Store, TERMINAL, atomic_json, locked
from .work_sandbox import execute, kill_tree

ROOT = Path('/var/lib/personal-ai-brain/builder')
PROFILE = Path('/etc/personal-ai-brain/builder/profile.json')

def profile(path=PROFILE):
    value = json.loads(path.read_bytes())
    if (set(value) - {'coding_enabled', 'generic_enabled', 'resource_enabled'} != {'schema_version', 'repository', 'base_ref', 'seed', 'min_free_bytes'}
            or type(value.get('coding_enabled', False)) is not bool
            or type(value.get('generic_enabled', False)) is not bool
            or type(value.get('resource_enabled', False)) is not bool
            or value['schema_version'] != '0.1' or value['repository'] != REPOSITORY
            or not re.fullmatch('[0-9a-f]{40}', value['base_ref'])
            or value['seed'] != '/opt/personal-ai-brain/builder/repository.git'
            or type(value['min_free_bytes']) is not int or value['min_free_bytes'] < 8388608):
        raise ValueError('invalid_operator_profile')
    return value

class Runner:
    def __init__(self, store, policy, stopping=lambda: False, executor=execute,
                 resource_loader=None):
        self.store, self.policy, self.stopping, self.executor = store, policy, stopping, executor
        self.resource_loader = resource_loader

    def recovery(self):
        # systemd KillMode=control-group clears children before restarting us.
        for record in self.store.records():
            if record['status'] in {'reserved', 'running'}:
                identifier = record['task_id']
                atomic_json(self.store.audit / identifier / 'test-result.json',
                            {'status': 'not_completed', 'code': 'interrupted_no_retry'})
                if record['request']['task_type'] == 'coding.patch-test.v1':
                    path = self.store.audit / identifier / 'agent-result.json'
                    prior = json.loads(path.read_bytes())
                    atomic_json(path, {**prior, 'status': 'interrupted', 'code': 'interrupted'})
                self.store.transition(identifier, 'interrupted', 'interrupted_no_retry')

    def guard(self, identifier, deadline):
        if self.stopping():
            raise Abort('interrupted', 'service_stopping')
        if self.store.load(identifier)['cancel_requested']:
            raise Abort('cancelled', 'cancellation_requested')
        if time.monotonic() >= deadline:
            raise Abort('failed', 'deadline_exceeded')

    def git(self, identifier, deadline, *arguments):
        env = {'PATH': '/usr/bin:/bin', 'HOME': '/nonexistent', 'LC_ALL': 'C',
               'GIT_TERMINAL_PROMPT': '0'}
        process = subprocess.Popen(['/usr/bin/git', *arguments], env=env, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   start_new_session=True, close_fds=True)
        try:
            while process.poll() is None:
                self.guard(identifier, deadline)
                time.sleep(0.025)
            self.guard(identifier, deadline)
            if process.returncode:
                raise Abort('failed', 'workspace_preparation_failed')
        finally:
            kill_tree(process)

    def prepare(self, record, deadline):
        identifier = record['task_id']
        parent = self.store.workspaces / identifier
        parent.mkdir(mode=0o700)
        if record['request']['task_type'] in (GENERIC_TYPE, RESOURCE_TYPE):
            scratch = parent / 'scratch'
            scratch.mkdir(mode=0o700)
            return scratch
        workspace = parent / 'repo'
        seed = ('/opt/personal-ai-brain/builder/coding-repository.git'
                if record['request']['task_type'] == 'coding.patch-test.v1' else self.policy['seed'])
        self.git(identifier, deadline, 'clone', '--no-hardlinks', '--no-local', '--no-checkout',
                 seed, str(workspace))
        self.git(identifier, deadline, '-C', str(workspace), 'checkout', '-b',
                 'builder/' + identifier, record['request']['base_ref'])
        self.git(identifier, deadline, '-C', str(workspace), 'remote', 'remove', 'origin')
        # Only the approved fixture can run, never repo scripts/config/hooks.
        if ((workspace / '.gitmodules').exists() or (workspace / 'builder-fixture.txt').exists()
                or any(p.is_file() and os.access(p, os.X_OK) and not p.name.endswith('.sample')
                       for p in (workspace / '.git/hooks').glob('*'))):
            raise Abort('blocked', 'unsupported_repository_content')
        return workspace

    def tick(self):
        candidates = [r for r in self.store.records() if r['status'] == 'submitted']
        if not candidates:
            return False
        record = min(candidates, key=lambda r: r['history'][0]['at'])
        identifier = record['task_id']
        artifacts = self.store.audit / identifier
        deadline = time.monotonic() + record['request']['deadline_seconds']
        try:
            resource = record['request']['task_type'] == RESOURCE_TYPE
            generic = record['request']['task_type'] in (GENERIC_TYPE, RESOURCE_TYPE)
            reserved = {'attempts': 1, 'base_sha': record['request']['base_ref']}
            if not generic:
                reserved['branch'] = 'builder/' + identifier
            record = self.store.transition(identifier, 'reserved', 'worker_reserved',
                                           **reserved)
            if record['status'] in TERMINAL:
                return True
            coding = record['request']['task_type'] == 'coding.patch-test.v1'
            if coding and not self.policy.get('coding_enabled', False):
                self.store.transition(identifier, 'blocked', 'coding_not_enabled')
                return True
            if generic and not self.policy.get('generic_enabled', False):
                self.store.transition(identifier, 'blocked', 'generic_not_enabled')
                return True
            if resource and not self.policy.get('resource_enabled', False):
                self.store.transition(identifier, 'blocked', 'resource_not_enabled')
                return True
            if shutil.disk_usage(self.store.root).free < self.policy['min_free_bytes']:
                self.store.transition(identifier, 'blocked', 'insufficient_storage')
                return True
            workspace = self.prepare(record, deadline)
            grants, receipt = None, None
            if resource:
                from .work_resource_grants import ResourceGrantUnavailable, load_host_resource
                try:
                    grants, receipt = (self.resource_loader or load_host_resource)(
                        record['request']['resource_id'])
                except ResourceGrantUnavailable:
                    self.store.transition(identifier, 'blocked', 'resource_unavailable')
                    return True
            self.guard(identifier, deadline)
            record = self.store.transition(identifier, 'running', 'sandbox_starting', started_at=timestamp())
            if record['status'] in TERMINAL:
                return True
            output_artifacts = ()
            if generic:
                from .work_generic import execute as generic_execute
                if resource:
                    status, code, output_artifacts = generic_execute(
                        record['request'], workspace, artifacts,
                        lambda: self.guard(identifier, deadline),
                        trusted_grants=grants)
                else:
                    status, code, output_artifacts = generic_execute(
                        record['request'], workspace, artifacts,
                        lambda: self.guard(identifier, deadline))
            elif coding:
                from .coding_execute import execute as coding_execute
                status, code = coding_execute(record['request'], workspace, artifacts,
                                               lambda: self.guard(identifier, deadline))
            else:
                status, code = self.executor(workspace, artifacts, deadline,
                                         lambda: self.store.load(identifier)['cancel_requested'],
                                         self.stopping, Path(__file__).with_name('work_fixture.py'))
            if self.stopping():
                status, code = 'interrupted', 'service_stopping'
            if generic and status == 'succeeded':
                extra = {'output_artifacts': list(output_artifacts),
                         'goal_semantics_verified': False}
                if resource:
                    extra['resource_receipt'] = receipt
                self.store.transition(identifier, status, code, **extra)
            else:
                self.store.transition(identifier, status, code)
        except Abort as error:
            atomic_json(artifacts / 'test-result.json', {'status': 'not_completed', 'code': error.code})
            self.store.transition(identifier, error.status, error.code)
        except OSError:
            self.store.storage_failure(identifier)
        except Exception:
            # Fixed diagnostics only, preserve workspace and prior evidence.
            atomic_json(artifacts / 'test-result.json', {'status': 'not_completed', 'code': 'runner_error'})
            self.store.transition(identifier, 'failed', 'runner_error')
        return True

def receive_work(root, base, stopped):
    from .work_handoff import Server
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stopped.set())
    handoff = Server(Store(root), base)
    try:
        while not stopped.is_set():
            handoff.tick()
            stopped.wait(0.05)
    finally:
        handoff.close()

def serve(store, policy):
    stopped = False
    def stop(*_):
        nonlocal stopped
        stopped = True
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, stop)
    with locked(store.state / 'worker.lock', nonblocking=True):
        runner = Runner(store, policy, stopping=lambda: stopped)
        runner.recovery()
        # A separate receiver keeps cancellation responsive without introducing
        # threads around the sandbox's resource-limit preexec_fn.
        context = multiprocessing.get_context('spawn')
        handoff_stop = context.Event()
        receiver = context.Process(target=receive_work,
            args=(store.root, policy['base_ref'], handoff_stop), daemon=True)
        receiver.start()
        runner.stopping = lambda: stopped or not receiver.is_alive()
        print('work_runner_ready', flush=True)
        try:
            while not stopped and receiver.is_alive():
                if not runner.tick():
                    time.sleep(0.1)
        finally:
            stopped = True
            handoff_stop.set()
            receiver.join(timeout=2)
            if receiver.is_alive():
                receiver.terminate()
                receiver.join(timeout=2)
        if receiver.exitcode != 0:
            raise RuntimeError('handoff_unavailable')

def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['serve', 'submit', 'status', 'cancel'])
    parser.add_argument('task_id', nargs='?')
    arguments = parser.parse_args()
    try:
        store = Store(ROOT)
        policy = profile()
        if arguments.action == 'serve':
            serve(store, policy)
            return 0
        if arguments.action == 'submit':
            request = parse(sys.stdin.buffer.read(8193))
            record, created = store.submit(request, policy['base_ref'])
            print(json.dumps({'task_id': record['task_id'], 'status': record['status'],
                              'created': created, 'payload_hash': record['payload_hash']}))
        elif arguments.action == 'cancel':
            print(json.dumps(store.cancel(arguments.task_id)))
        else:
            record = store.load(arguments.task_id)
            if record is None:
                raise Rejected('unknown_task')
            print(json.dumps(record))
        return 0
    except Rejected as error:
        print(json.dumps({'error': str(error)}))
        return 2
    except Exception:
        print('{"error":"runner_unavailable_or_state_error"}', flush=True)
        return 1

if __name__ == '__main__':
    sys.exit(main())
