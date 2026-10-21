"""Synthetic cross-language lease test. No Electron, install, OAuth or provider.

Run with the companion App bridge source path. Virtual clock and fixture
identity exist only in this child harness, never in the Product transport.
"""
import json
from pathlib import Path
import subprocess
import sys
import uuid

from ordax_dev_agent.orchestrator_app_bridge import (
    ExpectedOrchestratorIdentity, InheritedOrchestratorPipe,
    OrchestratorAppReadClient, OrchestratorBridgeUnavailable,
    VerifiedOrchestratorReadAuthorization,
)


def run(app_bridge):
    source = """
import readline from 'node:readline';
import {createOrchestratorRuntimeBridge} from MODULE;
let now=100000;
const bridge=createOrchestratorRuntimeBridge({
 workspaceSessionId:'synthetic-lease-test',clock:()=>now,
 buildProof:{verified:true,status:'verified',sourceCommit:'a'.repeat(40),contentDigest:'b'.repeat(64)},
 profileFingerprint:'c'.repeat(64),
 service:{async invoke(context,operation){return {operation};}}
});
for await (const line of readline.createInterface({input:process.stdin})) {
 const frame=JSON.parse(line);now=frame.now;
 console.log(JSON.stringify(await bridge.handle(frame.message)));
}
""".replace('MODULE', json.dumps(Path(app_bridge).resolve(strict=True).as_uri()))
    child = subprocess.Popen(['node', '--input-type=module', '-e', source],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    now, revoked, calls = 100000, False, []
    identity = [str(uuid.uuid4()) for _ in range(3)]
    transport = InheritedOrchestratorPipe(child, request_pipe=child.stdin,
                                        response_pipe=child.stdout, timeout_seconds=5)
    def authorize():
        if revoked:
            return None
        return VerifiedOrchestratorReadAuthorization(identity[0], 'oauth-synthetic-fixture',
                                                     identity[1], identity[2], now + 600000)
    def exchange(message):
        calls.append(message)
        return transport({'now': now, 'message': message})
    client = None
    try:
        client = OrchestratorAppReadClient(exchange=exchange, authorize=authorize,
            expected=ExpectedOrchestratorIdentity('a'*40, 'b'*64, 'c'*64), clock=lambda: now)
        original_session = client._session_id
        for _ in range(360):
            now += 240000
            client.renew()
            assert client.read('status') == {'operation': 'status'}
        old_request = calls[-1]
        now += 1000
        client.renew()
        assert transport({'now': now, 'message': old_request})['error'] == 'host_signature_rejected'
        assert client._session_id == original_session
        revoked, before = True, len(calls)
        try:
            client.renew()
            raise AssertionError('revoked authority renewed')
        except OrchestratorBridgeUnavailable:
            pass
        assert len(calls) == before and bytes(client._key) == bytes(32)
        assert child.poll() is None
        print(json.dumps({'schema': 'ordax.orchestrator-lease-fixture/1', 'ok': True,
            'simulatedHours': 24, 'renewals': 361, 'pythonToJavaScript': True,
            'oldKeyReplayRejected': True, 'revocationRejectedBeforeExchange': True,
            'liveProductGrant': False, 'providerMessagesSent': 0}))
    finally:
        if client is not None:
            client.close()
        transport.close()
        child.terminate()
        child.wait(timeout=10)
        child.stdin.close()
        child.stdout.close()


if __name__ == '__main__':
    run(sys.argv[1])
