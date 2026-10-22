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
from dataclasses import replace
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
                original_pid, original_session = session.pid, session._client._session_id
                old_key = bytes(session._client._key)
                authorization = replace(authorization, expires_at_ms=int(time.time()*1000)+600000)
                renewed_until = session.renew()
                if (renewed_until <= int(time.time()*1000) or session.pid != original_pid
                        or session._client._session_id != original_session
                        or bytes(session._client._key) == old_key
                        or session.read('projects_list')['projects'] != []):
                    raise RuntimeError('managed_fixture_renewal_mismatch')
                session.close()
                if session._child.poll() is not None:
                    raise RuntimeError('managed_fixture_closed_human_app')
                # A second disposable profile exercises the Product task executor,
                # real live-authority parser and real verified launcher together.
                from ordax_dev_agent.models import AgentJob
                from ordax_dev_agent.product_device_control_plane import ProductDeviceControlPlane
                from ordax_dev_agent.orchestrator_product_execution import execute_orchestrator_app_status
                with tempfile.TemporaryDirectory(prefix='ordax-product-app-ci-', ignore_cleanup_errors=True) as product_profile:
                    fingerprint = hashlib.sha256(str(Path(product_profile) / 'OrdaX/OrchestratorLab').lower().encode()).hexdigest()
                    product_installation = replace(installation, expected=replace(expected, profile_fingerprint=fingerprint))
                    plane = ProductDeviceControlPlane.__new__(ProductDeviceControlPlane)
                    plane.device_id = str(uuid.uuid4())
                    plane.runtime_kind, plane.agent_version = 'desktop-agent', 'ci-fixture'
                    job = AgentJob(id=str(uuid.uuid4()), action='orchestrator.app_read',
                        effect_id=str(uuid.uuid4()), attempt_id=str(uuid.uuid4()), lease_token=str(uuid.uuid4()),
                        execution_epoch=1, agent_instance_id=str(uuid.uuid4()), boot_id='ci-fixture-boot')
                    plane._jobs = {job.id: job}
                    calls = []
                    subject_id, grant_id = str(uuid.uuid4()), str(uuid.uuid4())
                    def product_post(operation, body):
                        calls.append(operation)
                        return {'ok': True, 'authorization': {
                            'schema': 'ordax.orchestrator-read-authorization/1',
                            'subject_id': subject_id, 'client_id': 'oauth-managed-ci-fixture',
                            'device_id': plane.device_id, 'grant_id': grant_id,
                            'app_id': 'br.com.ordax.orchestrator.lab', 'capability': 'orchestrator.app_read',
                            'expires_at_ms': int(time.time()*1000)+120000}}
                    plane._post = product_post
                    with patch.dict(os.environ, {'LOCALAPPDATA': product_profile}), patch(
                            'ordax_dev_agent.orchestrator_app_launcher.subprocess.Popen', side_effect=capture):
                        result = execute_orchestrator_app_status(plane, job,
                            installation_provider=lambda: product_installation)
                    if not result.ok or result.data['status']['engine'] != 'app-owned' or len(calls) < 4:
                        raise RuntimeError('managed_fixture_product_execution_mismatch')
                print(json.dumps({'schema':'ordax.orchestrator-managed-app-e2e/1', 'ok':True,
                    'productTaskExecutor':True, 'freshProductAuthorization':True,
                    'pythonRuntimeClient':True, 'packagedElectron':True, 'allBundleFilesPinned':True,
                    'kernelPeerPidVerified':True, 'freshProfileVerified':True,
                    'humanAppPreservedOnClose':True, 'authorizationFixture':True,
                    'authenticatedLeaseRenewal':True, 'sameProcessAndSession':True,
                    'ephemeralKeyRotated':True,
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
