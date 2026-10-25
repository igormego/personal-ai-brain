import copy
import io
import json
from pathlib import Path
import threading
import uuid
from urllib.error import HTTPError

import pytest

from personal_ai_brain.codex_bridge_core import Ledger, public_result
from personal_ai_brain import codex_bridge_transport as t
from personal_ai_brain import codex_bridge_transport_runtime as rt


def payload(**fields):
    return dict(transport_version=t.PROTOCOL, operation='codex.submit', request_id=str(uuid.uuid4()),
                task_id=str(uuid.uuid4()), project_id='personal-ai-brain', execution_profile='local-homelab-admin',
                goal='Improve a bounded unit test', acceptance_criteria='Tests pass', base_sha='a'*40) | fields


class Mailbox:
    owner_id = 123

    def __init__(self):
        self.rows = {}
        self.messages = {}
        self.posts = 0
        self.closed = []
        self.post_failure = None
        self.close_failure = False

    def add(self, number, request, title=t.PROTOCOL):
        self.rows[number] = dict(number=number, title=title, state='open', body=json.dumps(request))

    def verify(self):
        pass

    def issues(self):
        return [copy.deepcopy(v) for v in self.rows.values() if v['state'] == 'open']

    def issue(self, number):
        return copy.deepcopy(self.rows[number])

    def comments(self, number):
        return copy.deepcopy(self.messages.get(number, []))

    def post(self, number, body):
        self.posts += 1
        if self.post_failure == 'before':
            raise t.TransportError()
        self.messages.setdefault(number, []).append(dict(body=body, user={'id': self.owner_id}))
        if self.post_failure == 'after':
            raise t.TransportError()

    def close(self, number):
        if self.close_failure:
            raise t.TransportError()
        self.closed.append(number)
        self.rows[number]['state'] = 'closed'


@pytest.fixture
def setup(tmp_path):
    mailbox = Mailbox()
    ledger = Ledger(tmp_path / 'ledger')
    turns = []

    def execute(ledger, request):
        task = ledger.submit(request)
        if task['state'] == 'submitted':
            turns.append(request['task_id'])
            ledger.transition(task['task_id'], 'submitted', 'reserved')
            ledger.transition(task['task_id'], 'reserved', 'running')
            task = ledger.transition(task['task_id'], 'running', 'succeeded', result={
                'test_summary': '10 tests passed in restricted Linux sandbox'})
        return public_result(task)

    worker = t.Worker(tmp_path / 'transport', ledger, mailbox, execute)
    return worker, mailbox, ledger, turns


def drain(worker):
    try:
        worker.tick()
    except t.TransportError as error:
        assert error.code == 'delivery_reconcile'
    worker.tick()


@pytest.mark.parametrize('extra', ['cwd', 'repository', 'distro', 'backend', 'CODEX_HOME', 'sandbox',
    'network', 'environment', 'approval', 'model', 'flags', 'command', 'retry_count', 'validate_completion'])
def test_additional_fields_rejected(extra):
    with pytest.raises(ValueError):
        t.parse_request(json.dumps(payload(**{extra: 'hostile'})))


@pytest.mark.parametrize('override', [dict(project_id='other'), dict(execution_profile='other'),
    dict(operation='codex.approve'), dict(transport_version='pab-control/v0.1'), dict(task_id='bad'),
    dict(request_id=True), dict(base_sha='A'*40), dict(goal=''), dict(goal='x'*8001)])
def test_contract_rejects_invalid(override):
    with pytest.raises((ValueError, TypeError)):
        t.parse_request(json.dumps(payload(**override)))


@pytest.mark.parametrize('body', ['[]', '{}', 'not-json', '{"a":1,"a":2}', 'x'*20001, '{"a":NaN}'])
def test_bad_json(body):
    with pytest.raises(ValueError):
        t.parse_request(body)


def test_unrelated_and_pull_requests_ignored(setup):
    worker, mailbox, ledger, turns = setup
    mailbox.add(1, payload(), 'pab-control/v0.1')
    mailbox.add(2, payload()); mailbox.rows[2]['pull_request'] = {}
    worker.tick()
    assert not turns and not mailbox.posts and not mailbox.closed


def test_invalid_request_closed_without_echo_or_execution(setup):
    worker, mailbox, ledger, turns = setup
    mailbox.add(1, {'secret': 'never-output'})
    worker.tick()
    assert mailbox.closed == [1] and not mailbox.posts and not turns
    assert 'never-output' not in worker.path.read_text()


def test_success_and_identical_request_replay_has_one_truth_and_comment(setup):
    worker, mailbox, ledger, turns = setup
    p = payload(); mailbox.add(1, p)
    drain(worker)
    mailbox.add(2, p)
    worker.tick()
    assert len(turns) == 1 and mailbox.posts == 1
    assert mailbox.rows[2]['state'] == 'closed'
    result = mailbox.messages[1][0]['body']
    assert p['request_id'] in result and 'succeeded' in result
    assert p['goal'] not in result


def test_same_task_new_request_and_conflict(setup):
    worker, mailbox, ledger, turns = setup
    p = payload(); mailbox.add(1, p); drain(worker)
    mailbox.add(2, p | {'request_id': str(uuid.uuid4())}); drain(worker)
    mailbox.add(3, p | {'request_id': str(uuid.uuid4()), 'goal': 'changed'}); drain(worker)
    assert len(turns) == 1
    assert 'task_conflict' in mailbox.messages[3][0]['body']
    assert '"attempts": 0' in mailbox.messages[3][0]['body']


def test_same_request_conflict_no_new_comment_or_turn(setup):
    worker, mailbox, ledger, turns = setup
    p = payload(); mailbox.add(1, p); drain(worker)
    mailbox.add(2, p | {'goal': 'conflicting'}); worker.tick()
    assert len(turns) == 1 and mailbox.posts == 1
    assert worker.load()['rejected']['2'] == 'request_conflict'


def test_crash_after_execution_before_result_durable_reuses_ledger(setup):
    worker, mailbox, ledger, turns = setup
    p = payload(); mailbox.add(1, p)
    execute = worker.execute
    def crash(*args):
        execute(*args)
        raise SystemExit('process killed')
    worker.execute = crash
    with pytest.raises(SystemExit): worker.tick()
    restarted = t.Worker(worker.root, ledger, mailbox, execute)
    drain(restarted)
    assert len(turns) == 1 and mailbox.posts == 1


def test_crash_after_comment_before_close_reconciles_without_duplicate(setup):
    worker, mailbox, ledger, turns = setup
    mailbox.add(1, payload()); mailbox.post_failure = 'after'
    with pytest.raises(t.TransportError): worker.tick()
    restarted = t.Worker(worker.root, ledger, mailbox, worker.execute)
    restarted.tick()
    assert len(turns) == 1 and mailbox.posts == 1 and mailbox.closed == [1]


def test_ambiguous_post_without_visible_comment_fails_closed(setup):
    worker, mailbox, ledger, turns = setup
    mailbox.add(1, payload()); mailbox.post_failure = 'before'
    with pytest.raises(t.TransportError): worker.tick()
    restarted = t.Worker(worker.root, ledger, mailbox, worker.execute)
    with pytest.raises(t.TransportError, match='delivery_uncertain'): restarted.tick()
    assert len(turns) == 1 and mailbox.posts == 1 and not mailbox.closed


def test_close_retry_does_not_repeat_comment(setup):
    worker, mailbox, ledger, turns = setup
    mailbox.add(1, payload())
    with pytest.raises(t.TransportError, match='delivery_reconcile'): worker.tick()
    mailbox.close_failure = True
    with pytest.raises(t.TransportError): worker.tick()
    mailbox.close_failure = False
    t.Worker(worker.root, ledger, mailbox, worker.execute).tick()
    assert mailbox.posts == 1 and len(turns) == 1


@pytest.mark.parametrize('state', ['uncertain', 'interrupted', 'approval_required'])
def test_uncertain_interrupted_approval_never_replay_or_approve(setup, state):
    worker, mailbox, ledger, turns = setup
    p = payload(); task = ledger.submit(t.parse_request(json.dumps(p)))
    ledger.transition(task['task_id'], 'submitted', 'reserved')
    ledger.transition(task['task_id'], 'reserved', 'running')
    ledger.transition(task['task_id'], 'running', state)
    mailbox.add(1, p); drain(worker)
    assert not turns and state in mailbox.messages[1][0]['body']
    assert mailbox.rows[1]['state'] == 'closed'
    assert ledger.load()['tasks'][task['task_id']]['state'] == state


def test_crashed_running_task_reports_uncertainty_without_turn(setup):
    worker, mailbox, ledger, turns = setup
    p = payload(); task = ledger.submit(t.parse_request(json.dumps(p)))
    ledger.transition(task['task_id'], 'submitted', 'reserved')
    ledger.transition(task['task_id'], 'reserved', 'running')
    mailbox.add(1, p); drain(worker)
    assert not turns and 'uncertain' in mailbox.messages[1][0]['body']


def test_only_allowlisted_sanitized_results():
    p = t.parse_request(json.dumps(payload()))
    r = t.envelope(dict(status='succeeded', attempts=1, goal='secret', raw_events='secret',
        test_summary='C:\\private\\secret', files_changed=['../auth'], failure_class='secret',
        commit_sha='token', environment={'TOKEN': 'secret'}), p)
    assert 'secret' not in json.dumps(r) and 'files_changed' not in r and 'test_summary' not in r
    assert r['failure_class'] == 'execution_failed'


def test_marker_spoof_other_author_not_authoritative(setup):
    worker, mailbox, ledger, turns = setup
    p = payload(); mailbox.add(1, p)
    mailbox.messages[1] = [{'body': '<!-- pab-codex-result:v1:'+p['request_id']+' -->\nfake', 'user': {'id': 999}}]
    drain(worker)
    assert mailbox.posts == 1 and len(turns) == 1


def test_single_worker_lock(tmp_path):
    with t.worker_lock(tmp_path/'worker.lock'):
        with pytest.raises(RuntimeError, match='worker_already_running'):
            with t.worker_lock(tmp_path/'worker.lock'): pass
    with t.worker_lock(tmp_path/'worker.lock'): pass


@pytest.mark.parametrize('token', ['', 'bad', 'github_pat_bad\nvalue'])
def test_missing_invalid_token(token):
    with pytest.raises(t.TransportError): t.GitHub(token)


def test_github_fixed_url_redirects_and_bounded_response():
    api = t.GitHub('github_pat_fixture')
    seen=[]
    def opener(req, **kwargs):
        seen.append(req.full_url)
        return io.BytesIO(json.dumps(dict(full_name=t.REPOSITORY,private=True,has_issues=True,owner={'id':123})).encode())
    api.open=opener; api.verify()
    assert seen == ['https://api.github.com/repos/igormego/personal-project-brain']
    assert t.NoRedirect().redirect_request(None,None,301,'',{},'https://evil.invalid') is None
    with pytest.raises(t.TransportError):api.request('GET','https://evil.invalid')
    with pytest.raises(t.TransportError):api.request('POST','/issues',{'title':'x'})
    api.open=lambda *a,**k:io.BytesIO(b'x'*(t.MAX_RESPONSE+1))
    with pytest.raises(t.TransportError,match='response_too_large'):api.verify()


@pytest.mark.parametrize('code', [301, 401, 403, 429, 500])
def test_http_errors_sanitized(code):
    api=t.GitHub('github_pat_fixture')
    def fail(*a,**k): raise HTTPError('https://api.github.com',code,'secret',{'Retry-After':'99999'},None)
    api.open=fail
    with pytest.raises(t.TransportError) as caught:api.verify()
    assert 'secret' not in str(caught.value) and caught.value.delay<=300


def test_bounded_pagination():
    api=t.GitHub('github_pat_fixture')
    calls=[]
    def request(*args):calls.append(args);return [{}]*100
    api.request=request
    with pytest.raises(t.TransportError,match='pagination_limit'):api.issues()
    assert len(calls)==t.MAX_PAGES


def test_backoff_and_restart_health(setup):
    worker, mailbox, ledger, turns = setup
    stop=threading.Event();delays=[]
    def fail():raise t.TransportError('rate_limited',120)
    mailbox.verify=fail
    def wait(delay):delays.append(delay);stop.set()
    t.serve(worker,stop,wait)
    assert delays==[120]
    assert json.loads((worker.root/'health.json').read_text())['status']=='rate_limited'


def test_validator_fixed_sandbox_and_no_payload_options(monkeypatch):
    task=str(uuid.uuid4());ws={'cwd':rt.ROOT+'/tasks/'+task,'base_sha':'a'*40}
    seen=[]
    def run(args,**kw):
        seen.append(args)
        return type('R',(),{'returncode':0,'stdout':b'{"test_summary":"10 tests passed in restricted Linux sandbox"}'})()
    monkeypatch.setattr(rt.subprocess,'run',run)
    assert rt.validate_completion(ws)['test_summary'].startswith('10')
    args=seen[0]
    assert args[:len(rt.PREFIX)]==rt.PREFIX
    assert args[args.index('--permission-profile')+1]=='bridge-workspace-only'
    assert 'permissions.bridge-workspace-only.network.enabled=false' in args
    assert rt.VALIDATE in args
    with pytest.raises(ValueError):rt.validate_completion(ws|{'cwd':'/other'})


def test_repeated_transport_failure_backoff_is_bounded(setup):
    worker, mailbox, ledger, turns = setup
    stop=threading.Event(); delays=[]
    def fail(): raise t.TransportError()
    mailbox.verify=fail
    def wait(delay):
        delays.append(delay)
        if len(delays)==5: stop.set()
    t.serve(worker,stop,wait)
    assert delays==[60,120,240,300,300]


def test_tampered_authoritative_comment_fails_closed(setup):
    worker, mailbox, ledger, turns = setup
    p=payload();mailbox.add(1,p)
    with pytest.raises(t.TransportError,match='delivery_reconcile'):worker.tick()
    mailbox.messages[1][0]['body'] += 'tampered'
    with pytest.raises(t.TransportError,match='result_integrity_failure'):worker.tick()
    assert mailbox.posts==1 and not mailbox.closed and len(turns)==1


def test_runtime_uses_only_accepted_local_entry(monkeypatch):
    seen=[]
    def run(ledger, request, **kwargs):
        seen.append(kwargs)
        return {'status':'succeeded'}
    monkeypatch.setattr(rt,'run_local',run)
    request=t.parse_request(json.dumps(payload()))
    assert rt.execute(object(),request)=={'status':'succeeded'}
    assert seen==[{'validate_completion':rt.validate_completion,'timeout':600}]
