"""Fixed offline Bubblewrap execution, bounded output and whole-sandbox cleanup."""
import json
import os
from pathlib import Path
import resource
import selectors
import signal
import stat
import subprocess
import sys
import time

from .work_store import atomic_bytes, atomic_json

CONTENT = b'Personal AI Brain Work Runner v0.1\n'
CHECKS = {'core_credential_denied', 'control_credential_denied', 'builder_credential_denied',
          'other_workspace_denied', 'production_state_denied', 'supervisor_state_denied',
          'host_shadow_denied', 'docker_socket_denied', 'docker_socket_absent',
          'non_root', 'only_primary_group', 'no_capabilities', 'no_new_privileges',
          'setuid_root_denied', 'network_denied', 'git_metadata_readonly', 'fixture_content_matches',
          'transport_absent'}

def limits():
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_FSIZE, (1048576, 1048576))
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))

def command(workspace, fixture, *, generic=False):
    version = f'python{sys.version_info.major}.{sys.version_info.minor}'
    args = ['/usr/bin/bwrap', '--unshare-all', '--unshare-user', '--die-with-parent', '--new-session',
            '--disable-userns', '--assert-userns-disabled', '--uid', '65534', '--gid', '65534',
            '--cap-drop', 'ALL', '--clearenv',
            '--ro-bind', '/usr/bin/python3', '/usr/bin/python3',
            '--ro-bind', '/usr/lib/' + version, '/usr/lib/' + version,
            '--ro-bind', '/usr/lib/x86_64-linux-gnu', '/usr/lib/x86_64-linux-gnu',
            '--ro-bind', '/usr/lib64', '/usr/lib64',
            '--symlink', 'usr/lib', '/lib', '--symlink', 'usr/lib64', '/lib64',
            '--proc', '/proc', '--remount-ro', '/proc', '--dev', '/dev',
            '--size', '16777216', '--tmpfs', '/tmp', '--size', '4194304', '--tmpfs', '/home',
            '--dir', '/home/worker', '--setenv', 'HOME', '/home/worker',
            '--setenv', 'PATH', '/usr/bin', '--setenv', 'LC_ALL', 'C']
    if generic:
        # Never expose a writable host directory to untrusted task code:
        # only a size-capped anonymous tmpfs and RO staged input mount.
        args.extend(['--size', '16777216', '--tmpfs', '/workspace',
                     '--dir', '/workspace/inputs',
                     '--ro-bind', str(workspace / 'inputs'), '/workspace/inputs',
                     '--dir', '/workspace/outputs'])
    else:
        args.extend(['--bind', str(workspace), '/workspace',
                     '--ro-bind', str(workspace / '.git'), '/workspace/.git'])
    args.extend(['--ro-bind', str(fixture), '/fixture.py', '--chdir', '/workspace',
                 '/usr/bin/python3', '-I', '-B', '/fixture.py'])
    return args

def kill_tree(process):
    # Killing the namespace parent destroys even children that called setsid().
    if process.poll() is None:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=3)

def execute(workspace, artifacts, deadline, cancellation, stopping, fixture):
    process = None
    output = bytearray()
    code = 'sandbox_failed'
    terminal = 'failed'
    try:
        process = subprocess.Popen(command(workspace, fixture), stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   env={'PATH': '/usr/bin:/bin', 'LC_ALL': 'C'},
                                   close_fds=True, start_new_session=True, preexec_fn=limits)
        with selectors.DefaultSelector() as selector, (artifacts / 'execution.log').open('ab', buffering=0) as log:
            selector.register(process.stdout, selectors.EVENT_READ)
            while True:
                if stopping():
                    terminal, code = 'interrupted', 'service_stopping'
                    break
                if cancellation():
                    terminal, code = 'cancelled', 'cancellation_requested'
                    break
                if time.monotonic() >= deadline:
                    terminal, code = 'failed', 'deadline_exceeded'
                    break
                for key, _ in selector.select(timeout=0.05):
                    chunk = os.read(key.fd, 4096)
                    if chunk:
                        remaining = 16384 - len(output)
                        output.extend(chunk[:remaining])
                        log.write(chunk[:remaining])
                        os.fsync(log.fileno())
                        if len(chunk) > remaining:
                            terminal, code = 'failed', 'output_limit'
                            break
                    else:
                        selector.unregister(key.fd)
                if code == 'output_limit':
                    break
                if process.poll() is not None and not selector.get_map():
                    if process.returncode == 0:
                        terminal, code = 'succeeded', 'fixture_completed'
                    break
    finally:
        if process is not None:
            kill_tree(process)
            process.stdout.close()
    # systemd signals the entire cgroup. EOF/child exit can race with the
    # supervisor's signal handler; classify cancellation/stop again after reaping.
    if stopping():
        terminal, code = 'interrupted', 'service_stopping'
    elif cancellation():
        terminal, code = 'cancelled', 'cancellation_requested'
    if terminal != 'succeeded':
        atomic_json(artifacts / 'test-result.json', {'status': 'not_completed', 'code': code})
        return terminal, code
    try:
        report = json.loads(output)
        assert report['ok'] is True and report['fixture'] == 'fixture.patch-test.v1'
        assert set(report['checks']) == CHECKS and all(v is True for v in report['checks'].values())
        fd = os.open(workspace / 'builder-fixture.txt', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            assert stat.S_ISREG(info.st_mode) and info.st_size == len(CONTENT)
            assert stream.read(len(CONTENT) + 1) == CONTENT
    except (AssertionError, ValueError, KeyError, TypeError, OSError):
        atomic_json(artifacts / 'test-result.json', {'status': 'failed', 'code': 'artifact_validation_failed'})
        return 'failed', 'artifact_validation_failed'
    atomic_json(artifacts / 'test-result.json', {'status': 'passed', **report})
    # Fixed verified new file; never invoke Git against task-modified config/hooks.
    patch = (b'diff --git a/builder-fixture.txt b/builder-fixture.txt\nnew file mode 100644\n'
             b'--- /dev/null\n+++ b/builder-fixture.txt\n@@ -0,0 +1 @@\n+' + CONTENT)
    atomic_bytes(artifacts / 'patch.diff', patch)
    return terminal, code
