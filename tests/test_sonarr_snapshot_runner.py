"""Linux-only synthetic publication/timeout/secret-boundary tests."""
import fcntl
import json
import multiprocessing
import os
from pathlib import Path
import signal
import time

import pytest

from personal_ai_brain import sonarr_snapshot_runner as runner
from personal_ai_brain.assistant_media_resources import MediaResourceUnavailable
from personal_ai_brain.media_snapshot import read_media_snapshot
from test_media_snapshot import packet


@pytest.fixture
def setup(tmp_path, monkeypatch):
    tmp_path.chmod(0o700)
    config = tmp_path / "config.xml"
    config.write_text('<Config><ApiKey>' + 'a' * 32 + '</ApiKey><Port>8989</Port><UrlBase /></Config>')
    config.chmod(0o600)
    target = tmp_path / "output"
    target.mkdir(mode=0o700)
    monkeypatch.setattr(runner, "SonarrOverviewCollector", lambda key: lambda _: packet())
    return config, target


def test_atomic_publish_and_reader_parity(setup):
    config, target = setup
    runner.run(config, target)
    assert read_media_snapshot(target / "snapshot.json", "sonarr/overview") == packet()
    old_inode = (target / "snapshot.json").stat().st_ino
    runner.run(config, target)
    assert (target / "snapshot.json").stat().st_ino != old_inode
    assert (target / "snapshot.json").stat().st_mode & 0o777 == 0o600
    assert (target / "snapshot.json").stat().st_uid == os.geteuid()
    assert not list(target.glob('.pending-*'))


@pytest.mark.parametrize("mode", [0o775, 0o666, 0o620])
def test_reject_writable_existing_key_source(setup, mode):
    config, target = setup
    config.chmod(mode)
    with pytest.raises(MediaResourceUnavailable, match='^media_resource_unavailable$'):
        runner.run(config, target)
    assert not (target / "snapshot.json").exists()


def test_reject_writable_config_parent(setup):
    config, target = setup
    config.parent.chmod(0o775)
    with pytest.raises(MediaResourceUnavailable):
        runner.run(config, target)


@pytest.mark.parametrize("xml", [
    '<Config><ApiKey>bad</ApiKey><Port>8989</Port></Config>',
    '<Config><ApiKey>' + 'a'*32 + '</ApiKey><Port>1234</Port></Config>',
    '<!DOCTYPE Config [<!ENTITY x "boom">]><Config />',
    '<Config><ApiKey>' + 'a'*32 + '</ApiKey><ApiKey>' + 'b'*32 + '</ApiKey><Port>8989</Port></Config>',
    '<Config><ApiKey>' + 'a'*32 + '</ApiKey><Port>8989</Port><UrlBase>/other</UrlBase></Config>',
    'x' * 65537,
])
def test_invalid_config_never_publishes(setup, xml):
    config, target = setup
    config.write_text(xml)
    with pytest.raises(MediaResourceUnavailable):
        runner.run(config, target)


def test_config_symlink_and_hardlink_denied(setup):
    config, target = setup
    link = config.with_name('link')
    link.symlink_to(config)
    with pytest.raises(MediaResourceUnavailable):
        runner.run(link, target)
    link.unlink()
    os.link(config, link)
    with pytest.raises(MediaResourceUnavailable):
        runner.run(config, target)


def _hang(config, channel):
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    time.sleep(30)


def test_hard_timeout_kills_noncooperative_child_preserves_old(setup, monkeypatch):
    config, target = setup
    runner.run(config, target)
    old = (target / 'snapshot.json').read_bytes()
    before = {p.pid for p in multiprocessing.active_children()}
    monkeypatch.setattr(runner, '_collect_child', _hang)
    start = time.monotonic()
    with pytest.raises(MediaResourceUnavailable):
        runner.run(config, target, timeout=0.2)
    assert time.monotonic() - start < 3
    assert {p.pid for p in multiprocessing.active_children()} == before
    assert (target / 'snapshot.json').read_bytes() == old


def test_exception_output_is_suppressed_and_old_preserved(setup, monkeypatch, capfd):
    config, target = setup
    runner.run(config, target)
    old = (target / 'snapshot.json').read_bytes()
    def bad(key):
        print('PRIVATE ' + key)
        raise RuntimeError('PRIVATE ' + key)
    monkeypatch.setattr(runner, 'SonarrOverviewCollector', bad)
    with pytest.raises(MediaResourceUnavailable) as exc:
        runner.run(config, target)
    assert str(exc.value) == 'media_resource_unavailable'
    assert 'PRIVATE' not in str(capfd.readouterr())
    assert (target / 'snapshot.json').read_bytes() == old


def test_partial_packet_never_replaces_old(setup, monkeypatch):
    config, target = setup
    runner.run(config, target)
    old = (target / 'snapshot.json').read_bytes()
    monkeypatch.setattr(runner, 'SonarrOverviewCollector', lambda key: lambda _: {'complete': False})
    with pytest.raises(MediaResourceUnavailable):
        runner.run(config, target)
    assert (target / 'snapshot.json').read_bytes() == old


def test_overlap_rejected(setup):
    config, target = setup
    lock = os.open(target / '.runner.lock', os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(MediaResourceUnavailable):
            runner.run(config, target)
    finally:
        os.close(lock)


@pytest.mark.parametrize('kind', ['directory_mode', 'directory_symlink', 'snapshot_symlink', 'snapshot_hardlink', 'snapshot_mode', 'lock_symlink'])
def test_unsafe_targets_rejected(setup, kind):
    config, target = setup
    runner.run(config, target)
    snap = target / 'snapshot.json'
    if kind == 'directory_mode':
        target.chmod(0o750)
    elif kind == 'directory_symlink':
        link = target.with_name('linked'); link.symlink_to(target); target = link
    elif kind == 'snapshot_symlink':
        snap.unlink(); snap.symlink_to(config)
    elif kind == 'snapshot_hardlink':
        os.link(snap, target / 'other')
    elif kind == 'snapshot_mode':
        snap.chmod(0o644)
    else:
        lock = target / '.runner.lock'; lock.unlink(); lock.symlink_to(config)
    original_config = config.read_bytes()
    with pytest.raises(MediaResourceUnavailable):
        runner.run(config, target)
    assert config.read_bytes() == original_config


def test_replace_failure_preserves_old_and_cleans_only_own_temp(setup, monkeypatch):
    config, target = setup
    runner.run(config, target)
    old = (target / 'snapshot.json').read_bytes()
    retained = target / '.pending-existing-evidence'
    retained.write_text('retain')
    def fail(*a, **k):
        raise OSError('private')
    monkeypatch.setattr(os, 'replace', fail)
    with pytest.raises(MediaResourceUnavailable):
        runner.run(config, target)
    assert (target / 'snapshot.json').read_bytes() == old
    assert list(target.glob('.pending-*')) == [retained]
