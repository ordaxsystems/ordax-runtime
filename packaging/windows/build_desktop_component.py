"""Build an unsigned component candidate, never an installer or activation receipt."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]


def run(*args: str, **kwargs) -> str:
    return subprocess.check_output(args, text=True, **kwargs).strip()


def main() -> None:
    if sys.platform != "win32" or sys.maxsize <= 2**32:
        raise RuntimeError("Build requires Windows amd64")
    os.chdir(ROOT)
    source = run("git", "rev-parse", "HEAD")
    if not re.fullmatch(r"[0-9a-f]{40}", source):
        raise RuntimeError("Exact Runtime source required")
    if run("git", "status", "--porcelain", "--untracked-files=no"):
        raise RuntimeError("Tracked Runtime source must be clean")
    expected_source = os.environ.get("GITHUB_SHA", source)
    if os.environ.get("GITHUB_EVENT_NAME") == "pull_request":
        event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8"))
        expected_source = event["pull_request"]["head"]["sha"]
    if expected_source != source:
        raise RuntimeError("CI source binding mismatch")
    output = ROOT / "dist" / "desktop-component"
    output.mkdir(parents=True, exist_ok=True)
    build = ROOT / "build" / "desktop-component"
    build.mkdir(parents=True, exist_ok=True)
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--onefile",
                    "--noupx", "--name", "ordax-product-agent", "--distpath", str(output),
                    "--workpath", str(build / "work"), "--specpath", str(build),
                    "--paths", str(ROOT), "--copy-metadata", "ordax-runtime",
                    "--collect-data", "ordax_dev_agent", "--exclude-module", "ordax_studio",
                    "--exclude-module", "webview", "--exclude-module", "PySide6",
                    str(ROOT / "packaging/windows/desktop_entrypoint.py")], check=True)
    binary = output / "ordax-product-agent.exe"
    from PyInstaller.archive.readers import CArchiveReader
    import pefile
    pe = pefile.PE(str(binary), fast_load=True)
    if pe.FILE_HEADER.Machine != 0x8664 or pe.OPTIONAL_HEADER.Magic != 0x20B:
        raise RuntimeError("Candidate must be a PE32+ amd64 executable")
    pe.close()
    archive = CArchiveReader(str(binary))
    modules = archive.open_embedded_archive("PYZ.pyz").toc
    if any(name == "ordax_studio" or name.startswith(("ordax_studio.", "webview", "PySide6"))
           for name in modules):
        raise RuntimeError("Studio or UI dependency leaked into Desktop component")
    required = {"ordax_dev_agent.desktop_host_main", "ordax_dev_agent.main",
                "ordax_dev_agent.product_device_control_plane",
                "ordax_dev_agent.product_central_session"}
    if not required.issubset(modules):
        raise RuntimeError("Canonical Runtime closure incomplete")
    # Execute only the copied .exe: no checkout, Python, Git, Studio, identity,
    # inherited agent environment or installed package directory on PATH.
    with tempfile.TemporaryDirectory(prefix="ordax-desktop-isolated-") as tmp:
        isolated = Path(tmp)
        executable = isolated / binary.name
        shutil.copyfile(binary, executable)
        env = {key: os.environ[key] for key in ("SystemRoot", "WINDIR", "TEMP", "TMP")
               if key in os.environ}
        env.update(PATH=str(Path(os.environ["SystemRoot"]) / "System32"),
                   USERPROFILE=str(isolated), LOCALAPPDATA=str(isolated / "local"),
                   APPDATA=str(isolated / "roaming"), PYTHONNOUSERSITE="1")
        proof = json.loads(run(str(executable), "--self-test", cwd=isolated, env=env, timeout=90))
        if (proof.get("ok") is not True or proof.get("profile") != "ordax-desktop"
                or proof.get("actions") != ["agent.status", "computer.access_status"]
                or proof.get("network_used") is not False
                or proof.get("device_credential_used") is not False
                or proof.get("remote_action_proven") is not False):
            raise RuntimeError("Offline frozen proof invalid")
        for profile in (None, "windows-agent", "ordax-desktop"):
            launch_env = dict(env)
            if profile is not None:
                launch_env["ORDAX_RUNTIME_HOST_PROFILE"] = profile
            result = subprocess.run([str(executable)], cwd=isolated, env=launch_env,
                                    capture_output=True, text=True, timeout=30)
            expected = "requires OS-owned userData/state" if profile == "ordax-desktop" else "requires the Supervisor host profile"
            if result.returncode == 0 or expected not in result.stderr:
                raise RuntimeError("Unenrolled or foreign-profile launch did not fail closed")
    digest = hashlib.sha256(binary.read_bytes()).hexdigest()
    from desktop_package import package
    release = package(binary, source, proof["runtime_version"])
    manifest = {"schema": "ordax.desktop-runtime-candidate/1",
                "source_repository": "ordaxsystems/ordax-runtime", "source_commit": source,
                "profile": "ordax-desktop", "platform": "windows-amd64",
                "artifact": binary.name, "sha256": digest, "size_bytes": binary.stat().st_size,
                "runtime_version": proof["runtime_version"],
                "build_python_version": sys.version.split()[0],
                "python_required_on_device": False, "studio_required": False,
                "offline_proof_verified": True, "device_execution_proven": False,
                "signature_present": False, "signed_distribution_authorized": False,
                "installation_authorized_by_candidate": False,
                "stable_activation_authorized": False,
                "recipe_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                "requirements_sha256": hashlib.sha256((ROOT / "packaging/windows/desktop-requirements.lock").read_bytes()).hexdigest()}
    manifest["component_package"] = release["package"]
    (output / "self-test.json").write_text(json.dumps(proof, indent=2) + "\n", encoding="utf-8")
    (output / "candidate.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    (output / "SHA256SUMS").write_text(f"{digest}  {binary.name}\n", encoding="ascii")
    print(json.dumps(manifest, sort_keys=True))


if __name__ == "__main__":
    main()
