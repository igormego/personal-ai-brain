"""Offline candidate scope and exact accepted-baseline guards."""
import importlib.util
import json
from pathlib import Path
import sys
import subprocess

import pytest

from personal_ai_brain.control import Redactor, Rejected

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('candidate_builder', ROOT / 'scripts/prepare_sonarr63_candidate.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)
SOURCE = ROOT / 'src/personal_ai_brain'


def baseline(tmp_path):
    # Reconstruct the known accepted files from repository source, then require
    # their independently host-verified SHA-256 values in the production builder.
    directory = tmp_path / 'baseline'
    directory.mkdir()
    ctl = (SOURCE / 'control.py').read_text()
    start = ctl.index('    # Source-only opt-in:')
    end = ctl.index('    if work_client is not None:', start)
    ctl = ctl[:start] + ctl[end:]
    ctl = ctl.replace('def build_registry(core_state: Path, reader_factory, redactor, work_client=None,\n'
                      '                   media_plan_provider=None, host_audit_snapshot: Path | None = None,\n'
                      '                   *, media_resource_provider=None):',
                      'def build_registry(core_state: Path, reader_factory, redactor, work_client=None, *, host_audit_snapshot: Path | None = None):')
    worker = builder.without_arr_worker((SOURCE / 'control_worker.py').read_text())
    start = worker.index('    # Explicitly opted-in, fixed, read-only mount.')
    end = worker.index('    def pulse():', start)
    worker = worker[:start] + ('    registry = build_registry(Path("/core-state"), lambda: RunReader(core_token),\n'
                              '                              redactor, Client(), host_audit_snapshot=audit_file)\n') + worker[end:]
    (directory / 'control.py').write_text(ctl, newline='\n')
    (directory / 'control_worker.py').write_text(worker, newline='\n')
    return directory


def test_candidate_includes_only_approved_control_delta(tmp_path):
    out = tmp_path / 'candidate'
    manifest = builder.prepare(baseline(tmp_path), SOURCE, out)
    assert manifest['candidate_image'] is None
    assert set(manifest['files']) == set(builder.MODULES) | {'Dockerfile'}
    assert 'sonarr_overview' not in (out / 'Dockerfile').read_text()
    assert 'sonarr_snapshot_runner' not in (out / 'Dockerfile').read_text()
    # ARR opt-in/auth code belongs to a separate reviewed deployment, not
    # the exact accepted Sonarr63 candidate baseline.
    assert 'PAB_ARR_SONARR_ENABLED' not in (out / 'control_worker.py').read_text()
    assert 'owner_auth_required' not in (out / 'control_worker.py').read_text()
    spec = importlib.util.spec_from_file_location('personal_ai_brain._candidate_control', out / 'control.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
        default = module.build_registry(tmp_path, None, Redactor())
        enabled = module.build_registry(tmp_path, None, Redactor(), media_resource_provider=lambda _: {})
        assert set(enabled.capabilities) - set(default.capabilities) == {'media.resource_read'}
        assert 'media.plan_movie' not in enabled.capabilities
    finally:
        del sys.modules[spec.name]
    assert json.loads((out / 'manifest.json').read_text()) == manifest
    with pytest.raises(FileExistsError):
        builder.prepare(tmp_path / 'baseline', SOURCE, out)


def test_changed_baseline_rejected_before_output(tmp_path):
    base = baseline(tmp_path)
    with (base / 'control.py').open('a') as stream:
        stream.write('# drift\n')
    with pytest.raises(ValueError, match='candidate_baseline_mismatch'):
        builder.prepare(base, SOURCE, tmp_path / 'candidate')
    assert not (tmp_path / 'candidate').exists()


def test_isolated_host_bundle_bootstrap_has_no_control_modules(tmp_path):
    output = tmp_path / 'host'
    manifest = builder.prepare_host(SOURCE, ROOT / 'ops/sonarr63/run.py', output)
    assert len(manifest) == 6
    assert not (output / 'lib/personal_ai_brain/control.py').exists()
    result = subprocess.run([sys.executable, '-I', '-B', str(output / 'run.py'), '--help'],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0
    assert '--directory' in result.stdout
