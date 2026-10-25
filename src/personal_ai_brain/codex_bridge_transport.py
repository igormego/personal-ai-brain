"""Outbound mailbox only. Execution authority stays in the accepted local ledger."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener, HTTPRedirectHandler

from .codex_bridge_core import Ledger, atomic, digest, identifier, now, validate

PROTOCOL = 'pab-codex/v0.1'
REPOSITORY = 'igormego/personal-project-brain'
MAX_BODY = 20000
MAX_RESPONSE = 262144
MAX_PAGES = 10
FINAL = {'succeeded', 'failed', 'cancelled', 'interrupted', 'uncertain', 'approval_required'}


class TransportError(Exception):
    def __init__(self, code='transport_unavailable', delay=30, http_status=None):
        self.code = code
        self.http_status = http_status
        self.delay = min(300, max(30, delay))
        super().__init__(code)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def strict_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate_fields')
        result[key] = value
    return result


def parse_request(body):
    if not isinstance(body, str) or len(body.encode('utf-8')) > MAX_BODY:
        raise ValueError('invalid_body')
    value = json.loads(body, object_pairs_hook=strict_object,
                       parse_constant=lambda _: (_ for _ in ()).throw(ValueError('invalid_number')))
    if not isinstance(value, dict) or value.get('transport_version') != PROTOCOL or value.get('operation') != 'codex.submit':
        raise ValueError('invalid_protocol')
    return validate({k: v for k, v in value.items() if k not in {'transport_version', 'operation'}})


class GitHub:
    def __init__(self, token):
        if not token or not token.startswith('github_pat_') or any(c.isspace() for c in token):
            raise TransportError('credential_invalid')
        self.token = token
        self.open = build_opener(NoRedirect()).open
        self.owner_id = None

    def request(self, method, suffix, data=None):
        permitted = bool(method == 'GET' and (suffix == '' or suffix == '/contents/core/project-map.yaml' or
            re.fullmatch(r'/issues\?state=open&per_page=100&page=[1-9][0-9]*', suffix) or
            re.fullmatch(r'/issues/[1-9][0-9]*(/comments\?per_page=100&page=[1-9][0-9]*)?', suffix)))
        permitted |= method == 'POST' and bool(re.fullmatch(r'/issues/[1-9][0-9]*/comments', suffix)) and set(data or {}) == {'body'}
        permitted |= method == 'PATCH' and bool(re.fullmatch(r'/issues/[1-9][0-9]*', suffix)) and data == {'state': 'closed'}
        if not permitted:
            raise TransportError('transport_policy_denied')
        req = Request('https://api.github.com/repos/' + REPOSITORY + suffix, method=method,
                      data=json.dumps(data).encode() if data is not None else None,
                      headers={'Authorization': 'Bearer ' + self.token, 'Accept': 'application/vnd.github+json',
                               'X-GitHub-Api-Version': '2022-11-28', 'User-Agent': 'pab-windows-bridge',
                               'Content-Type': 'application/json'})
        try:
            with self.open(req, timeout=20) as response:
                raw = response.read(MAX_RESPONSE + 1)
                if len(raw) > MAX_RESPONSE:
                    raise TransportError('response_too_large')
                return json.loads(raw)
        except HTTPError as error:
            if error.code in (401, 403, 404):
                code = 'authentication_permission_failure'
            else:
                code = 'transport_unavailable'
            delay = 30
            if error.code == 429 or error.headers.get('X-RateLimit-Remaining') == '0':
                code = 'rate_limited'
                try:
                    delay = max(30, float(error.headers.get('Retry-After', 30)))
                except ValueError:
                    pass
            raise TransportError(code, delay, error.code) from None
        except (URLError, OSError, ValueError):
            raise TransportError() from None

    def verify(self):
        data = self.request('GET', '')
        if not isinstance(data, dict) or data.get('full_name') != REPOSITORY or data.get('private') is not True or not data.get('has_issues'):
            raise TransportError('repository_policy_denied')
        self.owner_id = data['owner']['id']

    def pages(self, prefix):
        output = []
        for page in range(1, MAX_PAGES + 1):
            rows = self.request('GET', prefix + str(page))
            if not isinstance(rows, list) or len(rows) > 100:
                raise TransportError('invalid_page')
            output.extend(rows)
            if len(rows) < 100:
                return output
        raise TransportError('pagination_limit')

    def issues(self):
        return self.pages('/issues?state=open&per_page=100&page=')

    def issue(self, number):
        return self.request('GET', f'/issues/{number}')

    def comments(self, number):
        return self.pages(f'/issues/{number}/comments?per_page=100&page=')

    def post(self, number, body):
        return self.request('POST', f'/issues/{number}/comments', {'body': body})

    def close(self, number):
        self.request('PATCH', f'/issues/{number}', {'state': 'closed'})


def envelope(result, payload):
    # Never forward arbitrary backend output, paths, summaries or failure text.
    status = result.get('status')
    if status not in FINAL:
        status = 'uncertain'
    attempts = result.get('attempts', 0)
    if type(attempts) is not int or attempts not in (0, 1):
        raise ValueError('invalid_attempts')
    out = {k: payload[k] for k in ('request_id', 'task_id', 'project_id', 'base_sha')}
    out.update(transport_version=PROTOCOL, status=status, attempts=attempts,
               approval_required=status == 'approval_required')
    allowed_failures = {'owner_approval_required', 'backend_startup_failed', 'execution_truth_uncertain',
                        'validation_required', 'request_conflict', 'task_conflict', 'base_not_approved',
                        'recovery_uncertain', 'recovery_interrupted', 'recovery_failed', 'owner_cancelled'}
    failure = result.get('failure_class')
    out['failure_class'] = failure if failure in allowed_failures or failure is None else 'execution_failed'
    sha = result.get('commit_sha')
    if isinstance(sha, str) and re.fullmatch('[0-9a-f]{40}', sha):
        out['commit_sha'] = sha
    files = result.get('files_changed')
    if isinstance(files, list) and len(files) <= 128 and all(
        isinstance(f, str) and len(f) <= 256 and re.fullmatch(r'[A-Za-z0-9_.\-/]+', f) and
        not f.startswith('/') and '..' not in f.split('/') and
        not re.search(r'github_pat_|gh[pousr]_', f) for f in files):
        out['files_changed'] = files
    for key in ('additions', 'deletions'):
        if type(result.get(key)) is int and 0 <= result[key] <= 100000000:
            out[key] = result[key]
    summary = result.get('test_summary')
    if isinstance(summary, str) and re.fullmatch(r'[0-9]{1,6} tests passed in restricted Linux sandbox', summary):
        out['test_summary'] = summary
    return out


@contextmanager
def worker_lock(path):
    with open(path, 'a+b') as stream:
        if stream.tell() == 0:
            stream.write(b'0'); stream.flush()
        stream.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise RuntimeError('worker_already_running') from None
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == 'nt':
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


class Worker:
    def __init__(self, root, ledger, client, execute):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.ledger, self.client, self.execute = ledger, client, execute
        self.path = self.root / 'delivery.json'

    def load(self):
        return json.loads(self.path.read_text()) if self.path.exists() else {'requests': {}, 'rejected': {}}

    def save(self, state):
        atomic(self.path, state)

    def tick(self):
        self.client.verify()
        issues = self.client.issues()
        for issue in issues:
            if issue.get('title') != PROTOCOL or 'pull_request' in issue:
                continue
            number = issue.get('number')
            if type(number) is not int or number < 1:
                raise TransportError('invalid_issue')
            issue = self.client.issue(number)
            if issue.get('title') != PROTOCOL or issue.get('state') != 'open' or 'pull_request' in issue:
                continue
            self.process(number, issue.get('body'))
        atomic(self.root / 'health.json', {'status': 'healthy', 'checked_at': now(),
               'requests_seen': len(self.load()['requests'])})

    def process(self, number, body):
        state = self.load()
        try:
            payload = parse_request(body)
        except (ValueError, TypeError, RecursionError, UnicodeError):
            # No echo of malformed input, no executable reservation, no ambiguous POST.
            if len(state['rejected']) >= 4096 and str(number) not in state['rejected']:
                raise TransportError('retention_limit')
            state['rejected'][str(number)] = 'invalid_request'
            self.save(state)
            self.client.close(number)
            return
        rid = payload['request_id']
        record = state['requests'].get(rid)
        if record and record['hash'] != digest(payload):
            if len(state['rejected']) >= 4096 and str(number) not in state['rejected']:
                raise TransportError('retention_limit')
            state['rejected'][str(number)] = 'request_conflict'
            self.save(state)
            self.client.close(number)
            return
        if record is None:
            if len(state['requests']) >= 4096 or len(state['rejected']) >= 4096:
                raise TransportError('retention_limit')
            record = {'hash': digest(payload), 'issue': number, 'phase': 'invoking'}
            state['requests'][rid] = record
            self.save(state)
        if record['phase'] == 'invoking':
            try:
                result = self.execute(self.ledger, payload)
            except ValueError as error:
                if str(error) not in {'request_conflict', 'task_conflict', 'base_not_approved'}:
                    raise
                result = {'status': 'failed', 'attempts': 0, 'failure_class': str(error)}
            result = envelope(result, payload)
            marker = '<!-- pab-codex-result:v1:' + rid + ' -->'
            record.update(phase='ready', body=marker + '\n```json\n' + json.dumps(result, sort_keys=True) + '\n```')
            self.save(state)
        original = record['issue']
        if record['phase'] != 'delivered':
            comments = self.client.comments(original)
            marker = '<!-- pab-codex-result:v1:' + rid + ' -->'
            matching = [c for c in comments if c.get('body', '').startswith(marker) and
                        c.get('user', {}).get('id') == self.client.owner_id]
            if len(matching) > 1 or (matching and matching[0]['body'] != record['body']):
                raise TransportError('result_integrity_failure')
            if matching:
                record['phase'] = 'delivered'
                self.save(state)
            elif record['phase'] == 'posting':
                # A POST may have reached GitHub: never blindly retry, even if
                # the current read does not yet show it. Owner reconciliation.
                raise TransportError('delivery_uncertain')
            else:
                record['phase'] = 'posting'
                self.save(state)
                self.client.post(original, record['body'])
                # Always reconcile server-visible content before closure.
                raise TransportError('delivery_reconcile', 30)
        self.client.close(original)
        if number != original:
            self.client.close(number)


def serve(worker, stop, wait=None):
    wait = wait or stop.wait
    delay = 30
    with worker_lock(worker.root / 'worker.lock'):
        while not stop.is_set():
            try:
                worker.tick()
                delay = 30
            except TransportError as error:
                delay = 30 if error.code == 'delivery_reconcile' else min(300, max(delay * 2, error.delay))
                atomic(worker.root / 'health.json', {'status': error.code, 'checked_at': now()})
            except Exception:
                atomic(worker.root / 'health.json', {'status': 'worker_failure', 'checked_at': now()})
                delay = min(300, delay * 2)
            wait(delay)
