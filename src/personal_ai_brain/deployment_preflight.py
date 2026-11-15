"""Read-only, default-deny checks for the Issue 83 fixture-1 manifest.

No deployment, Compose invocation, permission change or ledger access. A trusted
operator must separately establish normalized-config provenance and protect the
input tree from concurrent change. This result is not a deployment authorization.
Linux only: descriptor-relative, no-follow filesystem access is mandatory.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import PurePosixPath
import re
import stat

MAX_INPUT = 1024 * 1024
HASH = re.compile(r"[0-9a-f]{64}\Z")
MODULE = re.compile(r"[a-z_]+\.py\Z")
ACTIONS = ["stop_control", "stop_builder", "start_builder", "start_control"]


class PreflightDenied(ValueError):
    """Fixed error: never expose private paths or parsed configuration."""


def deny():
    raise PreflightDenied("deployment_preflight_denied")


def unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            deny()
        result[key] = value
    return result


def obj(value, keys):
    if type(value) is not dict or set(value) != set(keys):
        deny()


def sha(value):
    if type(value) is not str or not HASH.fullmatch(value):
        deny()


def path_parts(path, *, absolute):
    if type(path) is not str or not path or "\x00" in path:
        deny()
    p = PurePosixPath(path)
    parts = p.parts[1:] if absolute else p.parts
    if p.is_absolute() != absolute or not parts or any(x in (".", "..") for x in path.split("/")) or str(p) != path:
        deny()
    return parts


def check_dir(fd, uid):
    try:
        s = os.fstat(fd)
    except OSError:
        deny()
    if not stat.S_ISDIR(s.st_mode) or s.st_uid != uid or s.st_mode & 0o022:
        deny()


def open_beneath(root_fd, parts, uid, *, directory=False):
    """Open one path below a caller-pinned trusted directory; never follow links."""
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
        deny()
    check_dir(root_fd, uid)
    try:
        current = os.dup(root_fd)
    except OSError:
        deny()
    try:
        for index, part in enumerate(parts):
            is_dir = index < len(parts) - 1 or directory
            flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_CLOEXEC", 0) | (os.O_DIRECTORY if is_dir else 0)
            child = os.open(part, flags, dir_fd=current)
            os.close(current)
            current = child
            s = os.fstat(current)
            if is_dir:
                check_dir(current, uid)
            elif not stat.S_ISREG(s.st_mode) or s.st_uid != uid or s.st_nlink != 1 or stat.S_IMODE(s.st_mode) != 0o600 or s.st_size > MAX_INPUT:
                deny()
        return current
    except (OSError, ValueError):
        os.close(current)
        deny()
    except BaseException:
        os.close(current)
        raise


def read_file(root_fd, path, uid):
    fd = open_beneath(root_fd, path_parts(path, absolute=True), uid)
    try:
        try:
            before = os.fstat(fd)
            chunks = []
            remaining = MAX_INPUT + 1
            while remaining:
                part = os.read(fd, min(65536, remaining))
                if not part:
                    break
                chunks.append(part)
                remaining -= len(part)
            data = b"".join(chunks)
            after = os.fstat(fd)
            if len(data) > MAX_INPUT or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                deny()
            return data
        except OSError:
            deny()
    finally:
        os.close(fd)


def parse_manifest(data):
    try:
        m = json.loads(data, object_pairs_hook=unique, parse_constant=lambda _: deny())
        obj(m, ("schema_version", "source_image", "target_image", "project_directory", "environment", "layers", "modules", "profile", "supervisor_actions", "ledger", "recovery"))
        if m["schema_version"] != "fixture-1": deny()
        for key in ("source_image", "target_image"):
            if type(m[key]) is not str or not m[key].startswith("sha256:"): deny()
            sha(m[key][7:])
        path_parts(m["project_directory"], absolute=True)
        for key in ("environment", "profile"):
            item = m[key]
            obj(item, ("reference", "sha256") if key == "environment" else ("reference", "sha256", "fresh_baseline"))
            path_parts(item["reference"], absolute=True)
            sha(item["sha256"])
        if m["profile"]["fresh_baseline"] is not True: deny()
        if type(m["layers"]) is not list or len(m["layers"]) != 5: deny()
        names = []
        for layer in m["layers"]:
            obj(layer, ("path", "sha256"))
            if len(path_parts(layer["path"], absolute=False)) != 1: deny()
            sha(layer["sha256"])
            names.append(layer["path"])
        if len(set(names)) != 5: deny()
        if type(m["modules"]) is not list or not 1 <= len(m["modules"]) <= 64: deny()
        module_names = []
        for item in m["modules"]:
            obj(item, ("name", "before_sha256", "after_sha256"))
            if type(item["name"]) is not str or not MODULE.fullmatch(item["name"]): deny()
            sha(item["before_sha256"]); sha(item["after_sha256"])
            module_names.append(item["name"])
        if len(set(module_names)) != len(module_names) or m["supervisor_actions"] != ACTIONS: deny()
        obj(m["ledger"], ("preserve_current", "restore_historical"))
        if m["ledger"]["preserve_current"] is not True or m["ledger"]["restore_historical"] is not False: deny()
        obj(m["recovery"], ("retry_uncertain", "rollback_uncertain", "require_review_on_failed_rollback"))
        if m["recovery"]["retry_uncertain"] is not False or m["recovery"]["rollback_uncertain"] is not False or m["recovery"]["require_review_on_failed_rollback"] is not True: deny()
        return m
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError):
        deny()


def check_references(root_fd, config, project, uid):
    """Validate supported normalized bind and secret references, or deny.

    External resources, configs, includes and env_file are outside this narrow
    contract. Compose-generated paths must remain inside the pinned project tree.
    """
    if type(config) is not dict or type(config.get("services")) is not dict:
        deny()
    if config.get("configs") or config.get("include"):
        deny()
    def within_project(value):
        path_parts(value, absolute=True)
        if not value.startswith(project + "/"):
            deny()
        return path_parts(value, absolute=True)
    for service in config["services"].values():
        if type(service) is not dict or service.get("env_file") or service.get("extends") or service.get("configs"):
            deny()
        for volume in service.get("volumes", []):
            if type(volume) is not dict or volume.get("type") != "bind" or type(volume.get("source")) is not str:
                deny()
            fd = open_beneath(root_fd, within_project(volume["source"]), uid, directory=True)
            os.close(fd)
    for secret in (config.get("secrets") or {}).values():
        if type(secret) is not dict or set(secret) != {"file"}:
            deny()
        # A reference is validated, but contents are deliberately not retained.
        fd = open_beneath(root_fd, within_project(secret["file"]), uid)
        os.close(fd)


def preflight(root_fd, manifest_path, *, owner_uid, expected_layer_order, original, candidate):
    """Check pinned private inputs and strict image-only normalized-config diff.

    Paths in fixture-1 are absolute *within* root_fd; root_fd is independently
    opened and pinned by the trusted caller. The caller supplies the separately
    reviewed order and normalized snapshots. This function does not establish
    snapshot provenance or protect against later filesystem changes.
    """
    if type(owner_uid) is not int or owner_uid < 0 or type(expected_layer_order) is not tuple:
        deny()
    m = parse_manifest(read_file(root_fd, manifest_path, owner_uid))
    if tuple(x["path"] for x in m["layers"]) != expected_layer_order: deny()
    root = m["project_directory"]
    fd = open_beneath(root_fd, path_parts(root, absolute=True), owner_uid, directory=True)
    os.close(fd)
    for item in (m["environment"], m["profile"]):
        if hashlib.sha256(read_file(root_fd, item["reference"], owner_uid)).hexdigest() != item["sha256"]: deny()
    for item in m["layers"]:
        if hashlib.sha256(read_file(root_fd, root + "/" + item["path"], owner_uid)).hexdigest() != item["sha256"]: deny()
    try:
        # Round-trip also rejects NaN, non-JSON objects, recursive and ambiguous data.
        original = json.loads(json.dumps(original, allow_nan=False))
        candidate = json.loads(json.dumps(candidate, allow_nan=False))
        check_references(root_fd, original, root, owner_uid)
        check_references(root_fd, candidate, root, owner_uid)
        if original["services"]["control"]["image"] != m["source_image"] or candidate["services"]["control"]["image"] != m["target_image"]: deny()
        original["services"]["control"]["image"] = m["target_image"]
        if json.dumps(original, sort_keys=True, separators=(",", ":"), allow_nan=False) != json.dumps(candidate, sort_keys=True, separators=(",", ":"), allow_nan=False): deny()
    except (KeyError, TypeError, ValueError, RecursionError, AttributeError):
        deny()
    return {"status": "inputs_and_supplied_diff_verified", "deploy_authorized": False,
            "normalization_provenance": "unverified", "baseline_freshness": "self_attested",
            "image_identity": "unverified", "module_contents": "unverified"}
