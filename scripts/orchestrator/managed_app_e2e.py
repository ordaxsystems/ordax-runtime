"""Disposable Windows CI fixture, not an installation receipt or live grant."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import uuid
from unittest.mock import patch

from ordax_dev_agent.orchestrator_app_bridge import ExpectedOrchestratorIdentity, VerifiedOrchestratorReadAuthorization
from ordax_dev_agent.orchestrator_app_launcher import VerifiedOrchestratorInstallation, launch_verified_orchestrator


def run():
    if os.name != 'nt' or os.environ.get('GITHUB_ACTIONS') != 'true':
        raise RuntimeError('managed_fixture_requires_windows_ci')
    app_root = Path(sys.argv[1]).resolve(strict=True)
    proof = json.loads((app_root / 'dist/orchestrator-packaged-proof.json').read_text())
    if proof['sourceCommit'] != os.environ['GITHUB_SHA'] or proof['status'] != 'verified':
        raise RuntimeError('managed_fixture_build_mismatch')
    bundle = app_root / 'dist/win-unpacked'
    def digest(file):
        with file.open('rb') as stream:
            return hashlib.file_digest(stream, 'sha256').hexdigest()
    hashes = tuple((f.relative_to(bundle).as_posix(), digest(f))
                   for f in sorted(bundle.rglob('*')) if f.is_file())
    with tempfile.TemporaryDirectory(prefix='ordax-managed-app-ci-') as fresh:
        profile = str(Path(fresh) / 'OrdaX/OrchestratorLab').lower()
        expected = ExpectedOrchestratorIdentity(proof['sourceCommit'], proof['contentDigest'], hashlib.sha256(profile.encode()).hexdigest())
        installation = VerifiedOrchestratorInstallation(bundle, hashes, expected)
        authorization = VerifiedOrchestratorReadAuthorization(str(uuid.uuid4()), 'oauth-managed-ci-fixture', str(uuid.uuid4()), str(uuid.uuid4()), int(time.time()*1000)+120000)
        session, owned = None, []
        real_spawn = subprocess.Popen
        def capture(*args, **kwargs):
            child = real_spawn(*args, **kwargs)
            owned.append(child)
            return child
        with patch.dict(os.environ, {'LOCALAPPDATA': fresh}):
            try:
                with patch('ordax_dev_agent.orchestrator_app_launcher.subprocess.Popen', side_effect=capture):
                    session = launch_verified_orchestrator(installation=installation, authorize=lambda: authorization)
                status = session.read('status')
                projects = session.read('projects_list')
                if status['autonomousExecution'] is not False or status['coordinatorAvailable'] is not False or projects['projects'] != []:
                    raise RuntimeError('managed_fixture_state_mismatch')
                session.close()
                if session._child.poll() is not None:
                    raise RuntimeError('managed_fixture_closed_human_app')
                print(json.dumps({'schema':'ordax.orchestrator-managed-app-e2e/1', 'ok':True,
                    'pythonRuntimeClient':True, 'packagedElectron':True, 'allBundleFilesPinned':True,
                    'kernelPeerPidVerified':True, 'freshProfileVerified':True,
                    'humanAppPreservedOnClose':True, 'authorizationFixture':True,
                    'liveProductGrant':False, 'providerMessagesSent':0}))
            finally:
                if session is not None:
                    # Only the exact child created in this disposable CI test.
                    session.close()
                for child in owned:
                    if child.poll() is None:
                        subprocess.run(['taskkill','/PID',str(child.pid),'/T','/F'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False, timeout=15)
                    child.wait(timeout=15)



if __name__ == '__main__':
    try:
        run()
    except Exception as error:
        # Only local protocol reason codes; never arbitrary exception content.
        allowed = {'host_managed_launch_unavailable','host_profile_binding_required','host_verified_installation_required'}
        print(json.dumps({'schema':'ordax.orchestrator-managed-app-e2e/1','ok':False,
            'reason':str(error) if str(error) in allowed else 'managed_fixture_failed'}), file=sys.stderr)
        raise SystemExit(2)
