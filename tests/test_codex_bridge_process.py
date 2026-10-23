import io
import queue
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
import pytest
from personal_ai_brain.codex_bridge_process import ProcessOwner
from personal_ai_brain.codex_bridge_client import AppServer

def test_owned_descendant_cannot_hold_stdout_after_parent_death():
    owner=ProcessOwner()
    child="import time;time.sleep(30)"
    parent="import subprocess,sys,time;subprocess.Popen([sys.executable,'-c',"+repr(child)+"]);print('ready',flush=True);time.sleep(30)"
    p=owner.spawn([sys.executable,"-c",parent],stdin=subprocess.PIPE,
                  stdout=subprocess.PIPE,text=True)
    try:
        assert p.stdout.readline().strip()=="ready"
        p.kill();p.wait(timeout=2)
        done=threading.Event()
        reader=threading.Thread(target=lambda:(p.stdout.read(),done.set()))
        reader.start()
        assert not done.wait(.1)
        start=time.monotonic()
        owner.close()
        assert done.wait(3)
        reader.join(1)
        assert time.monotonic()-start<4
    finally:
        owner.close()
        if p.poll() is None:p.kill()
        p.wait(timeout=2)
        p.stdin.close();p.stdout.close()

def test_owner_never_targets_unrelated_process():
    unrelated=subprocess.Popen([sys.executable,"-c","import time;time.sleep(30)"])
    owner=ProcessOwner()
    try:
        owned=owner.spawn([sys.executable,"-c","import time;time.sleep(30)"])
        owner.close()
        owned.wait(timeout=3)
        assert unrelated.poll() is None
    finally:
        owner.close()
        unrelated.terminate();unrelated.wait(timeout=3)

@pytest.mark.parametrize("alive,timeout",[(False,False),(True,False),(True,True)])
def test_shutdown_releases_waiters_and_is_idempotent(alive,timeout):
    obj=AppServer.__new__(AppServer)
    obj.lifecycle="RUNNING";obj.stopping=threading.Event()
    obj.close_lock=threading.Lock();obj.pending_lock=threading.Lock()
    obj.pending={i:queue.Queue(1) for i in range(3)}
    obj.disconnected=False
    calls=[]
    class Process:
        stdin=io.StringIO();stdout=io.StringIO()
        def poll(self):return None if alive else 0
        def terminate(self):calls.append("terminate")
        def wait(self,timeout):
            if "job_closed" not in calls and timeout and alive:
                if globals_timeout:raise subprocess.TimeoutExpired("owned",timeout)
            return 0
    globals_timeout=timeout
    obj.process=Process()
    obj.owner=SimpleNamespace(close=lambda:calls.append("job_closed"))
    obj.reader=SimpleNamespace(join=lambda timeout:None,is_alive=lambda:False)
    obj.writer=SimpleNamespace(join=lambda timeout:None,is_alive=lambda:False)
    obj.close()
    assert obj.lifecycle=="STOPPED"
    assert all(waiter.get_nowait()["client_failure"]=="client_shutdown" for waiter in obj.pending.values())
    snapshot=list(calls)
    obj.close()
    assert calls==snapshot
    with pytest.raises(RuntimeError,match="stopping"):
        obj.send({"method":"anything"})

def test_process_owner_cannot_be_reused():
    owner=ProcessOwner()
    try:
        p=owner.spawn([sys.executable,"-c","pass"])
        p.wait(timeout=3)
        with pytest.raises(RuntimeError,match="already_used"):
            owner.spawn([sys.executable,"-c","pass"])
    finally:owner.close()
