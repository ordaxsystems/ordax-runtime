"""Opt-in integration smoke: real Blender rendering in a temporary isolated project.

Run with the repository Python: python scripts/verify_visual_agent.py
Does not open or modify the user's scenes or running agent.
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mcp_blender_unity.config import find_blender
from mcp_blender_unity.process import run_process
from ordax_dev_agent.actions import ActionRegistry
from ordax_dev_agent.config import AgentConfig


def main():
    blender = find_blender()
    if not blender:
        raise SystemExit("Blender not installed")
    with tempfile.TemporaryDirectory(prefix="ordax-visual-test-") as directory:
        root = Path(directory)
        scene = root / "scene.blend"
        # Blender factory startup includes a cube, light and camera.
        create = run_process([str(blender), "--background", "--factory-startup", "--python-exit-code", "1",
                              "--python-expr", f"import bpy; bpy.ops.wm.save_as_mainfile(filepath={str(scene)!r})"],
                             timeout_seconds=120)
        if not create['ok']:
            raise RuntimeError(create)
        config = AgentConfig('smoke',   5, root / 'state', root, root, root,
                             projects={'cube': {'path': str(root), 'apps': ['blender'],
                                                'blender': {'blend_file': 'scene.blend'}}}, default_project='cube')
        agent = ActionRegistry(config)
        before = scene.read_bytes()
        result = agent.execute('observation.capture', {'app': 'blender', 'frames': 2,
                                                     'width': 320, 'height': 180, 'samples': 4})
        if not result.ok:
            raise RuntimeError(json.dumps(result.data, indent=2))
        observations = result.data['observations']
        assert len(observations) == 2
        assert observations[0]['artifact'] != observations[1]['artifact']
        for observation in observations:
            assert Path(observation['artifact']).read_bytes().startswith(b'\x89PNG\r\n\x1a\n')
            assert observation['snapshot']['object_count'] == 3

        companion = (
            Path(__file__).resolve().parents[1]
            / "ordax_dev_agent"
            / "assets"
            / "blender_live_companion.py"
        )
        live_root = root / "live-smoke"
        control_root = live_root / "control"
        artifacts_root = live_root / "artifacts"
        multiview_output = artifacts_root / "multiview"
        scripts_root = root / "automation" / "blender"
        scripts_root.mkdir(parents=True, exist_ok=True)

        multiview = run_process(
            [
                str(blender),
                "--background",
                str(scene),
                "--python-exit-code",
                "1",
                "--python",
                str(companion),
                "--",
                "--ordax-control-root",
                str(control_root),
                "--ordax-project-root",
                str(root),
                "--ordax-scripts-root",
                str(scripts_root),
                "--ordax-artifacts-root",
                str(artifacts_root),
                "--ordax-project-slug",
                "cube",
                "--ordax-smoke-output-dir",
                str(multiview_output),
                "--ordax-smoke-mode",
                "silhouette",
                "--ordax-smoke-width",
                "320",
                "--ordax-smoke-height",
                "320",
            ],
            timeout_seconds=180,
        )
        if not multiview["ok"]:
            raise RuntimeError(json.dumps(multiview, indent=2))

        result_path = control_root / "results" / "smoke.json"
        if not result_path.is_file():
            raise RuntimeError("real multiview smoke did not create a result")
        multiview_result = json.loads(result_path.read_text(encoding="utf-8-sig"))
        if not multiview_result.get("ok"):
            raise RuntimeError(json.dumps(multiview_result, indent=2))
        assert multiview_result["mode"] == "silhouette"
        assert len(multiview_result["artifacts"]) == 4
        assert Path(multiview_result["manifest"]).is_file()
        for artifact in multiview_result["artifacts"]:
            assert Path(artifact["artifact"]).read_bytes().startswith(
                b'\x89PNG\r\n\x1a\n'
            )

        assert scene.read_bytes() == before, 'Visual smoke modified the original .blend file'
        print(json.dumps({
            'ok': True,
            'frames': len(observations),
            'objects': 3,
            'source_unchanged': True,
            'durations': [o['duration_seconds'] for o in observations],
            'multiview_mode': multiview_result['mode'],
            'multiview_views': [item['view'] for item in multiview_result['artifacts']],
            'multiview_engine': multiview_result['render_engine'],
        }, indent=2))


if __name__ == '__main__':
    main()
