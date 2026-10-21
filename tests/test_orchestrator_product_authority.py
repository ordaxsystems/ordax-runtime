from dataclasses import replace
import time
import unittest
import uuid
from unittest.mock import patch

from ordax_dev_agent.models import AgentJob
from ordax_dev_agent.product_device_control_plane import ProductDeviceControlPlane
from ordax_dev_agent.orchestrator_app_bridge import (
    OrchestratorBridgeUnavailable, require_orchestrator_read_authorization,
)


class ProductOrchestratorAuthorityTests(unittest.TestCase):
    def setUp(self):
        self.now = int(time.time() * 1000)
        self.plane = ProductDeviceControlPlane.__new__(ProductDeviceControlPlane)
        self.plane.device_id = str(uuid.uuid4())
        self.plane.runtime_kind, self.plane.agent_version = 'desktop-agent', 'fixture-version'
        self.job = AgentJob(id=str(uuid.uuid4()), action='orchestrator.app_read',
            effect_id=str(uuid.uuid4()), attempt_id=str(uuid.uuid4()), lease_token=str(uuid.uuid4()),
            execution_epoch=1, agent_instance_id=str(uuid.uuid4()), boot_id='fixture-boot',
            payload={'owner_user_id': 'MODEL-FORGED', 'grant_id': 'MODEL-FORGED'})
        self.plane._jobs = {self.job.id: self.job}
        self.value = dict(schema='ordax.orchestrator-read-authorization/1',
            subject_id=str(uuid.uuid4()), client_id=str(uuid.uuid4()), device_id=self.plane.device_id,
            grant_id=str(uuid.uuid4()), app_id='br.com.ordax.orchestrator.lab',
            capability='orchestrator.app_read', expires_at_ms=self.now + 60000)
        self.calls = []
        self.plane._post = self.post

    def post(self, operation, body):
        self.calls.append((operation, body))
        return {'ok': True, 'authorization': dict(self.value)}

    def test_each_resolution_uses_only_the_registered_job_fence_and_device_transport(self):
        resolve = self.plane.orchestrator_read_authorizer(self.job)
        for _ in range(2):
            value = resolve()
            self.assertEqual(value.subject_id, self.value['subject_id'])
        self.assertEqual(len(self.calls), 2)
        operation, body = self.calls[0]
        self.assertEqual(operation, 'orchestrator-read-authorization')
        self.assertEqual(set(body), {'job_id', 'effect_id', 'attempt_id', 'lease_id',
                                    'execution_epoch', 'agent_instance_id', 'boot_id'})
        self.assertNotIn('MODEL-FORGED', str(body))
        self.assertNotIn('runtime_kind', body)

    def test_unclaimed_duplicate_generic_or_project_jobs_never_reach_http(self):
        for job in (replace(self.job), replace(self.job, action='computer.screenshot'),
                    replace(self.job, project_slug='project')):
            with self.assertRaises(OrchestratorBridgeUnavailable):
                self.plane.orchestrator_read_authorizer(job)
        self.assertEqual(self.calls, [])

    def test_completed_or_replaced_job_and_mutated_fence_invalidate_live_callback(self):
        for mutation in ('removed', 'replaced', 'epoch', 'lease', 'device'):
            with self.subTest(mutation=mutation):
                self.setUp()
                resolve = self.plane.orchestrator_read_authorizer(self.job)
                if mutation == 'removed': self.plane._jobs.clear()
                if mutation == 'replaced': self.plane._jobs[self.job.id] = replace(self.job)
                if mutation == 'epoch': self.job.execution_epoch += 1
                if mutation == 'lease': self.job.lease_token = str(uuid.uuid4())
                if mutation == 'device': self.plane.device_id = str(uuid.uuid4())
                with self.assertRaises(OrchestratorBridgeUnavailable): resolve()
                self.assertEqual(self.calls, [])

    def test_late_response_cannot_release_authority_after_job_completion(self):
        def post(operation, body):
            reply = self.post(operation, body)
            self.plane._jobs.clear()
            return reply
        self.plane._post = post
        resolve = self.plane.orchestrator_read_authorizer(self.job)
        with self.assertRaises(OrchestratorBridgeUnavailable): resolve()
        self.assertEqual(len(self.calls), 1)

    def test_malformed_foreign_stale_or_excessive_authority_is_rejected(self):
        original = dict(self.value)
        variants = [dict(device_id=str(uuid.uuid4())), dict(app_id='other-app'),
            dict(capability='computer.screenshot'), dict(subject_id='invalid'),
            dict(expires_at_ms=self.now), dict(expires_at_ms=self.now+300001),
            dict(expires_at_ms=True), dict(extra='private'), dict(schema='unknown')]
        with patch('time.time', return_value=self.now / 1000):
            resolve = self.plane.orchestrator_read_authorizer(self.job)
            for variant in variants:
                self.value = {**original, **variant}
                with self.subTest(variant=variant), self.assertRaises(OrchestratorBridgeUnavailable):
                    resolve()

    def test_denial_or_transport_failure_never_reuses_previous_authority(self):
        resolve = self.plane.orchestrator_read_authorizer(self.job)
        resolve()
        self.plane._post = lambda *args: {'ok': False}
        with self.assertRaises(OrchestratorBridgeUnavailable): resolve()
        def unavailable(*args): raise TimeoutError('sensitive transport detail')
        self.plane._post = unavailable
        with self.assertRaisesRegex(OrchestratorBridgeUnavailable, '^host_authorization_required$'):
            require_orchestrator_read_authorization(resolve, lambda: self.now)


if __name__ == '__main__':
    unittest.main()
