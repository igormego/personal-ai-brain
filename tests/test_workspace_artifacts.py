"""Generic workspace input grants and output invariants (no live worker)."""

from __future__ import annotations

import hashlib
import os

import pytest

from personal_ai_brain.workspace_artifacts import (
    InputGrant,
    WorkspaceArtifactDenied,
    inspect_outputs,
    stage_inputs,
    MAX_FILE_BYTES,
    MAX_FILES,
)


def test_stages_two_distinct_resource_families_without_task_handler(tmp_path):
    root = tmp_path / "inputs"
    grants = (
        InputGrant("project-os/homelab", "states/state.json", b'{"ok":true}'),
        InputGrant("user-files/notes", "notes/example.md", b"# Example\n"),
    )
    records = stage_inputs(root, grants)
    assert [entry.path for entry in records] == ["notes/example.md", "states/state.json"]
    assert (root / "notes/example.md").read_bytes() == b"# Example\n"
    assert records[0].resource_id == "user-files/notes"
    assert records[0].sha256 == hashlib.sha256(b"# Example\n").hexdigest()
    assert all(len(record.sha256) == 64 for record in records)
    assert (root.stat().st_mode & 0o777) == 0o700


@pytest.mark.parametrize("name", [
    "../escape", "/absolute", "x/../../escape", "x//file", "x/./file",
    ".hidden", "a/.private", "x\\bad", "foo\nbar", "x/" * 8 + "file",
])
def test_rejects_unsafe_paths_before_writing(tmp_path, name):
    root = tmp_path / "inputs"
    with pytest.raises(WorkspaceArtifactDenied):
        stage_inputs(root, (InputGrant("user-files/notes", name, b"safe"),))
    assert not root.exists()


def test_rejects_bad_grants_and_duplicates(tmp_path):
    with pytest.raises(WorkspaceArtifactDenied, match="invalid_input_grants"):
        stage_inputs(tmp_path / "inputs", (
            InputGrant("user-files/notes", "same.txt", b"a"),
            InputGrant("user-files/notes", "same.txt", b"b"),
        ))
    with pytest.raises(WorkspaceArtifactDenied, match="invalid_input_grants"):
        stage_inputs(tmp_path / "inputs2", (
            InputGrant("https://host/path", "x.txt", b"a"),
        ))
    with pytest.raises(WorkspaceArtifactDenied, match="invalid_input_grants"):
        stage_inputs(tmp_path / "inputs3", (
            InputGrant("user-files/notes", "x.txt", "not bytes"),
        ))


def test_rejects_preexisting_input_root_and_large_file(tmp_path):
    (tmp_path / "existing").mkdir()
    with pytest.raises(WorkspaceArtifactDenied, match="staging_failed"):
        stage_inputs(tmp_path / "existing", (
            InputGrant("user-files/notes", "x.txt", b"safe"),
        ))
    with pytest.raises(WorkspaceArtifactDenied):
        stage_inputs(tmp_path / "large", (
            InputGrant("user-files/notes", "x.txt", b"x" * (MAX_FILE_BYTES + 1)),
        ))
    assert not (tmp_path / "large").exists()


def test_rejects_too_many_input_files(tmp_path):
    grants = tuple(
        InputGrant("user-files/notes", f"item-{i}.txt", b"x")
        for i in range(MAX_FILES + 1)
    )
    with pytest.raises(WorkspaceArtifactDenied):
        stage_inputs(tmp_path / "inputs", grants)


def test_inspects_generic_task_outputs_in_nested_directories(tmp_path):
    out = tmp_path / "outputs"
    (out / "analysis").mkdir(parents=True)
    (out / "code").mkdir()
    (out / "analysis/report.json").write_text('{"total":2}', encoding="utf-8")
    (out / "code/transform.py").write_text("print('ok')\n", encoding="utf-8")
    items = inspect_outputs(out)
    assert [item.path for item in items] == [
        "analysis/report.json", "code/transform.py",
    ]
    assert all(item.resource_id is None for item in items)
    assert items[0].size == len(b'{"total":2}')


def test_rejects_symlink_output_and_symlink_root(tmp_path):
    root = tmp_path / "outputs"
    root.mkdir()
    (root / "alias").symlink_to(tmp_path / "target")
    with pytest.raises(WorkspaceArtifactDenied, match="invalid_artifact_type"):
        inspect_outputs(root)
    (tmp_path / "root-link").symlink_to(root)
    with pytest.raises(WorkspaceArtifactDenied, match="invalid_output_root"):
        inspect_outputs(tmp_path / "root-link")


def test_rejects_hardlinked_output(tmp_path):
    root = tmp_path / "outputs"
    root.mkdir()
    original = tmp_path / "original"
    original.write_bytes(b"secret")
    os.link(original, root / "linked.txt")
    with pytest.raises(WorkspaceArtifactDenied, match="invalid_artifact_type"):
        inspect_outputs(root)


def test_rejects_fifo_and_too_large_output(tmp_path):
    root = tmp_path / "outputs"
    root.mkdir()
    os.mkfifo(root / "pipe")
    with pytest.raises(WorkspaceArtifactDenied, match="invalid_artifact_type"):
        inspect_outputs(root)
    (root / "pipe").unlink()
    (root / "large").write_bytes(b"x" * (MAX_FILE_BYTES + 1))
    with pytest.raises(WorkspaceArtifactDenied, match="invalid_artifact_type"):
        inspect_outputs(root)


def test_rejects_unbounded_output_file_count(tmp_path):
    root = tmp_path / "outputs"
    root.mkdir()
    for index in range(MAX_FILES + 1):
        (root / f"result-{index}.json").write_text("{}", encoding="utf-8")
    with pytest.raises(WorkspaceArtifactDenied, match="artifact_limit"):
        inspect_outputs(root)


def test_accepts_empty_output_inventory_without_claiming_task_success(tmp_path):
    root = tmp_path / "outputs"
    root.mkdir()
    assert inspect_outputs(root) == ()
