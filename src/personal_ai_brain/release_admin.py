"""Local operator review/signing CLI; not exposed through Control or sandbox."""
import argparse
from datetime import datetime, timezone
import hashlib
import hmac
import json
import sys
from uuid import uuid4

from .approved_effect import (
    EffectDenied, validate_arguments, _deadline, _signed_receipt,
)
from .effect_plan import plan_effect
from .release_slot import parse_json, NAME
from .release_effect_service import load_configuration
import re

def prepare(adapter, slot, desired, *, now):
    if type(slot) is not str or not re.fullmatch(NAME, slot):
        raise EffectDenied('effect_denied')
    rid = 'release/'+slot
    grant = adapter.grants.get(rid)
    if grant is None:
        raise EffectDenied('effect_denied')
    target = adapter.slots[rid][1]
    plan = plan_effect({'resource_id':rid,'target_id':target,
                        'operation':'activate_release','desired':desired},
                       principal='owner',grant=grant,
                       read_current=adapter.read_current,now=now)
    return {'request_id':str(uuid4()),'plan':plan,
            'manifests':adapter.review_transition(plan)}

def approve(adapter, key, packet, confirm, *, now):
    if (type(packet) is not dict or set(packet) != {'request_id','plan','manifests'}
            or type(packet['plan']) is not dict
            or type(confirm) is not str or confirm != packet['plan'].get('plan_hash')):
        raise EffectDenied('effect_denied')
    plan = packet['plan']
    rid = packet['request_id']
    args = validate_arguments({'plan':plan,'approval':{
        'request_id':rid,'plan_hash':confirm,'signature':'0'*64}})
    _deadline(plan,now.astimezone(timezone.utc))
    if packet['manifests'] != adapter.review_transition(plan):
        raise EffectDenied('effect_denied')
    args['approval']['signature'] = hmac.new(
        key,_signed_receipt(rid,confirm,plan['principal']),hashlib.sha256).hexdigest()
    return {'request_id':rid,'capability':'effect.execute','arguments':args}

def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='action',required=True)
    p = sub.add_parser('prepare')
    p.add_argument('slot')
    p.add_argument('generation',type=int)
    a = sub.add_parser('approve')
    a.add_argument('--confirm',required=True)
    args = parser.parse_args()
    try:
        adapter,key,_ = load_configuration()
        now = datetime.now(timezone.utc)
        if args.action == 'prepare':
            result = prepare(adapter,args.slot,args.generation,now=now)
        else:
            raw = sys.stdin.buffer.read(8193)
            if len(raw)>8192:
                raise EffectDenied('effect_denied')
            result = approve(adapter,key,parse_json(raw),args.confirm,now=now)
        print(json.dumps(result,sort_keys=True))
    except (OSError,ValueError,RuntimeError,KeyError,TypeError,RecursionError):
        print('release_review_denied',file=sys.stderr)
        return 1
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
