import hashlib
import inspect
from pathlib import Path
from types import SimpleNamespace
import pytest
from personal_ai_brain import codex_bridge_v02_validation as v

@pytest.fixture
def node(monkeypatch):
    original=Path.lstat
    monkeypatch.setattr(Path,'lstat',lambda self,*a,**k:SimpleNamespace(st_mode=0o100755,st_uid=65534) if self.name=='pab-v02-node-v24.22.1' else original(self,*a,**k))
    monkeypatch.setattr(Path,'read_bytes',lambda self:b'fixture')
    monkeypatch.setattr(hashlib,'sha256',lambda data:SimpleNamespace(hexdigest=lambda:'e32e2d16081cf644f88392934288086e7662529d0d43277c34f7aad2207cfafb'))
    monkeypatch.setattr(v.subprocess,'run',lambda *a,**k:SimpleNamespace(returncode=0,stdout=b'v24.22.1\n'))

def test_pinned_identity_independent_of_namespace_uid(node):
    assert Path(v.pinned_node()).name=='pab-v02-node-v24.22.1'
    assert "'/usr/bin/node'" not in v.validator_script()

def test_wrong_digest(node,monkeypatch):
    monkeypatch.setattr(hashlib,'sha256',lambda data:SimpleNamespace(hexdigest=lambda:'bad'))
    with pytest.raises(RuntimeError,match='pinned_node_digest'):v.pinned_node()

@pytest.mark.parametrize('version',[b'v20.19.2\n',b'v24.22.0\n',b'v24.22.1\nextra'])
def test_wrong_version(node,monkeypatch,version):
    monkeypatch.setattr(v.subprocess,'run',lambda *a,**k:SimpleNamespace(returncode=0,stdout=version))
    with pytest.raises(RuntimeError,match='pinned_node_version'):v.pinned_node()

def test_missing_binary(node,monkeypatch):
    def missing(self,*a,**k):raise FileNotFoundError()
    monkeypatch.setattr(Path,'lstat',missing)
    with pytest.raises(RuntimeError,match='pinned_node_unavailable'):v.pinned_node()

def test_no_task_path(node):
    with pytest.raises(TypeError):v.pinned_node('/tmp/node')

@pytest.mark.parametrize('uid,mode,writable',[(1000,0o100755,False),(0,0o100777,False),(0,0o120755,False),(0,0o100755,True)])
def test_host_ownership_rejection(monkeypatch,uid,mode,writable):
    import os
    monkeypatch.setattr(Path,'lstat',lambda self:SimpleNamespace(st_uid=uid,st_mode=mode))
    monkeypatch.setattr(os,'access',lambda *a:writable)
    with pytest.raises(RuntimeError,match='pinned_node_host_untrusted'):v.verify_host_node()

@pytest.mark.parametrize('bad_parent',['/usr/local/bin','/usr/local','/usr','/'])
def test_host_checks_each_parent(monkeypatch,bad_parent):
    import os
    def metadata(self):
        file=self.name=='pab-v02-node-v24.22.1'
        return SimpleNamespace(st_uid=1000 if self.as_posix()==bad_parent else 0,st_mode=0o100755 if file else 0o40755)
    monkeypatch.setattr(Path,'lstat',metadata);monkeypatch.setattr(os,'access',lambda *a:False)
    with pytest.raises(RuntimeError,match='pinned_node_host_untrusted'):v.verify_host_node()
