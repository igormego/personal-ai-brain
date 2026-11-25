"""Offline real filesystem and broker tests; not host deployment."""
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
from uuid import uuid4

import pytest

from personal_ai_brain.approved_effect import ApprovedEffectBroker, EffectDenied, EffectUncertain, _signed_receipt
from personal_ai_brain.effect_plan import plan_effect
from personal_ai_brain.effect_reservation import EffectReservations
from personal_ai_brain.release_slot import ReleaseSlots

NOW = datetime(2026,11,25,tzinfo=timezone.utc)
KEY = b'only-synthetic-test-secret'*2

def create_release(root,generation,body):
    directory = root/'importer'/str(generation)
    directory.mkdir(parents=True)
    (directory/'main.py').write_bytes(body)
    raw = json.dumps({'schema_version':'0.1','files':{'main.py':hashlib.sha256(body).hexdigest()}}).encode()
    (directory/'manifest.json').write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()

@pytest.fixture
def setup(tmp_path):
    releases = tmp_path/'releases'
    releases.mkdir()
    policy = {'schema_version':'0.1','slots':{'importer':{'target_id':1,'generations':{
        '1':create_release(releases,1,b'one'),
        '2':create_release(releases,2,b'two')}}}}
    state = tmp_path/'state'
    state.mkdir(mode=0o700)
    slot = state/'importer'
    slot.mkdir(mode=0o700)
    (slot/'lock').touch(mode=0o600)
    (slot/'current').symlink_to(releases/'importer/1')
    receipts = tmp_path/'receipts'
    receipts.mkdir(mode=0o700)
    adapter = ReleaseSlots(policy,releases,state,trusted_uid=os.geteuid())
    return adapter,releases,slot,receipts

def signed(adapter,desired=2):
    plan = plan_effect({'resource_id':'release/importer','target_id':1,
                        'operation':'activate_release','desired':desired},
                       principal='owner',grant=adapter.grants['release/importer'],
                       read_current=adapter.read_current,now=NOW)
    rid = str(uuid4())
    sig = hmac.new(KEY,_signed_receipt(rid,plan['plan_hash'],'owner'),hashlib.sha256).hexdigest()
    return rid,{'plan':plan,'approval':{'request_id':rid,'plan_hash':plan['plan_hash'],'signature':sig}}

def broker(adapter,receipts):
    return ApprovedEffectBroker(grants=adapter.grants,adapter=adapter,verification_key=KEY,
                                clock=lambda:NOW,reserve=EffectReservations(receipts))

def test_real_switch_and_separately_approved_rollback_no_replay(setup):
    adapter,releases,slot,receipts=setup
    b=broker(adapter,receipts)
    rid,args=signed(adapter)
    assert b.execute(args,request_id=rid)['observed']==2
    assert (slot/'current/main.py').read_bytes()==b'two'
    assert (releases/'importer/1/main.py').read_bytes()==b'one'
    rollback,back=signed(adapter,1)
    assert b.execute(back,request_id=rollback)['observed']==1
    # Original expected state now matches again; durable host reservation
    # still prevents the old signed request, even after broker reconstruction.
    with pytest.raises(EffectUncertain):
        broker(adapter,receipts).execute(args,request_id=rid)
    assert (slot/'current/main.py').read_bytes()==b'one'
    assert len(list(receipts.iterdir()))==2

@pytest.mark.parametrize('change',['tamper','extra','symlink','hardlink','group-writable','unregistered'])
def test_untrusted_release_never_activated(setup,change):
    adapter,releases,slot,receipts=setup
    rid,args=signed(adapter,3 if change=='unregistered' else 2)
    directory=releases/'importer/2'
    if change=='tamper':
        (directory/'main.py').write_bytes(b'changed')
    elif change=='extra':
        (directory/'extra.py').write_bytes(b'extra')
    elif change=='symlink':
        (directory/'main.py').unlink()
        (directory/'main.py').symlink_to(releases/'importer/1/main.py')
    elif change=='group-writable':
        (directory/'main.py').chmod(0o664)
    elif change=='hardlink':
        (directory/'main.py').unlink()
        os.link(releases/'importer/1/main.py',directory/'main.py')
    with pytest.raises(EffectUncertain):
        broker(adapter,receipts).execute(args,request_id=rid)
    assert (slot/'current').readlink()==releases/'importer/1'

def test_stale_plan_cannot_switch(setup):
    adapter,releases,slot,receipts=setup
    rid,args=signed(adapter)
    new_id,new_args=signed(adapter)
    broker(adapter,receipts).execute(new_args,request_id=new_id)
    with pytest.raises(EffectDenied):
        broker(adapter,receipts).execute(args,request_id=rid)
    assert len(list(receipts.iterdir()))==1


def test_local_review_signer_exact_packet_and_recheck(setup):
    from personal_ai_brain.release_admin import prepare,approve
    adapter,releases,slot,receipts=setup
    packet=prepare(adapter,'importer',2,now=NOW)
    with pytest.raises(EffectDenied):
        approve(adapter,KEY,packet,'wrong',now=NOW)
    from copy import deepcopy
    tampered=deepcopy(packet)
    tampered['manifests']['desired_manifest']='0'*64
    with pytest.raises(EffectDenied):
        approve(adapter,KEY,tampered,packet['plan']['plan_hash'],now=NOW)
    approved=approve(adapter,KEY,packet,packet['plan']['plan_hash'],now=NOW)
    assert approved['capability']=='effect.execute'
    assert broker(adapter,receipts).execute(approved['arguments'],
        request_id=approved['request_id'])['observed']==2
    with pytest.raises(EffectDenied):
        approve(adapter,KEY,packet,packet['plan']['plan_hash'],now=NOW)
