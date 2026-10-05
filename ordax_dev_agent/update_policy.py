"""Helpers for safe managed-agent Git preflight checks."""
from __future__ import annotations

import argparse
import json
import subprocess
import tomllib
import os
from pathlib import Path
from typing import Any


_PROJECT_INSTALL_KEYS = (
    "requires-python",
    "dependencies",
    "optional-dependencies",
    "scripts",
    "gui-scripts",
    "entry-points",
    "dynamic",
)
_SETUPTOOLS_INSTALL_KEYS = (
    "package-dir",
    "packages",
    "py-modules",
)


def install_contract(pyproject_text: str) -> dict[str, Any]:
    try:
        data = tomllib.loads(pyproject_text)
    except (tomllib.TOMLDecodeError, TypeError) as error:
        raise ValueError(f"invalid pyproject.toml: {error}") from error

    project = data.get("project") or {}
    build_system = data.get("build-system") or {}
    setuptools = ((data.get("tool") or {}).get("setuptools") or {})

    return {
        "build-system": build_system,
        "project": {
            key: project[key]
            for key in _PROJECT_INSTALL_KEYS
            if key in project
        },
        "tool.setuptools": {
            key: setuptools[key]
            for key in _SETUPTOOLS_INSTALL_KEYS
            if key in setuptools
        },
    }


def install_contract_changed(before_text: str, after_text: str) -> bool:
    return install_contract(before_text) != install_contract(after_text)


def install_contract_changed_between_refs(
    repo: str | Path,
    before_ref: str,
    after_ref: str,
    *,
    timeout: int = 30,
) -> dict[str, Any]:
    root = Path(repo).resolve()
    git = ["git", "-c", "core.fsmonitor=false", "-C", str(root)]

    def read_pyproject(ref: str) -> tuple[str | None, str | None]:
        try:
            completed = subprocess.run(
                [*git, "show", f"{ref}:pyproject.toml"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=timeout,
                shell=False,
            )
        except subprocess.TimeoutExpired as error:
            return None, f"timed out after {error.timeout} seconds"
        if completed.returncode != 0:
            return None, completed.stderr[-4000:]
        return completed.stdout, None

    before_text, before_error = read_pyproject(before_ref)
    after_text, after_error = read_pyproject(after_ref)
    if before_error or after_error or before_text is None or after_text is None:
        return {
            "ok": False,
            "changed": None,
            "before_ref": before_ref,
            "after_ref": after_ref,
            "before_error": before_error,
            "after_error": after_error,
        }

    try:
        changed = install_contract_changed(before_text, after_text)
    except ValueError as error:
        return {
            "ok": False,
            "changed": None,
            "before_ref": before_ref,
            "after_ref": after_ref,
            "error": str(error),
        }

    return {
        "ok": True,
        "changed": changed,
        "before_ref": before_ref,
        "after_ref": after_ref,
    }


def staged_index_check(
    repo: str | Path,
    *,
    timeout: int = 30,
) -> dict[str, Any]:
    """Compare the staged index tree with HEAD without scanning the worktree."""
    root = Path(repo).resolve()
    git = ["git", "-c", "core.fsmonitor=false", "-C", str(root)]

    try:
        index_tree = subprocess.run(
            [*git, "write-tree"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
            shell=False,
        )
    except subprocess.TimeoutExpired as error:
        return {
            "ok": False,
            "clean": False,
            "error": f"git write-tree timed out after {error.timeout} seconds",
        }

    if index_tree.returncode != 0:
        return {
            "ok": False,
            "clean": False,
            "returncode": index_tree.returncode,
            "error": index_tree.stderr[-4000:],
        }

    try:
        head_tree = subprocess.run(
            [*git, "rev-parse", "HEAD^{tree}"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
            shell=False,
        )
    except subprocess.TimeoutExpired as error:
        return {
            "ok": False,
            "clean": False,
            "error": f"HEAD tree lookup timed out after {error.timeout} seconds",
        }

    if head_tree.returncode != 0:
        return {
            "ok": False,
            "clean": False,
            "returncode": head_tree.returncode,
            "error": head_tree.stderr[-4000:],
        }

    index_sha = index_tree.stdout.strip().lower()
    head_sha = head_tree.stdout.strip().lower()
    if not index_sha or not head_sha:
        return {
            "ok": False,
            "clean": False,
            "error": "Git returned an empty tree hash",
        }

    return {
        "ok": True,
        "clean": index_sha == head_sha,
        "index_tree": index_sha,
        "head_tree": head_sha,
        "method": "index-tree-hash",
    }

def tracked_worktree_check(
    repo: str | Path,
    *,
    timeout: int = 60,
) -> dict[str, Any]:
    """Compare tracked worktree content with the index without refreshing it."""
    root = Path(repo).resolve()
    git = ["git", "-c", "core.fsmonitor=false", "-C", str(root)]

    try:
        listed = subprocess.run(
            [*git, "ls-files", "--stage", "-z"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            shell=False,
        )
    except subprocess.TimeoutExpired as error:
        return {
            "ok": False,
            "clean": False,
            "error": f"tracked file listing timed out after {error.timeout} seconds",
        }

    if listed.returncode != 0:
        return {
            "ok": False,
            "clean": False,
            "returncode": listed.returncode,
            "error": listed.stderr.decode("utf-8", errors="replace")[-4000:],
        }

    paths: list[bytes] = []
    expected: list[bytes] = []
    changed_paths: list[str] = []
    unsupported_paths: list[str] = []

    for record in listed.stdout.split(b"\0"):
        if not record:
            continue
        try:
            metadata, raw_path = record.split(b"\t", 1)
            mode, object_id, stage = metadata.split(b" ", 2)
        except ValueError:
            return {
                "ok": False,
                "clean": False,
                "error": "could not parse git ls-files --stage output",
            }

        display_path = os.fsdecode(raw_path)
        if stage != b"0":
            changed_paths.append(display_path)
            continue
        if mode not in {b"100644", b"100755"}:
            unsupported_paths.append(display_path)
            continue
        if b"\n" in raw_path or b"\r" in raw_path:
            unsupported_paths.append(display_path)
            continue

        candidate = root / display_path
        if not candidate.is_file():
            changed_paths.append(display_path)
            continue

        paths.append(raw_path)
        expected.append(object_id.lower())

    if unsupported_paths:
        return {
            "ok": False,
            "clean": False,
            "error": "tracked worktree contains unsupported path modes/names",
            "unsupported_paths": unsupported_paths[:100],
        }

    if changed_paths:
        return {
            "ok": True,
            "clean": False,
            "changed_paths": sorted(set(changed_paths))[:200],
            "method": "index-object-hash",
        }

    if not paths:
        return {
            "ok": True,
            "clean": True,
            "changed_paths": [],
            "tracked_files": 0,
            "method": "index-object-hash",
        }

    input_paths = b"\n".join(paths) + b"\n"
    try:
        hashed = subprocess.run(
            [*git, "hash-object", "--stdin-paths"],
            input=input_paths,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            shell=False,
        )
    except subprocess.TimeoutExpired as error:
        return {
            "ok": False,
            "clean": False,
            "error": f"tracked file hashing timed out after {error.timeout} seconds",
        }

    if hashed.returncode != 0:
        return {
            "ok": False,
            "clean": False,
            "returncode": hashed.returncode,
            "error": hashed.stderr.decode("utf-8", errors="replace")[-4000:],
        }

    actual = [
        line.strip().lower()
        for line in hashed.stdout.splitlines()
        if line.strip()
    ]
    if len(actual) != len(expected):
        return {
            "ok": False,
            "clean": False,
            "error": "tracked file hash count did not match index entry count",
            "expected_count": len(expected),
            "actual_count": len(actual),
        }

    for raw_path, expected_id, actual_id in zip(paths, expected, actual):
        if expected_id != actual_id:
            changed_paths.append(os.fsdecode(raw_path))

    return {
        "ok": True,
        "clean": not changed_paths,
        "changed_paths": sorted(set(changed_paths))[:200],
        "tracked_files": len(paths),
        "method": "index-object-hash",
    }


def managed_repo_clean_check(
    repo: str | Path,
    *,
    worktree_timeout: int = 60,
    staged_timeout: int = 30,
) -> dict[str, Any]:
    worktree = tracked_worktree_check(repo, timeout=worktree_timeout)
    staged = staged_index_check(repo, timeout=staged_timeout)
    ok = bool(worktree.get("ok")) and bool(staged.get("ok"))
    clean = ok and bool(worktree.get("clean")) and bool(staged.get("clean"))
    return {
        "ok": ok,
        "clean": clean,
        "worktree": worktree,
        "staged": staged,
        "method": "index-object-hash+index-tree-hash",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check-clean", metavar="REPO")
    mode.add_argument("--compare-install-contract", metavar="REPO")
    parser.add_argument("--before-ref")
    parser.add_argument("--after-ref")
    args = parser.parse_args(argv)

    if args.check_clean:
        result = managed_repo_clean_check(args.check_clean)
        print(json.dumps(result, separators=(",", ":")))
        if not result.get("ok"):
            return 2
        return 0 if result.get("clean") else 1

    if not args.before_ref or not args.after_ref:
        parser.error(
            "--compare-install-contract requires --before-ref and --after-ref"
        )
    result = install_contract_changed_between_refs(
        args.compare_install_contract,
        args.before_ref,
        args.after_ref,
    )
    print(json.dumps(result, separators=(",", ":")))
    if not result.get("ok"):
        return 2
    return 1 if result.get("changed") else 0


if __name__ == "__main__":
    raise SystemExit(main())
