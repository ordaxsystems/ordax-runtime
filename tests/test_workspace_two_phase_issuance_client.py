"""Two-stage Product issuance HTTP adapter tests; NEVER issues a live grant."""
import unittest
import uuid
import json
import httpx
from ordax_dev_agent.product_remote_client import ProductRemoteClient, ProductRemoteError


def uid():
    return str(uuid.uuid4())


class WorkspaceIssueClientTests(unittest.TestCase):
    def setUp(self):
        self.ids = {k: uid() for k in (
            "intent_id", "client_id", "space_id", "project_id", "device_id", "actor_id",
        )}
        self.ids.update(lab_project_id="lab_one", workspace_session_id="session_one",
                        resource_id="resource_" + uuid.uuid4().hex)
        self.secret = "enrolled-local-device-token-" + uuid.uuid4().hex
        self.calls = []
        self.approved = True
        self.committed = True
        self.route_enabled = True
        self.http = httpx.Client(transport=httpx.MockTransport(self.handler))
        self.addCleanup(self.http.close)
        self.client = ProductRemoteClient("https://product.example.invalid", http=self.http)

    def handler(self, request):
        body=json.loads(request.content)
        self.calls.append((request,body))
        if not self.route_enabled:
            return httpx.Response(404,json={"ok":False,"error":"not_found"})
        if request.url.path=="/v3/owner/workspace-resources/approvals":
            self.assertEqual(request.method,"POST")
            self.assertEqual(request.headers["authorization"],"Bearer owner-jwt")
            self.assertNotIn("x-ordax-device-token",request.headers)
            self.assertEqual(body,{
                "intent_id":self.ids["intent_id"],
                "client_id":self.ids["client_id"],"lab_project_id":"lab_one",
                "space_id":self.ids["space_id"],"project_id":self.ids["project_id"],
                "device_id":self.ids["device_id"],"actor_id":self.ids["actor_id"],
                "workspace_session_id":"session_one",
                "resource_id":self.ids["resource_id"],
            })
            return httpx.Response(200 if self.approved else 403,
                                  json={"ok":self.approved,"approved":self.approved}
                                  if self.approved else {"ok":False,"error":"workspace_approval_denied"})
        self.assertEqual(request.url.path,"/v3/device/transport/workspace-confirm")
        self.assertEqual(request.method,"POST")
        self.assertEqual(request.headers["x-ordax-device-id"],self.ids["device_id"])
        self.assertEqual(request.headers["x-ordax-device-token"],self.secret)
        self.assertNotIn("authorization",request.headers)
        self.assertEqual(body,{
            "intent_id":self.ids["intent_id"],"client_id":self.ids["client_id"],
            "actor_id":self.ids["actor_id"],"workspace_session_id":"session_one",
            "resource_id":self.ids["resource_id"],
        })
        return httpx.Response(200 if self.committed else 403,
                              json={"ok":self.committed,"committed":self.committed}
                              if self.committed else {"ok":False,"error":"workspace_confirmation_denied"})

    def owner(self,**change):
        ids=dict(self.ids,**change)
        return self.client.approve_owner_workspace_resource("owner-jwt",**ids)

    def device(self,**change):
        ids=dict(self.ids,**change)
        for key in ("lab_project_id","space_id","project_id"):
            ids.pop(key)
        return self.client.confirm_device_workspace_resource(
            device_token=self.secret,**ids)

    def test_exact_two_stage_requests_use_existing_authority_and_no_path(self):
        self.assertTrue(self.owner())
        self.assertTrue(self.device())
        self.assertEqual(len(self.calls),2)
        for req,body in self.calls:
            serialized=json.dumps(body)
            self.assertNotIn(self.secret,serialized)
            self.assertNotIn("C:\\",serialized)
            self.assertNotIn("path",serialized)
            self.assertNotIn("owner_user_id",serialized)
        self.assertFalse(hasattr(self.client,"access_token"))

    def test_fail_closed_if_flag_disabled_or_postgres_denies(self):
        self.route_enabled=False
        with self.assertRaises(ProductRemoteError):
            self.owner()
        with self.assertRaises(ProductRemoteError):
            self.device()
        self.route_enabled=True
        self.approved=False
        with self.assertRaises(ProductRemoteError):
            self.owner()
        self.committed=False
        with self.assertRaises(ProductRemoteError):
            self.device()

    def test_invalid_identifiers_and_device_tokens_do_not_call_network(self):
        for field,value in (
            ("intent_id","forged"),("client_id","not-a-client"),
            ("space_id","wrong"),("project_id","wrong"),
            ("device_id","wrong"),("actor_id","wrong"),
            ("lab_project_id","../private"),("workspace_session_id","contains space"),
            ("resource_id","C:\\secret"),
        ):
            with self.subTest(field=field),self.assertRaises(ValueError):
                self.owner(**{field:value})
        self.assertEqual(self.calls,[])
        for field,value in (
            ("intent_id","forged"),("client_id","other"),("device_id","other"),
            ("actor_id","other"),("workspace_session_id","../session"),
            ("resource_id","../root"),
        ):
            with self.subTest(field=field),self.assertRaises(ValueError):
                self.device(**{field:value})
        with self.assertRaises(ValueError):
            self.client.confirm_device_workspace_resource(device_token="short",
                **{k:v for k,v in self.ids.items()
                   if k not in ("lab_project_id","space_id","project_id")})
        self.assertEqual(self.calls,[])

    def test_success_response_must_confirm_explicit_field(self):
        self.http.close()
        transport=httpx.MockTransport(lambda request:
            httpx.Response(200,json={"ok":True,"approved":False}))
        with httpx.Client(transport=transport) as http:
            client=ProductRemoteClient("https://product.example.invalid",http=http)
            with self.assertRaises(ProductRemoteError):
                client.approve_owner_workspace_resource("owner-jwt",**self.ids)


if __name__=="__main__":
    unittest.main()
