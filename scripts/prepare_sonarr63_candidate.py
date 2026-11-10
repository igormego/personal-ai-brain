"""Offline-only minimal image context; never invokes Docker or touches a host.

Input: the two independently verified accepted runtime modules, not a checkout
update. Output: four control-only modules, manifest and a pinned-base Dockerfile.
"""
import argparse
import hashlib
import json
from pathlib import Path

BASE_IMAGE = "sha256:1ca7a20c4de75abe60c2d75037d943a960b438cf2a1c0829f6d501370541f538"
BASE_HASHES = {
    "control.py": "8e55357a7fe578878c981614cbbb0abb5b8c01424e9b959a312c5c3b9f5e8b8e",
    "control_worker.py": "8162440f72aed412ad96849eacbfad97b6ede732b2a4d7f3881416cedff45fff",
}
MODULES = ("control.py", "control_worker.py", "assistant_media_resources.py", "media_snapshot.py")
HOST_MODULES = ("__init__.py", "sonarr_snapshot_runner.py", "sonarr_overview.py",
                "assistant_media_resources.py", "media_snapshot.py")


def replace_once(text, old, new):
    if text.count(old) != 1:
        raise ValueError("candidate_baseline_mismatch")
    return text.replace(old, new, 1)


def without_arr_worker(text):
    """Exclude the newer independent ARR read lane from the pinned Sonarr63 image.

    This restores the *exact* previously accepted worker source before the
    Sonarr63-only synthesis. The original immutable BASE_HASHES gate stays
    authoritative; no ARR code/credentials/owner guard enters that candidate.
    """
    authorization = """                # Title-level ARR inventory is persistently disclosed in private
                # Issues + the durable ledger. requested_by is user-controlled
                # metadata, never proof of owner identity. Bind to GitHub's
                # authenticated issue author on every newly admitted request.
                if (capability.id == "media.resource_read"
                        and arguments.get("resource_id") in ("arr/radarr", "arr/sonarr")):
                    author = issue.get("user")
                    if (type(author) is not dict
                            or type(author.get("id")) is not int
                            or author["id"] != 55916200
                            or author.get("login") != "igormego"):
                        raise Rejected("owner_auth_required")
"""
    arr_injection_start = "        # ARR item/title disclosure is an independent, OFF-by-default"
    arr_injection_end = "\n    registry = build_registry("
    if text.count(authorization) != 1 or text.count(arr_injection_start) != 1:
        raise ValueError("candidate_baseline_mismatch")
    text = text.replace(authorization, "", 1)
    start = text.index(arr_injection_start)
    end = text.index(arr_injection_end, start)
    # The ARR splice was inserted after an existing blank line; remove
    # its trailing newline too so the accepted baseline is byte-identical.
    return text[:start] + text[end + 1:]


def sources(baseline, current):
    result = {}
    for name, expected in BASE_HASHES.items():
        raw = (baseline / name).read_bytes()
        if hashlib.sha256(raw).hexdigest() != expected:
            raise ValueError("candidate_baseline_mismatch")
        result[name] = raw.decode()
    control = (current / "control.py").read_text()
    start = control.index("    # Source-only opt-in:")
    end = control.index("    if media_plan_provider is not None:", start)
    result["control.py"] = replace_once(
        result["control.py"],
        "*, host_audit_snapshot: Path | None = None):",
        "*, host_audit_snapshot: Path | None = None, media_resource_provider=None):")
    result["control.py"] = replace_once(result["control.py"],
        "    if work_client is not None:\n", control[start:end] + "    if work_client is not None:\n")
    worker = without_arr_worker((current / "control_worker.py").read_text())
    start = worker.index("    # Explicitly opted-in, fixed, read-only mount.")
    end = worker.index("    def pulse():", start)
    result["control_worker.py"] = replace_once(result["control_worker.py"],
        '    registry = build_registry(Path("/core-state"), lambda: RunReader(core_token),\n'
        '                              redactor, Client(), host_audit_snapshot=audit_file)\n',
        worker[start:end])
    for name in MODULES[2:]:
        result[name] = (current / name).read_text()
    if "media.plan_movie" in result["control.py"]:
        raise ValueError("unrelated_capability")
    for text in result.values():
        compile(text, "candidate", "exec")
    return result


def prepare(baseline, current, output):
    files = sources(baseline, current)
    output.mkdir(mode=0o700)  # Never overwrite an earlier candidate/evidence.
    for name, text in files.items():
        (output / name).write_text(text, encoding="utf-8", newline="\n")
    dockerfile = f"FROM {BASE_IMAGE}\nCOPY --chown=0:0 --chmod=0644 " + " ".join(MODULES)
    dockerfile += " /usr/local/lib/python3.14/site-packages/personal_ai_brain/\n"
    (output / "Dockerfile").write_text(dockerfile, encoding="utf-8", newline="\n")
    manifest = {"base_image": BASE_IMAGE, "base_modules": BASE_HASHES,
                "candidate_image": None,
                "files": {name: hashlib.sha256((output / name).read_bytes()).hexdigest()
                          for name in (*MODULES, "Dockerfile")}}
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def prepare_host(current, entrypoint, output):
    output.mkdir(mode=0o700)
    package = output / "lib/personal_ai_brain"
    package.mkdir(parents=True)
    for name in HOST_MODULES:
        (package / name).write_text((current / name).read_text(), encoding="utf-8", newline="\n")
    (output / "run.py").write_text(entrypoint.read_text(), encoding="utf-8", newline="\n")
    paths = [output / "run.py", *(package / name for name in HOST_MODULES)]
    manifest = {p.relative_to(output).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--runner-output", type=Path)
    args = parser.parse_args()
    prepare(args.baseline, args.source, args.output)
    if args.runner_output:
        prepare_host(args.source, Path(__file__).resolve().parents[1] / "ops/sonarr63/run.py", args.runner_output)
