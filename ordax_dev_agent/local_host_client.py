"""Generic, owner-invoked bridge from OrdaX MCP to a local app host.

Uses the existing, granted computer.terminal_exec action. The host remains
owner of its operation catalog and data. This module has NO TONART-specific
routes, database access, or new Product/OAuth action.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

MAX_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_BODY_BYTES = 2 * 1024 * 1024
MAX_MEDIA_BYTES = 25 * 1024 * 1024
OP_ID = re.compile(r"^[a-z][a-z0-9_.-]{1,79}$")
ARG_NAME = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
SAFE_TEMPLATE = re.compile(r"\{([a-z][a-z0-9_]*)\}")
SAFE_PATH = re.compile(r"^/(?:api|media)(?:/[a-zA-Z0-9._{}-]+)*$")


class HostBridgeError(ValueError):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise HostBridgeError("Local host attempted an HTTP redirect")


class LocalAppHost:
    def __init__(self, *, port: int, expected_contract: str | None = None,
                 token_file: str | Path | None = None, timeout: float = 15.0):
        if type(port) is not int or not 1 <= port <= 65535:
            raise HostBridgeError("Invalid local host port")
        if expected_contract is not None and (
                not isinstance(expected_contract, str)
                or not re.fullmatch(r"[a-z0-9][a-z0-9./-]{3,120}", expected_contract)):
            raise HostBridgeError("Invalid host contract")
        self.origin = f"http://127.0.0.1:{port}"
        self.expected_contract = expected_contract
        self.token_file = Path(token_file) if token_file is not None else None
        self.timeout = min(max(float(timeout), 1), 60)
        self._opener = build_opener(NoRedirect())
        self._manifest = None

    def _request(self, *, route: str, method="GET", body=None, headers=None, output_file=None):
        if not route.startswith("/") or route.startswith("//") or "#" in route:
            raise HostBridgeError("Invalid host route")
        raw = None
        if body is not None:
            raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
            if len(raw) > MAX_BODY_BYTES:
                raise HostBridgeError("Body too large; send smaller batches")
        request = Request(self.origin + route, data=raw, method=method, headers={
            "Accept": "image/*" if output_file else "application/json",
            **({"Content-Type": "application/json"} if raw is not None else {}),
            **(headers or {}),
        })
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                content_type = response.headers.get("Content-Type", "")
                if output_file:
                    mime=content_type.split(";",1)[0].lower().strip()
                    if mime not in ("image/jpeg","image/png","image/webp","image/gif","image/avif"):
                        raise HostBridgeError("Host media response is not supported raster image")
                    target=Path(output_file).expanduser()
                    if not target.is_absolute() or target.suffix.lower() not in (".jpg",".jpeg",".png",".webp",".gif",".avif"):
                        raise HostBridgeError("Output must be an absolute raster image path")
                    data=response.read(MAX_MEDIA_BYTES+1)
                    if len(data)>MAX_MEDIA_BYTES:
                        raise HostBridgeError("Image too large; request a smaller preview")
                    try:
                        with target.open("xb") as handle:
                            handle.write(data)
                    except FileExistsError as exc:
                        raise HostBridgeError("Output image already exists") from exc
                    return {"image_path":str(target),"mime_type":mime,
                            "size_bytes":len(data),"sha256":hashlib.sha256(data).hexdigest()}
                if "application/json" not in content_type:
                    raise HostBridgeError("Host response is not JSON")
                data = response.read(MAX_RESPONSE_BYTES + 1)
                if len(data) > MAX_RESPONSE_BYTES:
                    raise HostBridgeError("Host response too large; use pagination")
                return json.loads(data)
        except HTTPError as error:
            raise HostBridgeError(f"Host HTTP {error.code}") from error
        except URLError as error:
            raise HostBridgeError("Local application host unavailable") from error

    def capabilities(self):
        if self._manifest is None:
            manifest = self._request(route="/api/capabilities")
            if not isinstance(manifest, dict):
                raise HostBridgeError("Host contract must be an object")
            contract = manifest.get("contract")
            if not isinstance(contract, str) or not contract:
                raise HostBridgeError("Host does not expose a contract")
            if self.expected_contract and contract != self.expected_contract:
                raise HostBridgeError("Unexpected local host contract")
            ops = manifest.get("operations")
            if not isinstance(ops, list) or len(ops) > 200:
                raise HostBridgeError("Invalid host operation catalog")
            names = set()
            for op in ops:
                if not isinstance(op, dict):
                    raise HostBridgeError("Invalid operation")
                ident, method, path = op.get("id"), op.get("method"), op.get("path")
                if not isinstance(ident, str) or not OP_ID.fullmatch(ident):
                    raise HostBridgeError("Invalid operation identifier")
                if ident in names:
                    raise HostBridgeError("Duplicate operation identifier")
                names.add(ident)
                if method not in ("GET", "POST") or not isinstance(path,str):
                    raise HostBridgeError("Invalid operation method or path")
                if not SAFE_PATH.fullmatch(path) or ".." in path:
                    raise HostBridgeError("Unsafe operation path")
                if op.get("effect") not in ("read", "write"):
                    raise HostBridgeError("Invalid operation effect")
                if (method == "GET") != (op["effect"] == "read"):
                    raise HostBridgeError("Method and effect conflict")
            self._manifest = manifest
        return self._manifest

    def invoke(self, operation: str, *, params=None, body=None, output_file=None):
        if not self.expected_contract:
            raise HostBridgeError("Expected contract required to execute operations")
        manifest = self.capabilities()
        ops = {x["id"]:x for x in manifest["operations"]}
        spec = ops.get(operation)
        if not spec:
            raise HostBridgeError("Operation absent from host's capability catalog")
        params = dict(params or {})
        if not isinstance(params,dict):
            raise HostBridgeError("Parameters must be an object")
        route = spec["path"]
        for name in SAFE_TEMPLATE.findall(route):
            value = params.pop(name, None)
            if value is None or not isinstance(value, (str,int)) or isinstance(value,bool):
                raise HostBridgeError("Missing or invalid path parameter: "+name)
            route = route.replace("{"+name+"}",quote(str(value),safe=""))
        if "{" in route or "}" in route:
            raise HostBridgeError("Unresolved path parameter")
        if any(not ARG_NAME.fullmatch(k) for k in params):
            raise HostBridgeError("Invalid query argument")
        if any(not isinstance(v,(str,int,float,bool)) for v in params.values()):
            raise HostBridgeError("Invalid query value")
        if spec["method"] == "GET":
            if body is not None:
                raise HostBridgeError("Read action does not accept a body")
            if params:
                route+="?"+urlencode(params)
            if output_file and not spec["path"].startswith("/media/"):
                raise HostBridgeError("Image output is only valid for declared media actions")
            if spec["path"].startswith("/media/") and not output_file:
                raise HostBridgeError("Media action requires --output-file")
            return self._request(route=route,output_file=output_file)
        if not isinstance(body,dict):
            raise HostBridgeError("Write operation requires a JSON object")
        if not self.token_file or not self.token_file.is_file():
            raise HostBridgeError("Local write token file required")
        auth=manifest.get("write_auth")
        if not isinstance(auth,dict) or auth.get("type")!="local-token":
            raise HostBridgeError("Host write-auth contract is unsupported")
        header=auth.get("header")
        if not isinstance(header,str) or not re.fullmatch(r"X-[A-Za-z0-9-]{3,60}",header):
            raise HostBridgeError("Unsafe host auth header")
        token=self.token_file.read_text(encoding="ascii").strip()
        if not 16<=len(token)<=512 or "\n" in token or "\r" in token:
            raise HostBridgeError("Invalid local host token")
        if params:
            route+="?"+urlencode(params)
        return self._request(route=route,method="POST",body=body,
                             headers={header:token,"Origin":self.origin})


def main(argv=None):
    parser=argparse.ArgumentParser(description="OrdaX MCP: local host capabilities (loopback only)")
    parser.add_argument("--port",type=int,required=True)
    parser.add_argument("--contract")
    parser.add_argument("--token-file")
    parser.add_argument("--operation",default="")
    parser.add_argument("--params",default="{}")
    parser.add_argument("--body")
    parser.add_argument("--summary",action="store_true")
    parser.add_argument("--output-file")
    args=parser.parse_args(argv)
    client=LocalAppHost(port=args.port,expected_contract=args.contract,
                        token_file=args.token_file)
    try:
        if args.operation:
            result=client.invoke(args.operation,params=json.loads(args.params),
                                 body=json.loads(args.body) if args.body is not None else None,
                                 output_file=args.output_file)
        else:
            result=client.capabilities()
        if args.summary and isinstance(result,dict):
            result={k:v for k,v in result.items()
                    if not isinstance(v,list) or k=="operations"}
        print(json.dumps(result,ensure_ascii=False))
    except (HostBridgeError,ValueError,RuntimeError) as error:
        print(json.dumps({"ok":False,"error":str(error)},ensure_ascii=False))
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
