import unittest
from unittest.mock import Mock
from ordax_dev_agent.models import AgentJob
from ordax_dev_agent.orchestrator_app_launcher import VerifiedOrchestratorInstallation
from ordax_dev_agent.orchestrator_app_bridge import ExpectedOrchestratorIdentity
from ordax_dev_agent.orchestrator_product_execution import execute_orchestrator_app_status
from pathlib import Path

class ProductOrchestratorExecutionTests(unittest.TestCase):
    def setUp(self):
        self.job = AgentJob('fixture', 'orchestrator.app_read')
        self.control = Mock()
        self.authorize = self.control.orchestrator_read_authorizer.return_value
        self.provider = Mock(return_value=VerifiedOrchestratorInstallation(Path('/fixture'),
            tuple((n, 'a'*64) for n in ('OrdaX Orchestrator Lab.exe', 'resources/app.asar', 'resources/runtime-build.json')),
            ExpectedOrchestratorIdentity('b'*40, 'c'*64, 'd'*64)))
        self.session = Mock()
        self.session.read.return_value = {'engine': 'app-owned'}
        self.launcher = Mock(return_value=self.session)

    def execute(self, **kwargs):
        return execute_orchestrator_app_status(self.control, self.job,
            installation_provider=self.provider, launcher=self.launcher, **kwargs)

    def test_only_status_reaches_app_and_private_channel_closes(self):
        result = self.execute()
        self.assertTrue(result.ok)
        self.assertEqual(result.data['status'], {'engine': 'app-owned'})
        self.session.read.assert_called_once_with('status', {})
        self.session.close.assert_called_once_with()
        self.assertEqual(self.authorize.call_count, 2)
        self.assertNotIn('/fixture', str(result))

    def test_absent_installation_returns_explicit_failure_without_launch(self):
        result = execute_orchestrator_app_status(self.control, self.job, launcher=self.launcher)
        self.assertFalse(result.ok)
        self.assertEqual(result.data['error_code'], 'orchestrator_installation_unverified')
        self.launcher.assert_not_called()

    def test_model_cannot_supply_path_identity_project_or_operation(self):
        for payload in ({'path':'secret'}, {'operation':'tasks.create'}, {'grant_id':'forged'}):
            self.job.payload = payload
            self.assertFalse(self.execute().ok)
        self.job.payload = {}
        self.job.project_slug = 'project'
        self.assertFalse(self.execute().ok)
        self.provider.assert_not_called()
        self.control.orchestrator_read_authorizer.assert_not_called()

    def test_denial_prevents_installation_and_late_revocation_discards_reply(self):
        self.authorize.side_effect = PermissionError('private credential')
        result = self.execute()
        self.assertFalse(result.ok)
        self.provider.assert_not_called()
        self.assertNotIn('credential', str(result))
        self.authorize.side_effect = [None, PermissionError('revoked')]
        self.assertFalse(self.execute().ok)
        self.session.close.assert_called_once_with()

    def test_read_failure_closes_channel_and_never_leaks_details(self):
        self.session.read.side_effect = RuntimeError('private path and key')
        result = self.execute()
        self.assertFalse(result.ok)
        self.session.close.assert_called_once_with()
        self.assertNotIn('private path', str(result))
