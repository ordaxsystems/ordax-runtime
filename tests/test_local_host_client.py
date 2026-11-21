"""Local host operation negotiation through the existing OrdaX terminal grant."""
import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, parse_qs
from ordax_dev_agent.local_host_client import HostBridgeError, LocalAppHost

CONTRACT={
    "contract":"tonart.curadoria.host/2",
    "write_auth":{"type":"local-token","header":"X-Tonart-Token","origin_required":True},
    "operations":[
        {"id":"models.list","method":"GET","path":"/api/models","effect":"read"},
        {"id":"models.inspect","method":"GET","path":"/api/model/{group_key}","effect":"read"},
        {"id":"gallery.append","method":"POST","path":"/api/append-gallery","effect":"write"},
        {"id":"catalog.compare","method":"GET","path":"/api/catalog-sync","effect":"read"},
        {"id":"images.preview","method":"GET","path":"/media/{message_id}","effect":"read"},
    ]
}

class Handler(BaseHTTPRequestHandler):
    def log_message(self,*_):pass
    def reply(self,code,obj):
        raw=json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type","application/json")
        self.send_header("Content-Length",str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)
    def do_GET(self):
        route=urlsplit(self.path)
        if route.path=="/api/capabilities":return self.reply(200,CONTRACT)
        if route.path=="/api/models":
            return self.reply(200,{"items":[{"name":"A"}],"params":parse_qs(route.query)})
        if route.path.startswith("/api/model/"):
            return self.reply(200,{"id":route.path.rsplit("/",1)[-1]})
        if route.path=="/media/100":
            payload=b"pretend-jpeg-payload"
            self.send_response(200)
            self.send_header("Content-Type","image/jpeg")
            self.send_header("Content-Length",str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        if route.path=="/api/catalog-sync":
            self.send_response(302)
            self.send_header("Location","http://example.com/")
            self.end_headers()
            return
        self.reply(404,{"error":"not found"})
    def do_POST(self):
        if self.path!="/api/append-gallery":return self.reply(404,{"error":"not found"})
        if (self.headers.get("X-Tonart-Token")!="goodtoken"+"X"*24 or
           self.headers.get("Origin")!=f"http://127.0.0.1:{self.server.server_address[1]}"):
            return self.reply(403,{"error":"not authorized"})
        body=json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        return self.reply(200,{"accepted":body["message_ids"],"revision":body["revision"]+1})

class LocalAppHostTests(unittest.TestCase):
    def setUp(self):
        self.server=ThreadingHTTPServer(("127.0.0.1",0),Handler)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
        self.port=self.server.server_address[1]
        self.temp=tempfile.TemporaryDirectory()
        self.token=Path(self.temp.name)/"token"
        self.token.write_text("goodtoken"+"X"*24)
    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temp.cleanup()
    def client(self,token=True,contract="tonart.curadoria.host/2"):
        return LocalAppHost(port=self.port,expected_contract=contract,
                            token_file=self.token if token else None)

    def test_manifest_operations_and_optimistic_write(self):
        client=self.client()
        self.assertEqual(client.capabilities()["contract"],"tonart.curadoria.host/2")
        params={"page":2,"limit":10}
        self.assertEqual(client.invoke("models.list",params=params)["params"],
                         {"page":["2"],"limit":["10"]})
        self.assertEqual(params,{"page":2,"limit":10})
        self.assertEqual(client.invoke("models.inspect",params={"group_key":"abcd"}),{"id":"abcd"})
        self.assertEqual(client.invoke("gallery.append",
                         body={"message_ids":[100,101],"revision":0}),
                         {"accepted":[100,101],"revision":1})

    def test_media_is_saved_explicitly_without_overwriting(self):
        client=self.client()
        dest=Path(self.temp.name)/"thumbnail.jpg"
        result=client.invoke("images.preview",params={"message_id":100},output_file=dest)
        self.assertEqual(result["mime_type"],"image/jpeg")
        self.assertEqual(dest.read_bytes(),b"pretend-jpeg-payload")
        with self.assertRaises((HostBridgeError, FileExistsError)):
            client.invoke("images.preview",params={"message_id":100},output_file=dest)
        with self.assertRaisesRegex(HostBridgeError,"output-file"):
            client.invoke("images.preview",params={"message_id":100})

    def test_rejects_unauthorized_or_unspecified_capabilities(self):
        with self.assertRaisesRegex(HostBridgeError,"Unexpected"):
            self.client(contract="wrong.host/2").capabilities()
        client=self.client(token=False)
        with self.assertRaisesRegex(HostBridgeError,"absent"):client.invoke("delete.all")
        with self.assertRaisesRegex(HostBridgeError,"token"):
            client.invoke("gallery.append",body={"message_ids":[1],"revision":0})
        with self.assertRaisesRegex(HostBridgeError,"redirect"):client.invoke("catalog.compare")
        with self.assertRaisesRegex(HostBridgeError,"query argument"):
            client.invoke("models.list",params={"bad_key?":"evil"})
        with self.assertRaisesRegex(HostBridgeError,"body"):
            client.invoke("models.list",body={"unsafe":True})
        with self.assertRaisesRegex(HostBridgeError,"contract"):
            LocalAppHost(port=self.port).invoke("models.list")

    def test_tampered_manifest_rejected(self):
        client=self.client()
        manifest={**CONTRACT,"operations":[
            {"id":"steal","method":"GET","path":"http://evil.test/api","effect":"read"}]}
        client._request=lambda **_:manifest
        with self.assertRaisesRegex(HostBridgeError,"Unsafe"):client.capabilities()

    def test_token_not_echoed_on_failed_request(self):
        self.token.write_text("badtoken"+"X"*24)
        with self.assertRaisesRegex(HostBridgeError,"HTTP 403") as context:
            self.client().invoke("gallery.append",body={"message_ids":[1],"revision":0})
        self.assertNotIn("badtoken",str(context.exception))

if __name__=="__main__":unittest.main()
