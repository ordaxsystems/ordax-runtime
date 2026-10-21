"""Product task -> verified launcher -> App-owned status, never visual discovery.

Only the installation owner can supply the provider from host composition.
The public action accepts no receipt, file path, process ID, command or identity.
Absence of installation provenance is an explicit failure, not a live registry.
"""
from .models import ActionResult
from .orchestrator_app_launcher import launch_verified_orchestrator, validate_orchestrator_installation

CAPABILITY = 'orchestrator.app_read'


def execute_orchestrator_app_status(control, job, *, installation_provider=None,
                                    launcher=launch_verified_orchestrator):
    session = None
    result = ActionResult(False, 'Orchestrator Lab status could not be verified',
                          {'error_code': 'orchestrator_status_unavailable'})
    try:
        if job.action != CAPABILITY or job.project_slug is not None or job.action_payload() != {}:
            return ActionResult(False, 'Invalid Orchestrator status request',
                                {'error_code': 'orchestrator_status_input_invalid'})
        # Resolve live Product authority BEFORE accessing installation material.
        authorize = control.orchestrator_read_authorizer(job)
        authorize()
        if not callable(installation_provider):
            return ActionResult(False, 'Orchestrator Lab installation has not been verified',
                {'error_code': 'orchestrator_installation_unverified',
                 'app_id': 'br.com.ordax.orchestrator.lab', 'connected': False})
        installation = validate_orchestrator_installation(installation_provider())
        session = launcher(installation=installation, authorize=authorize)
        status = session.read('status', {})
        # The bridge already reauthorizes after the App reply; recheck before
        # returning the Product result as well. Never disclose paths or PID.
        authorize()
        result = ActionResult(True, 'Orchestrator Lab status read from its private service',
            {'app_id': 'br.com.ordax.orchestrator.lab', 'source': 'app-owned-service',
             'read_completed': True, 'status': status})
    except Exception:
        pass
    finally:
        if session is not None:
            try:
                session.close()
            except Exception:
                result = ActionResult(False, 'Orchestrator Lab channel could not be closed',
                                      {'error_code': 'orchestrator_status_unavailable'})
    return result
