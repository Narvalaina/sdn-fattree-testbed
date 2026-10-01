from __future__ import annotations

import base64
import json
import time
from dataclasses import dataclass
from socket import timeout as SocketTimeout
from typing import Any, Dict, List, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class OdlAuth:
    user: str = "admin"
    password: str = ""


class OdlClient:
    """
    Cliente RESTCONF mínimo para OpenDaylight usando solo la librería estándar.
    Base URL típica: http://127.0.0.1:8181
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8181,
        auth: Optional[OdlAuth] = None,
        timeout_s: int = 6,
    ):
        self.host = host
        self.port = int(port)
        self.auth = auth or OdlAuth()
        self.timeout_s = int(timeout_s)

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def _auth_header(self) -> str:
        if not self.auth.password:
            raise ValueError("OpenDaylight password is empty. Set ODL_PASS or pass OdlAuth explicitly.")
        token = base64.b64encode(
            f"{self.auth.user}:{self.auth.password}".encode("utf-8")
        ).decode("ascii")
        return f"Basic {token}"

    def request_text(
        self,
        method: str,
        path: str,
        body: bytes | None = None,
        content_type: str | None = None,
        accept: str | None = "application/json",
        headers: dict | None = None,
    ) -> Tuple[int, str]:
        url = f"{self.base_url}{path}"

        req_headers: Dict[str, str] = {
            "Authorization": self._auth_header(),
        }

        if accept:
            req_headers["Accept"] = accept

        if content_type:
            req_headers["Content-Type"] = content_type

        if headers:
            req_headers.update(headers)

        req = Request(url, data=body, method=method, headers=req_headers)

        try:
            with urlopen(req, timeout=self.timeout_s) as resp:
                text = resp.read().decode("utf-8", errors="replace")
                code = getattr(resp, "status", 200)
                return code, text

        except HTTPError as e:
            try:
                text = e.read().decode("utf-8", errors="replace")
            except Exception:
                text = str(e)
            return e.code, text

        except (URLError, TimeoutError, SocketTimeout) as e:
            return 599, f"{type(e).__name__}: {e}"

    def get_text(self, path: str) -> Tuple[int, str]:
        return self.request_text("GET", path)

    def get_json(self, path: str) -> Tuple[int, Dict[str, Any]]:
        code, text = self.get_text(path)

        if code != 200:
            return code, {}

        try:
            return code, json.loads(text) if text.strip() else {}
        except Exception:
            return 598, {}

    def put_json(self, path: str, payload: Dict[str, Any]) -> Tuple[int, str]:
        raw = json.dumps(payload).encode("utf-8")
        return self.request_text(
            "PUT",
            path,
            body=raw,
            content_type="application/json",
            accept="application/json",
        )

    def delete(self, path: str) -> Tuple[int, str]:
        return self.request_text(
            "DELETE",
            path,
            body=None,
            content_type=None,
            accept="application/json",
        )

    def inventory_nodes(self) -> Tuple[int, List[str]]:
        code, data = self.get_json("/rests/data/opendaylight-inventory:nodes?content=nonconfig")
        if code != 200:
            return code, []

        nodes: List[str] = []
        try:
            root = data.get("opendaylight-inventory:nodes", {})
            for n in root.get("node", []):
                node_id = n.get("id")
                if node_id:
                    nodes.append(node_id)
        except Exception:
            return 598, []

        return 200, nodes

    def wait_for_nodes(
        self,
        expected_nodes: List[str],
        timeout_s: int = 35,
        interval_s: float = 1.0,
    ) -> bool:
        deadline = time.time() + timeout_s
        expected = set(expected_nodes)

        while time.time() < deadline:
            code, nodes = self.inventory_nodes()

            if code == 200 and expected.issubset(set(nodes)):
                return True

            time.sleep(interval_s)

        return False

    def topology_raw(self) -> Tuple[int, str]:
        return self.get_text("/rests/data/network-topology:network-topology?content=nonconfig")

    def table0(self, node: str, content: str = "nonconfig") -> Tuple[int, Dict[str, Any]]:
        path = (
            f"/rests/data/opendaylight-inventory:nodes/node={node}"
            f"/flow-node-inventory:table=0?content={content}"
        )
        return self.get_json(path)

    def delete_flow(self, node: str, table_id: int, flow_id: str) -> Tuple[int, str]:
        path = (
            f"/rests/data/opendaylight-inventory:nodes/node={node}"
            f"/flow-node-inventory:table={table_id}/flow={flow_id}"
        )
        return self.delete(path)

    def put_flow(self, node: str, table_id: int, flow: Dict[str, Any]) -> Tuple[int, str]:
        fid = flow.get("id")
        if not isinstance(fid, str) or not fid:
            return 0, "Invalid flow: missing string 'id'"

        path = (
            f"/rests/data/opendaylight-inventory:nodes/node={node}"
            f"/flow-node-inventory:table={table_id}/flow={fid}"
        )

        payload = {"flow-node-inventory:flow": [flow]}
        return self.put_json(path, payload)
