"""Opt-in local service assembly for reviewed release activation.

No credentials or addresses are accepted from Issues. No application code is
executed by this service. The operator provisions fixed directories, group
access, immutable packages, HMAC verifier and the socket mount.
"""
import argparse
import os
from pathlib import Path

from .approved_effect import ApprovedEffectBroker, EffectDenied
from .effect_plan import plan_effect
from .effect_reservation import EffectReservations
from .host_effect_socket import HostEffectSocketServer
from .release_slot import ReleaseSlots, immutable_file, parse_json, check_dir

CONFIG = Path('/etc/personal-ai-brain/effects')
RELEASES = Path('/opt/personal-ai-brain/releases')
STATE = Path('/var/lib/personal-ai-brain/effects')
SOCKET = Path('/run/pab-effects/effect.sock')

def build():
    # Protect every ancestor of immutable configuration and release roots.
    for directory in (CONFIG, RELEASES):
        for ancestor in (directory,*directory.parents):
            check_dir(ancestor,0)
    policy = parse_json(immutable_file(CONFIG/'releases.json',0,32768))
    peer = parse_json(immutable_file(CONFIG/'peer.json',0,1024))
    if (type(peer) is not dict or set(peer) != {'allowed_uid'}
            or type(peer['allowed_uid']) is not int or peer['allowed_uid'] <= 0
            or peer['allowed_uid'] == os.geteuid()):
        raise EffectDenied('effect_denied')
    key = immutable_file(CONFIG/'verification.key',0,64)
    if len(key)<32 or (CONFIG/'verification.key').stat().st_mode & 0o007:
        raise EffectDenied('effect_denied')
    check_dir(STATE,os.geteuid(),private=True)
    adapter = ReleaseSlots(policy, RELEASES, STATE/'slots')
    journal = EffectReservations(STATE/'reservations')
    broker = ApprovedEffectBroker(grants=adapter.grants,adapter=adapter,
                                  verification_key=key,reserve=journal)
    return adapter,broker,peer['allowed_uid']

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action',choices=('serve',))
    parser.parse_args()
    try:
        _,broker,uid = build()
        with HostEffectSocketServer(SOCKET,allowed_uid=uid,broker=broker,
                                    socket_mode=0o660) as server:
            print('release_effect_service_ready',flush=True)
            while True:
                server.serve_one()
    except (OSError,ValueError,RuntimeError):
        print('release_effect_service_stopped',flush=True)
        return 1

if __name__ == '__main__':
    raise SystemExit(main())
