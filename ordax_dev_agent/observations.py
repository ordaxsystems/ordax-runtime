"""Project-scoped visual evidence; each observation has its own immutable paths."""
from __future__ import annotations

import hashlib
import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from mcp_blender_unity.config import find_blender
from mcp_blender_unity.process import run_process

from .models import ActionResult
from .unity_editor_bridge import UnityEditorBridge


class ObservationActions:
    def projects_list(self, payload: dict) -> ActionResult:
        return ActionResult(True, "Registered local projects", {
            "default_project": self.config.default_project,
            "projects": [project.public() for project in self.projects.values()],
        })

    def project_observe(self, payload: dict) -> ActionResult:
        project = self._project(payload)
        app = payload.get("app") or (project.apps[0] if project.apps else None)
        if app not in project.apps:
            raise ValueError(f"app {app} is not enabled for {project.slug}")
        if app == "blender":
            return self.blender_inspect(payload)
        if app == "unity":
            data = self._editor(payload).status()
            data["project"] = project.slug
            data["observed_at"] = datetime.now(timezone.utc).isoformat()
            return ActionResult(bool(data.get("presence_fresh")), "Unity Editor observation", data)
        return ActionResult(False, f"No observation adapter installed for {app}")

    def _capture_output(self, payload: dict, name: str = "capture.png") -> Path:
        project = self._project(payload)
        root = self.config.state_dir / "artifacts" / project.slug / uuid.uuid4().hex
        root.mkdir(parents=True, exist_ok=False)
        return root / name

    def _record_capture(self, payload: dict, output: Path) -> None:
        project = self._project(payload)
        root = self.config.state_dir / "artifacts" / project.slug
        temporary = root / f"latest-{uuid.uuid4().hex}.tmp"
        temporary.write_text(json.dumps({"artifact": str(output),
                                        "snapshot_path": str(output.with_suffix('.json'))}), encoding="utf-8")
        temporary.replace(root / 'latest.json')

    def observation_capture(self, payload: dict) -> ActionResult:
        """Bounded visual feedback loop, not a video streaming service."""
        project = self._project(payload)
        app = payload.get("app") or (project.apps[0] if project.apps else None)
        if app not in project.apps or app not in ("unity", "blender"):
            return ActionResult(False, f"No capture adapter enabled for {app}")
        count = max(1, min(12, int(payload.get("frames", 1))))
        interval = max(0.2, min(30.0, float(payload.get("interval_seconds", 1))))
        observations = []
        artifacts = []
        ok = True
        for index in range(count):
            started = time.monotonic()
            result = self.unity_capture(payload) if app == "unity" else self.blender_render_preview(payload)
            item = {"index": index, "observed_at": datetime.now(timezone.utc).isoformat(),
                    "duration_seconds": round(time.monotonic() - started, 3),
                    "ok": result.ok, "summary": result.summary, **result.data}
            observations.append(item)
            if self.on_observation is not None:
                self.on_observation(item)
            for key in ("artifact", "snapshot_path"):
                path = result.data.get(key)
                if path and Path(path).is_file():
                    artifacts.append({"path": path, "kind": "visual-frame" if key == "artifact" else "scene-snapshot"})
            if not result.ok:
                ok = False
                break
            if index + 1 < count:
                time.sleep(max(0.0, interval - (time.monotonic() - started)))
        return ActionResult(ok, f"Collected {len(observations)} observations", {
            "project": project.slug, "app": app, "observations": observations,
            "artifacts": artifacts, "transport": "bounded-capture-sequence",
        })

    def _blender_observation(self, payload: dict, render: bool) -> ActionResult:
        project = self._project(payload)
        blend = project.path(str(payload.get("blend_file") or project.blender.get("blend_file", "")))
        if not blend.is_file() or blend.suffix.lower() != ".blend":
            raise ValueError("blend_file must name a .blend file inside the registered project")
        blender = find_blender()
        if blender is None:
            return ActionResult(False, "Blender executable not found")
        output = self._capture_output(payload)
        request_path = output.with_name("request.json")
        request_path.write_text(json.dumps({
            "render": render, "output": str(output), "snapshot": str(output.with_suffix('.json')),
            "width": max(64, min(3840, int(payload.get("width", 1280)))),
            "height": max(64, min(2160, int(payload.get("height", 720)))),
            "samples": max(1, min(256, int(payload.get("samples", 32)))),
            "frame": payload.get("frame"),
        }), encoding="utf-8")
        script = Path(__file__).parent / "assets" / "blender_observe.py"
        command = [str(blender), "--background", "--factory-startup", "--disable-autoexec", str(blend),
                   "--python-exit-code", "1", "--python", str(script), "--", str(request_path)]
        process = run_process(command, cwd=project.root,
                              timeout_seconds=max(1, min(1800, int(payload.get("timeout_seconds", 300)))))
        snapshot = output.with_suffix('.json')
        data = {**process, "project": project.slug, "transport": "blender-cli",
                "observed_at": datetime.now(timezone.utc).isoformat()}
        if snapshot.is_file():
            data["snapshot"] = json.loads(snapshot.read_text(encoding="utf-8"))
            data["snapshot_path"] = str(snapshot)
        valid_image = output.is_file() and output.stat().st_size > 0
        if valid_image:
            data["artifact"] = str(output)
            data["sha256"] = hashlib.sha256(output.read_bytes()).hexdigest()
        ok = process["ok"] and snapshot.is_file() and (valid_image or not render)
        if ok and valid_image:
            self._record_capture(payload, output)
        return ActionResult(ok, "Blender observation completed" if ok else "Blender observation failed", data)

    def blender_inspect(self, payload: dict) -> ActionResult:
        return self._blender_observation(payload, False)

    def blender_render_preview(self, payload: dict) -> ActionResult:
        return self._blender_observation(payload, True)

    def unity_install_companion(self, payload: dict) -> ActionResult:
        project = self._project(payload)
        if not (project.root / "Assets").is_dir():
            raise ValueError("Unity project Assets directory is missing")
        if project.unity.get("profile") == "hordax":
            return ActionResult(False, "HORDAX already uses its dedicated companion")

        source = Path(__file__).parent / "assets" / "OrdaXGenericAgent.cs"
        target = project.path("Assets/OrdaX/Editor/OrdaXGenericAgent.cs", must_exist=False)
        content = source.read_bytes()
        before = target.read_bytes() if target.is_file() else None

        if before is not None and before != content:
            try:
                existing_text = before.decode("utf-8-sig")
            except UnicodeDecodeError:
                return ActionResult(
                    False,
                    "Existing Unity companion is not valid UTF-8; refusing managed upgrade",
                )
            managed_markers = (
                "class OrdaXGenericAgent",
                "ordax-generic-v",
                'namespace OrdaX',
            )
            if not all(marker in existing_text for marker in managed_markers):
                return ActionResult(
                    False,
                    "Existing companion is not recognized as OrdaX-managed; refusing overwrite",
                    {"path": str(target), "project": project.slug},
                )

        legacy = project.root / "Assets/HORDAX/Editor/OrdaXEditorAgent.cs"
        if legacy.exists():
            return ActionResult(False, "Existing HORDAX companion detected; select the hordax profile")

        target.parent.mkdir(parents=True, exist_ok=True)
        if before != content:
            temporary = target.with_name(target.name + f".ordax-{uuid.uuid4().hex}.tmp")
            try:
                temporary.write_bytes(content)
                if temporary.read_bytes() != content:
                    raise IOError("temporary companion verification failed")
                temporary.replace(target)
            finally:
                temporary.unlink(missing_ok=True)

        updated = before is not None and before != content
        installed = before is None
        return ActionResult(
            True,
            "Generic Unity companion upgraded; let Unity import the script"
            if updated
            else "Generic Unity companion installed; let Unity import the script",
            {
                "path": str(target),
                "project": project.slug,
                "installed": installed,
                "updated": updated,
                "already_current": before == content,
                "before_sha256": hashlib.sha256(before).hexdigest() if before is not None else None,
                "sha256": hashlib.sha256(content).hexdigest(),
            },
        )
