"""Isolated fake-provider checks for ORDAX Studio native ChatGPT Web DEV adapter."""
import os
import sys
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from ordax_studio.dev_chatgpt_bridge import _endpoint, _model_catalog, StudioDevChatApi, DevBridgeError

class FakeOrchestrator:
    def __init__(self):
        self.log=[]
        self.conversation={
            "id":"fake-id", "project_slug":"dev-only", "account_id":"chatgpt-web-dev",
            "provider_id":"chatgpt-web", "model_id":"chatgpt-web/fake-verified",
            "archived_at":None,
        }
    def assistant_conversation(self,id):
        assert id == "fake-id"
        return self.conversation
    def assistant_messages(self,id,limit=200):
        assert id=="fake-id"
        return self.log.copy()
    def add_assistant_message(self,id,*,role,content):
        assert id=="fake-id"
        self.log.append({"role":role,"content":content})
        return self.log[-1]

class Handler(BaseHTTPRequestHandler):
    last_request = None
    def log_message(self,*_args):
        pass
    def do_GET(self):
        if self.path!="/v1/models":
            self.send_error(404);return
        body={"object":"list","data":[
            {"id":"chatgpt-web/fake-verified","name":"ChatGPT Web test"},
            {"id":"openai/official","name":"Not a web-session model"}
        ]}
        encoded=json.dumps(body).encode()
        self.send_response(200);self.send_header("Content-Type","application/json");self.send_header("Content-Length",str(len(encoded)));self.end_headers();self.wfile.write(encoded)
    def do_POST(self):
        if self.path!="/v1/responses":
            self.send_error(404);return
        body=json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        Handler.last_request=body
        result={"id":"mock-result","output":[{"type":"message","role":"assistant",
                "content":[{"type":"output_text","text":"Recebido no chat nativo."}]}]}
        encoded=json.dumps(result).encode()
        self.send_response(200);self.send_header("Content-Type","application/json");self.send_header("Content-Length",str(len(encoded)));self.end_headers();self.wfile.write(encoded)

class NativeChatBridgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server=ThreadingHTTPServer(("127.0.0.1",0),Handler)
        cls.worker=threading.Thread(target=cls.server.serve_forever,daemon=True)
        cls.worker.start()
    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.worker.join(timeout=3)
    def setUp(self):
        self.url=f"http://127.0.0.1:{self.server.server_address[1]}/v1"
        self.environment=patch.dict(os.environ,{"ORDAX_STUDIO_DEV_CHAT_BRIDGE_URL":self.url},clear=False)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.api=object.__new__(StudioDevChatApi)
        self.api.project="dev-only"
        self.api.orchestrator=FakeOrchestrator()
        Handler.last_request=None

    def test_model_detection(self):
        self.assertEqual([{"id":"chatgpt-web/fake-verified","label":"ChatGPT Web test"}],_model_catalog())

    def test_native_message_and_persistence_through_real_http(self):
        result=self.api.assistant_send_message("fake-id","Oi! Responda.")
        self.assertTrue(result.get("ok"),result)
        self.assertEqual(["user","assistant"],[x["role"] for x in self.api.orchestrator.log])
        self.assertEqual("Recebido no chat nativo.",self.api.orchestrator.log[1]["content"])
        self.assertEqual("chatgpt-web/fake-verified",Handler.last_request["model"])
        self.assertEqual([],Handler.last_request["tools"])

    def test_history_uses_existing_orchestrator_storage(self):
        self.api.assistant_send_message("fake-id","Primeira")
        self.api.assistant_send_message("fake-id","Segunda")
        self.assertEqual(4,len(self.api.orchestrator.log))
        self.assertEqual(3,len(Handler.last_request["input"]))

    def test_no_cross_project_message(self):
        self.api.project="other"
        result=self.api.assistant_send_message("fake-id","Nao envie")
        self.assertFalse(result["ok"])
        self.assertIsNone(Handler.last_request)
        self.assertEqual([],self.api.orchestrator.log)

    def test_no_unapproved_model(self):
        self.api.orchestrator.conversation["model_id"]="openai/official"
        result=self.api.assistant_send_message("fake-id","Nao envie")
        self.assertFalse(result["ok"])
        self.assertEqual([],self.api.orchestrator.log)

    def test_rejects_remote_url_or_userinfo(self):
        for url in ("https://127.0.0.1:7777/v1", "http://localhost:7777/v1",
                    "http://evil.test:7777/v1", "http://admin:pass@127.0.0.1:7777/v1",
                    "http://127.0.0.1:7777/v1?secret=1"):
            with self.subTest(url=url),patch.dict(os.environ,{"ORDAX_STUDIO_DEV_CHAT_BRIDGE_URL":url}):
                with self.assertRaises(DevBridgeError):
                    _endpoint()

if __name__=="__main__":
    unittest.main()
