#!/usr/bin/env python3
"""
Capítulo 8 — Fase D.3: intento controlado de migración RESTCONF del prototipo exploratorio.

Ruta esperada:
    tfg_sdn/experiments/ch08_ecmp_full_restconf_attempt.py

Objetivo:
    Migrar de forma controlada a RESTCONF el prototipo exploratorio de la Fase D
    que previamente funcionó en datapath mediante ovs-ofctl.

Alcance metodológico:
    - No pretende demostrar ECMP completo Fat-Tree k=4 si D.1/D.2 no lo hicieron.
    - Comprueba si ODL RESTCONF puede instalar los grupos select y los flows del
      prototipo D.1/D.2 en OVS de forma trazable.
    - Primero valida instalación y tráfico h1 <-> h16.
    - Después permite una prueba de stress h1<->h16 + h2<->h15.
    - Mantiene criterios de parada: si RESTCONF introduce demasiada complejidad,
      se documenta como intento parcial.

Acciones disponibles:
    --action d3-cleanup-ovs
    --action d3-install-groups-restconf
    --action d3-verify-groups
    --action d3-install-flows-restconf
    --action d3-prepare-traffic
    --action d3-summarize-traffic
    --action d3-install-stress-flows-restconf
    --action d3-prepare-stress
    --action d3-summarize-stress
    --action status

Uso típico:
    cd /path/to/sdn-fattree-testbed
    export TFG_ROOT="$PWD"
    export PYTHONPATH="$TFG_ROOT:$PYTHONPATH"
    export ODL_HOST="127.0.0.1"
    export ODL_REST_PORT="8181"
    export ODL_USER="admin"
    export ODL_PASS="<set-locally>"
    sudo -v

    export CAMPAIGN_ID="ch08_fattree_ecmp_full_restconf_$(date +%Y%m%d_%H%M)"

    python3 tfg_sdn/experiments/ch08_ecmp_full_restconf_attempt.py \
      --campaign-id "$CAMPAIGN_ID" --action d3-cleanup-ovs

    python3 tfg_sdn/experiments/ch08_ecmp_full_restconf_attempt.py \
      --campaign-id "$CAMPAIGN_ID" --action d3-install-groups-restconf

    python3 tfg_sdn/experiments/ch08_ecmp_full_restconf_attempt.py \
      --campaign-id "$CAMPAIGN_ID" --action d3-verify-groups

    python3 tfg_sdn/experiments/ch08_ecmp_full_restconf_attempt.py \
      --campaign-id "$CAMPAIGN_ID" --action d3-install-flows-restconf

    python3 tfg_sdn/experiments/ch08_ecmp_full_restconf_attempt.py \
      --campaign-id "$CAMPAIGN_ID" --action d3-prepare-traffic

    # Dentro de Mininet:
    # source <ruta mostrada por d3-prepare-traffic>

    python3 tfg_sdn/experiments/ch08_ecmp_full_restconf_attempt.py \
      --campaign-id "$CAMPAIGN_ID" --action d3-summarize-traffic
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


CAMPAIGN_PREFIX = "ch08_fattree_ecmp_full_restconf_"
OPENFLOW13 = "OpenFlow13"
GROUP_ID = 200
TABLE_ID = 0

HOST_MAC = {
    "h1": "00:00:00:00:00:01",
    "h2": "00:00:00:00:00:02",
    "h15": "00:00:00:00:00:0f",
    "h16": "00:00:00:00:00:10",
}

HOST_IP = {
    "h1": "10.0.0.1",
    "h2": "10.0.0.2",
    "h15": "10.0.0.15",
    "h16": "10.0.0.16",
}

RELEVANT_SWITCHES = ["e1", "e8", "a1", "a2", "a7", "a8", "c1", "c2", "c3", "c4"]
GROUP_SWITCHES = ["e1", "a1", "a2", "e8", "a7", "a8"]
AGGREGATION_GROUP_SWITCHES = ["a1", "a2", "a7", "a8"]

EXPECTED_NODE_IDS = {
    "c1": "openflow:1",
    "c2": "openflow:2",
    "c3": "openflow:3",
    "c4": "openflow:4",
    "a1": "openflow:5",
    "a2": "openflow:6",
    "a7": "openflow:11",
    "a8": "openflow:12",
    "e1": "openflow:13",
    "e8": "openflow:20",
}

GROUP_BUCKET_PORTS = {
    "e1": [3, 4],
    "a1": [3, 4],
    "a2": [3, 4],
    "e8": [3, 4],
    "a7": [3, 4],
    "a8": [3, 4],
}


@dataclass(frozen=True)
class FlowSpec:
    bridge: str
    flow_id: str
    cookie: int
    priority: int
    dst_mac: Optional[str]
    action_kind: str  # output, group, drop
    action_value: Optional[int] = None
    eth_type: Optional[int] = None
    note: str = ""


D1_FLOWS: List[FlowSpec] = [
    # ARP drop + table miss se instalan también por RESTCONF para los switches relevantes.
    # h1 -> h16 y h16 -> h1.
    FlowSpec("e1", "d3_e1_to_h16_group", 0xD301, 250, HOST_MAC["h16"], "group", GROUP_ID, note="h1->h16 branching at e1"),
    FlowSpec("e1", "d3_e1_to_h1_local", 0xD302, 250, HOST_MAC["h1"], "output", 1, note="return to h1 local port"),
    FlowSpec("e8", "d3_e8_to_h1_group", 0xD303, 250, HOST_MAC["h1"], "group", GROUP_ID, note="h16->h1 branching at e8"),
    FlowSpec("e8", "d3_e8_to_h16_local", 0xD304, 250, HOST_MAC["h16"], "output", 2, note="delivery to h16"),
    FlowSpec("a1", "d3_a1_to_h16_group", 0xD305, 250, HOST_MAC["h16"], "group", GROUP_ID),
    FlowSpec("a1", "d3_a1_to_h1_down", 0xD306, 250, HOST_MAC["h1"], "output", 1),
    FlowSpec("a2", "d3_a2_to_h16_group", 0xD307, 250, HOST_MAC["h16"], "group", GROUP_ID),
    FlowSpec("a2", "d3_a2_to_h1_down", 0xD308, 250, HOST_MAC["h1"], "output", 1),
    FlowSpec("a7", "d3_a7_to_h16_down", 0xD309, 250, HOST_MAC["h16"], "output", 2),
    FlowSpec("a7", "d3_a7_to_h1_group", 0xD30A, 250, HOST_MAC["h1"], "group", GROUP_ID),
    FlowSpec("a8", "d3_a8_to_h16_down", 0xD30B, 250, HOST_MAC["h16"], "output", 2),
    FlowSpec("a8", "d3_a8_to_h1_group", 0xD30C, 250, HOST_MAC["h1"], "group", GROUP_ID),
    FlowSpec("c1", "d3_c1_to_h16", 0xD30D, 250, HOST_MAC["h16"], "output", 4),
    FlowSpec("c1", "d3_c1_to_h1", 0xD30E, 250, HOST_MAC["h1"], "output", 1),
    FlowSpec("c2", "d3_c2_to_h16", 0xD30F, 250, HOST_MAC["h16"], "output", 4),
    FlowSpec("c2", "d3_c2_to_h1", 0xD310, 250, HOST_MAC["h1"], "output", 1),
    FlowSpec("c3", "d3_c3_to_h16", 0xD311, 250, HOST_MAC["h16"], "output", 4),
    FlowSpec("c3", "d3_c3_to_h1", 0xD312, 250, HOST_MAC["h1"], "output", 1),
    FlowSpec("c4", "d3_c4_to_h16", 0xD313, 250, HOST_MAC["h16"], "output", 4),
    FlowSpec("c4", "d3_c4_to_h1", 0xD314, 250, HOST_MAC["h1"], "output", 1),
]

D2_STRESS_FLOWS: List[FlowSpec] = [
    # h2 -> h15.
    FlowSpec("e1", "d3_e1_to_h15_group", 0xD401, 250, HOST_MAC["h15"], "group", GROUP_ID),
    FlowSpec("a1", "d3_a1_to_h15_group", 0xD402, 250, HOST_MAC["h15"], "group", GROUP_ID),
    FlowSpec("a2", "d3_a2_to_h15_group", 0xD403, 250, HOST_MAC["h15"], "group", GROUP_ID),
    FlowSpec("c1", "d3_c1_to_h15", 0xD404, 250, HOST_MAC["h15"], "output", 4),
    FlowSpec("c2", "d3_c2_to_h15", 0xD405, 250, HOST_MAC["h15"], "output", 4),
    FlowSpec("c3", "d3_c3_to_h15", 0xD406, 250, HOST_MAC["h15"], "output", 4),
    FlowSpec("c4", "d3_c4_to_h15", 0xD407, 250, HOST_MAC["h15"], "output", 4),
    FlowSpec("a7", "d3_a7_to_h15_down", 0xD408, 250, HOST_MAC["h15"], "output", 2),
    FlowSpec("a8", "d3_a8_to_h15_down", 0xD409, 250, HOST_MAC["h15"], "output", 2),
    FlowSpec("e8", "d3_e8_to_h15_local", 0xD40A, 250, HOST_MAC["h15"], "output", 1),
    # h15 -> h2.
    FlowSpec("e8", "d3_e8_to_h2_group", 0xD40B, 250, HOST_MAC["h2"], "group", GROUP_ID),
    FlowSpec("a7", "d3_a7_to_h2_group", 0xD40C, 250, HOST_MAC["h2"], "group", GROUP_ID),
    FlowSpec("a8", "d3_a8_to_h2_group", 0xD40D, 250, HOST_MAC["h2"], "group", GROUP_ID),
    FlowSpec("c1", "d3_c1_to_h2", 0xD40E, 250, HOST_MAC["h2"], "output", 1),
    FlowSpec("c2", "d3_c2_to_h2", 0xD40F, 250, HOST_MAC["h2"], "output", 1),
    FlowSpec("c3", "d3_c3_to_h2", 0xD410, 250, HOST_MAC["h2"], "output", 1),
    FlowSpec("c4", "d3_c4_to_h2", 0xD411, 250, HOST_MAC["h2"], "output", 1),
    FlowSpec("a1", "d3_a1_to_h2_down", 0xD412, 250, HOST_MAC["h2"], "output", 1),
    FlowSpec("a2", "d3_a2_to_h2_down", 0xD413, 250, HOST_MAC["h2"], "output", 1),
    FlowSpec("e1", "d3_e1_to_h2_local", 0xD414, 250, HOST_MAC["h2"], "output", 2),
]


def default_tfg_root() -> Path:
    return Path(os.environ.get("TFG_ROOT", str(Path(__file__).resolve().parents[2]))).expanduser()


def now_id() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M")


def find_latest_campaign(tfg_root: Path) -> Optional[str]:
    base = tfg_root / "runs" / "ch08_fattree"
    if not base.exists():
        return None
    candidates = sorted(
        [p.name for p in base.iterdir() if p.is_dir() and p.name.startswith(CAMPAIGN_PREFIX)],
        reverse=True,
    )
    return candidates[0] if candidates else None


def resolve_campaign_id(tfg_root: Path, campaign_id: Optional[str], action: str) -> str:
    if campaign_id:
        return campaign_id
    latest = find_latest_campaign(tfg_root)
    if latest and action not in {"d3-cleanup-ovs"}:
        return latest
    return CAMPAIGN_PREFIX + now_id()


@dataclass(frozen=True)
class CampaignPaths:
    tfg_root: Path
    campaign_id: str

    @property
    def root(self) -> Path:
        return self.tfg_root / "runs" / "ch08_fattree" / self.campaign_id

    @property
    def cleanup(self) -> Path:
        return self.root / "d3_cleanup_ovs"

    @property
    def groups(self) -> Path:
        return self.root / "d3_groups_restconf"

    @property
    def flows(self) -> Path:
        return self.root / "d3_flows_restconf"

    @property
    def traffic(self) -> Path:
        return self.root / "d3_traffic_restconf"

    @property
    def stress_flows(self) -> Path:
        return self.root / "d3_stress_flows_restconf"

    @property
    def stress(self) -> Path:
        return self.root / "d3_stress_restconf"

    @property
    def summary(self) -> Path:
        return self.root / "d3_summary"

    def ensure_base(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def read_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, sort_keys=False), encoding="utf-8")


def run_cmd(cmd: Sequence[str], check: bool = True, output_path: Optional[Path] = None) -> subprocess.CompletedProcess:
    proc = subprocess.run(list(cmd), text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            "$ " + " ".join(cmd) + "\n\n" + proc.stdout + ("\n[stderr]\n" + proc.stderr if proc.stderr else ""),
            encoding="utf-8",
        )
    if check and proc.returncode != 0:
        raise RuntimeError(
            f"[FAIL] Comando fallido ({proc.returncode}): {' '.join(cmd)}\n"
            f"stdout:\n{proc.stdout}\n"
            f"stderr:\n{proc.stderr}"
        )
    return proc


def sudo_cmd(args: Sequence[str], check: bool = True, output_path: Optional[Path] = None) -> subprocess.CompletedProcess:
    return run_cmd(["sudo", "-n", *args], check=check, output_path=output_path)


def require_sudo_cached() -> None:
    proc = subprocess.run(["sudo", "-n", "true"], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        raise SystemExit(
            "[FAIL] sudo no está cacheado. Ejecuta primero:\n\n"
            "    sudo -v\n\n"
            "y repite el comando."
        )


def ovs_bridge_exists(bridge: str) -> bool:
    proc = sudo_cmd(["ovs-vsctl", "br-exists", bridge], check=False)
    return proc.returncode == 0


def get_bridge_dpid(bridge: str) -> Optional[str]:
    proc = sudo_cmd(["ovs-vsctl", "get", "Bridge", bridge, "datapath_id"], check=False)
    if proc.returncode != 0:
        return None
    return proc.stdout.strip().strip('"')


def bridge_to_node_id(bridge: str) -> Optional[str]:
    dpid = get_bridge_dpid(bridge)
    if not dpid:
        return None
    try:
        return f"openflow:{int(dpid, 16)}"
    except ValueError:
        return None


def dump_ovs_switch_state(out_dir: Path, bridges: Sequence[str]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    sudo_cmd(["ovs-vsctl", "show"], check=False, output_path=out_dir / "ovs_vsctl_show.txt")
    for br in bridges:
        if not ovs_bridge_exists(br):
            write_text(out_dir / f"{br}_missing.txt", f"Bridge {br} not found\n")
            continue
        sudo_cmd(["ovs-ofctl", "-O", OPENFLOW13, "show", br], check=False, output_path=out_dir / f"{br}_ofctl_show.txt")
        sudo_cmd(["ovs-ofctl", "-O", OPENFLOW13, "dump-flows", br], check=False, output_path=out_dir / f"{br}_flows.txt")
        sudo_cmd(["ovs-ofctl", "-O", OPENFLOW13, "dump-groups", br], check=False, output_path=out_dir / f"{br}_groups.txt")
        sudo_cmd(["ovs-ofctl", "-O", OPENFLOW13, "dump-group-stats", br], check=False, output_path=out_dir / f"{br}_group_stats.txt")


def ovs_group_present(bridge: str) -> bool:
    proc = sudo_cmd(["ovs-ofctl", "-O", OPENFLOW13, "dump-groups", bridge], check=False)
    return proc.returncode == 0 and f"group_id={GROUP_ID}" in proc.stdout and "type=select" in proc.stdout


def ovs_flow_present(bridge: str, flow: FlowSpec) -> bool:
    proc = sudo_cmd(["ovs-ofctl", "-O", OPENFLOW13, "dump-flows", bridge], check=False)
    if proc.returncode != 0:
        return False
    text = proc.stdout
    if flow.dst_mac and flow.dst_mac not in text:
        return False
    if flow.action_kind == "group":
        return f"actions=group:{flow.action_value}" in text and flow.dst_mac in text
    if flow.action_kind == "output":
        return f"actions=output:{flow.action_value}" in text and flow.dst_mac in text
    if flow.action_kind == "drop":
        return "actions=drop" in text
    return False


@dataclass
class RestconfClient:
    host: str
    port: str
    user: str
    password: str
    timeout: int = 20

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def _auth_header(self) -> str:
        token = f"{self.user}:{self.password}".encode("utf-8")
        return "Basic " + base64.b64encode(token).decode("ascii")

    def request(
        self,
        method: str,
        path: str,
        body: Optional[bytes] = None,
        content_type: Optional[str] = None,
        accept: str = "application/json",
    ) -> Tuple[int, str, Dict[str, str]]:
        url = self.base_url + path
        headers = {"Authorization": self._auth_header(), "Accept": accept}
        if content_type:
            headers["Content-Type"] = content_type
        req = urllib.request.Request(url, data=body, headers=headers, method=method.upper())
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                return resp.status, raw, dict(resp.headers.items())
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            return exc.code, raw, dict(exc.headers.items())
        except urllib.error.URLError as exc:
            return 0, str(exc), {}


def restconf_client_from_env() -> RestconfClient:
    return RestconfClient(
        host=os.environ.get("ODL_HOST", "127.0.0.1"),
        port=os.environ.get("ODL_REST_PORT", "8181"),
        user=os.environ.get("ODL_USER", "admin"),
        password=os.environ.get("ODL_PASS", ""),
    )


def node_ref(node_id: str) -> str:
    return f"/opendaylight-inventory:nodes/opendaylight-inventory:node[opendaylight-inventory:id='{node_id}']"


def group_ref(node_id: str) -> str:
    return (
        f"/opendaylight-inventory:nodes/opendaylight-inventory:node[opendaylight-inventory:id='{node_id}']"
        f"/flow-node-inventory:group[flow-node-inventory:group-id='{GROUP_ID}']"
    )


def make_group_rpc_json(node_id: str, bridge: str, buckets: Sequence[int]) -> Dict[str, Any]:
    bucket_items = []
    for idx, port in enumerate(buckets):
        bucket_items.append(
            {
                "bucket-id": idx,
                "weight": 1,
                "watch_port": 4294967295,
                "watch_group": 4294967295,
                "action": [
                    {
                        "order": 0,
                        "output-action": {
                            "output-node-connector": str(port),
                            "max-length": 65535,
                        },
                    }
                ],
            }
        )
    return {
        "input": {
            "node": node_ref(node_id),
            "group-ref": group_ref(node_id),
            "group-id": GROUP_ID,
            "group-name": f"d3_select_{bridge}",
            "group-type": "group-select",
            "barrier": False,
            "buckets": {"bucket": bucket_items},
        }
    }


def make_group_datastore_payload(bridge: str, buckets: Sequence[int]) -> Dict[str, Any]:
    bucket_items = []
    for idx, port in enumerate(buckets):
        bucket_items.append(
            {
                "bucket-id": idx,
                "weight": 1,
                "watch_port": 4294967295,
                "watch_group": 4294967295,
                "action": [
                    {
                        "order": 0,
                        "output-action": {
                            "output-node-connector": str(port),
                            "max-length": 65535,
                        },
                    }
                ],
            }
        )
    return {
        "flow-node-inventory:group": [
            {
                "group-id": GROUP_ID,
                "group-name": f"d3_select_{bridge}",
                "group-type": "group-select",
                "buckets": {"bucket": bucket_items},
            }
        ]
    }


def make_flow_payload(flow: FlowSpec) -> Dict[str, Any]:
    if flow.action_kind == "output":
        action = {
            "order": 0,
            "output-action": {
                "output-node-connector": str(flow.action_value),
                "max-length": 65535,
            },
        }
    elif flow.action_kind == "group":
        action = {
            "order": 0,
            "group-action": {
                "group-id": int(flow.action_value or GROUP_ID),
            },
        }
    elif flow.action_kind == "drop":
        action = {
            "order": 0,
            "drop-action": {},
        }
    else:
        raise ValueError(f"Unsupported action_kind: {flow.action_kind}")

    match: Dict[str, Any] = {}
    if flow.dst_mac:
        match["ethernet-match"] = {
            "ethernet-destination": {
                "address": flow.dst_mac,
            }
        }
    if flow.eth_type is not None:
        match.setdefault("ethernet-match", {})["ethernet-type"] = {"type": int(flow.eth_type)}

    return {
        "flow-node-inventory:flow": [
            {
                "id": flow.flow_id,
                "flow-name": flow.flow_id,
                "table_id": TABLE_ID,
                "priority": flow.priority,
                "cookie": flow.cookie,
                "hard-timeout": 0,
                "idle-timeout": 0,
                "match": match,
                "instructions": {
                    "instruction": [
                        {
                            "order": 0,
                            "apply-actions": {
                                "action": [action]
                            },
                        }
                    ]
                },
            }
        ]
    }


def restconf_put_json(client: RestconfClient, path: str, payload: Dict[str, Any], out_dir: Path, tag: str) -> Dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    body = json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8")
    write_text(out_dir / f"{tag}_request.json", body.decode("utf-8"))
    status, response, headers = client.request(
        "PUT",
        path,
        body=body,
        content_type="application/json",
        accept="application/json",
    )
    write_text(out_dir / f"{tag}_response.txt", response)
    write_json(out_dir / f"{tag}_response_meta.json", {"http_status": status, "headers": headers})
    return {"http_status": status, "success_status": status in {200, 201, 204}, "response_preview": response[:300]}


def restconf_post_json(client: RestconfClient, path: str, payload: Dict[str, Any], out_dir: Path, tag: str) -> Dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    body = json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8")
    write_text(out_dir / f"{tag}_request.json", body.decode("utf-8"))
    status, response, headers = client.request(
        "POST",
        path,
        body=body,
        content_type="application/json",
        accept="application/json",
    )
    write_text(out_dir / f"{tag}_response.txt", response)
    write_json(out_dir / f"{tag}_response_meta.json", {"http_status": status, "headers": headers})
    return {"http_status": status, "success_status": status in {200, 201, 204}, "response_preview": response[:300]}


def fetch_node_operational(client: RestconfClient, node_id: str, out_path: Path) -> Tuple[int, str]:
    status, body, headers = client.request(
        "GET",
        f"/rests/data/opendaylight-inventory:nodes/node={node_id}?content=nonconfig",
        accept="application/json",
    )
    write_text(out_path, body)
    write_json(out_path.with_suffix(".meta.json"), {"http_status": status, "headers": headers})
    return status, body


def node_operational_has_group(body: str, group_id: int = GROUP_ID) -> bool:
    def walk(obj: Any) -> bool:
        if isinstance(obj, dict):
            group_id_value = None
            group_type_value = None
            for key, value in obj.items():
                local_key = str(key).split(":")[-1]
                if local_key == "group-id":
                    group_id_value = value
                elif local_key == "group-type":
                    group_type_value = value
            if group_id_value is not None:
                try:
                    group_id_ok = int(group_id_value) == group_id
                except (TypeError, ValueError):
                    group_id_ok = str(group_id_value) == str(group_id)
                group_type_text = str(group_type_value or "")
                if group_id_ok and (not group_type_text or "select" in group_type_text.lower()):
                    return True
            return any(walk(value) for value in obj.values())
        if isinstance(obj, list):
            return any(walk(item) for item in obj)
        return False

    try:
        return walk(json.loads(body))
    except json.JSONDecodeError:
        compact = body.replace(" ", "").replace("\n", "").lower()
        return "group-id" in compact and str(group_id) in compact and "select" in compact


def parse_ping(path: Path) -> Dict[str, Any]:
    text = read_text(path)
    loss_match = re.search(r"(\d+(?:\.\d+)?)% packet loss", text)
    rtt_match = re.search(r"rtt min/avg/max/mdev = [0-9.]+/([0-9.]+)/", text)
    loss = float(loss_match.group(1)) if loss_match else None
    rtt_avg = float(rtt_match.group(1)) if rtt_match else None
    return {"path": str(path), "packet_loss_pct": loss, "rtt_avg_ms": rtt_avg, "ping_ok": loss == 0.0}


def parse_iperf(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {"path": str(path), "ok": False, "gbps": None, "error": "missing"}
    try:
        data = json.loads(read_text(path))
        bps = data.get("end", {}).get("sum_received", {}).get("bits_per_second")
        if bps is None:
            bps = data.get("end", {}).get("sum_sent", {}).get("bits_per_second")
        gbps = float(bps) / 1e9 if bps is not None else None
        return {"path": str(path), "ok": gbps is not None, "gbps": gbps}
    except Exception as exc:
        return {"path": str(path), "ok": False, "gbps": None, "error": str(exc)}


def parse_group_stats(path: Path) -> Dict[str, Any]:
    text = read_text(path)
    group_match = re.search(r"group_id=200.*?packet_count=(\d+).*?byte_count=(\d+)", text)
    bucket_matches = re.findall(r"bucket(\d+):packet_count=(\d+),byte_count=(\d+)", text)
    buckets = {
        f"bucket{idx}": {"packets": int(packets), "bytes": int(bytes_), "active": int(packets) > 0}
        for idx, packets, bytes_ in bucket_matches
    }
    return {
        "path": str(path),
        "group_present": group_match is not None,
        "group_packets": int(group_match.group(1)) if group_match else 0,
        "group_bytes": int(group_match.group(2)) if group_match else 0,
        "buckets": buckets,
        "active_buckets": sum(1 for row in buckets.values() if row["active"]),
    }


def summarize_iperf_dir(iperf_dir: Path, pattern: str = "h*_h*_p*.json") -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], float]:
    files = sorted(iperf_dir.glob(pattern))
    rows = [parse_iperf(path) for path in files]
    ok_rows = [row for row in rows if row["ok"]]
    total_gbps = sum(float(row["gbps"]) for row in ok_rows if row["gbps"] is not None)
    return rows, ok_rows, total_gbps


def summarize_groups(stats_dir: Path) -> Dict[str, Dict[str, Any]]:
    return {br: parse_group_stats(stats_dir / f"{br}_group_stats.txt") for br in GROUP_SWITCHES}


def make_mininet_line(host: str, command: str) -> str:
    return f"{host} {command}"


def cleanup_switches(paths: CampaignPaths) -> Dict[str, Any]:
    result: Dict[str, Any] = {"bridges": {}, "status": "PASS"}
    for br in RELEVANT_SWITCHES:
        if not ovs_bridge_exists(br):
            result["bridges"][br] = {"exists": False, "status": "MISSING"}
            result["status"] = "CHECK_REQUIRED"
            continue
        sudo_cmd(["ovs-ofctl", "-O", OPENFLOW13, "del-flows", br], check=False)
        sudo_cmd(["ovs-ofctl", "-O", OPENFLOW13, "del-groups", br], check=False)
        result["bridges"][br] = {"exists": True, "status": "CLEANED"}
    dump_ovs_switch_state(paths.cleanup / "ovs_after_cleanup", RELEVANT_SWITCHES)
    return result


def action_d3_cleanup_ovs(paths: CampaignPaths) -> None:
    require_sudo_cached()
    result = cleanup_switches(paths)
    write_json(paths.cleanup / "cleanup_ovs_summary.json", result)
    print("D.3 cleanup-ovs")
    print("=" * 60)
    print(f"campaign_id: {paths.campaign_id}")
    for br, row in result["bridges"].items():
        print(f"{br}: {row}")
    print(f"status: {result['status']}")
    print(f"summary: {paths.cleanup / 'cleanup_ovs_summary.json'}")


def install_group_for_bridge(client: RestconfClient, paths: CampaignPaths, bridge: str) -> Dict[str, Any]:
    node_id = bridge_to_node_id(bridge)
    if node_id is None:
        return {"bridge": bridge, "status": "MISSING_NODE_ID"}
    buckets = GROUP_BUCKET_PORTS[bridge]
    out_dir = paths.groups / "restconf_attempts" / bridge
    out_dir.mkdir(parents=True, exist_ok=True)

    rpc = restconf_post_json(
        client,
        "/rests/operations/sal-group:add-group",
        make_group_rpc_json(node_id, bridge, buckets),
        out_dir,
        "rpc_json_input",
    )
    time.sleep(0.8)
    rpc_ovs_present = ovs_group_present(bridge)

    ds_path = (
        f"/rests/data/opendaylight-inventory:nodes/node={node_id}"
        f"/flow-node-inventory:group={GROUP_ID}"
    )
    ds = restconf_put_json(
        client,
        ds_path,
        make_group_datastore_payload(bridge, buckets),
        out_dir,
        "datastore_ns_group",
    )
    time.sleep(1.5)
    ds_ovs_present = ovs_group_present(bridge)

    return {
        "bridge": bridge,
        "node_id": node_id,
        "expected_node_id": EXPECTED_NODE_IDS.get(bridge),
        "node_id_matches_expected": node_id == EXPECTED_NODE_IDS.get(bridge),
        "rpc_json_input": {**rpc, "ovs_present_after": rpc_ovs_present},
        "datastore_ns_group": {**ds, "ovs_present_after": ds_ovs_present},
        "ovs_group_present": ds_ovs_present or rpc_ovs_present,
        "status": "PASS" if (ds_ovs_present or rpc_ovs_present) else "CHECK_REQUIRED",
    }


def action_d3_install_groups_restconf(paths: CampaignPaths) -> None:
    require_sudo_cached()
    client = restconf_client_from_env()
    results = {br: install_group_for_bridge(client, paths, br) for br in GROUP_SWITCHES}
    dump_ovs_switch_state(paths.groups / "ovs_after_groups", RELEVANT_SWITCHES)

    for br in GROUP_SWITCHES:
        node_id = bridge_to_node_id(br)
        if node_id:
            fetch_node_operational(client, node_id, paths.groups / f"odl_operational_after_group_{br}_{node_id.replace(':', '_')}.json")

    status = "PASS" if all(row.get("status") == "PASS" for row in results.values()) else "CHECK_REQUIRED"
    summary = {
        "campaign_id": paths.campaign_id,
        "scope": "D.3 RESTCONF installation of group-select entries for exploratory ECMP prototype",
        "group_id": GROUP_ID,
        "status": status,
        "results": results,
    }
    write_json(paths.groups / "install_groups_restconf_summary.json", summary)

    print("D.3 install-groups-restconf")
    print("=" * 60)
    print(f"campaign_id: {paths.campaign_id}")
    for br, row in results.items():
        print(
            f"{br}: status={row.get('status')} node_id={row.get('node_id')} "
            f"rpc={row.get('rpc_json_input', {}).get('http_status')} "
            f"ds={row.get('datastore_ns_group', {}).get('http_status')} "
            f"ovs_group_present={row.get('ovs_group_present')}"
        )
    print(f"status: {status}")
    print(f"summary: {paths.groups / 'install_groups_restconf_summary.json'}")


def action_d3_verify_groups(paths: CampaignPaths) -> None:
    require_sudo_cached()
    client = restconf_client_from_env()
    dump_ovs_switch_state(paths.groups / "ovs_verify_groups", RELEVANT_SWITCHES)
    rows: Dict[str, Any] = {}
    for br in GROUP_SWITCHES:
        node_id = bridge_to_node_id(br)
        ovs_present = ovs_group_present(br)
        odl_present = False
        odl_status = None
        if node_id:
            odl_status, body = fetch_node_operational(client, node_id, paths.groups / f"odl_operational_verify_group_{br}_{node_id.replace(':', '_')}.json")
            odl_present = node_operational_has_group(body)
        rows[br] = {
            "node_id": node_id,
            "ovs_group_present": ovs_present,
            "odl_get_status": odl_status,
            "odl_group_present": odl_present,
        }
    status = "PASS" if all(row["ovs_group_present"] and row["odl_group_present"] for row in rows.values()) else "CHECK_REQUIRED"
    summary = {"campaign_id": paths.campaign_id, "status": status, "bridges": rows}
    write_json(paths.groups / "verify_groups_summary.json", summary)

    print("D.3 verify-groups")
    print("=" * 60)
    print(f"campaign_id: {paths.campaign_id}")
    for br, row in rows.items():
        print(
            f"{br}: node_id={row.get('node_id')} ovs_group_present={row.get('ovs_group_present')} "
            f"odl_group_present={row.get('odl_group_present')} odl_get_status={row.get('odl_get_status')}"
        )
    print(f"status: {status}")
    print(f"summary: {paths.groups / 'verify_groups_summary.json'}")


def install_flow_via_restconf(client: RestconfClient, paths: CampaignPaths, flow: FlowSpec, out_root: Path) -> Dict[str, Any]:
    node_id = bridge_to_node_id(flow.bridge)
    if node_id is None:
        return {"bridge": flow.bridge, "flow_id": flow.flow_id, "status": "MISSING_NODE_ID"}
    path = (
        f"/rests/data/opendaylight-inventory:nodes/node={node_id}"
        f"/flow-node-inventory:table={TABLE_ID}"
        f"/flow={flow.flow_id}"
    )
    attempt_dir = out_root / "restconf_flow_attempts" / flow.bridge / flow.flow_id
    result = restconf_put_json(client, path, make_flow_payload(flow), attempt_dir, "datastore_flow")
    time.sleep(0.25)
    present = ovs_flow_present(flow.bridge, flow)
    if not present:
        time.sleep(1.0)
        present = ovs_flow_present(flow.bridge, flow)
    return {
        "bridge": flow.bridge,
        "node_id": node_id,
        "flow_id": flow.flow_id,
        "dst_mac": flow.dst_mac,
        "action_kind": flow.action_kind,
        "action_value": flow.action_value,
        "http_status": result["http_status"],
        "restconf_success_status": result["success_status"],
        "ovs_flow_present_after": present,
        "response_preview": result["response_preview"],
        "status": "PASS" if result["success_status"] and present else "CHECK_REQUIRED",
    }


def action_d3_install_flows_restconf(paths: CampaignPaths) -> None:
    require_sudo_cached()
    client = restconf_client_from_env()
    rows = [install_flow_via_restconf(client, paths, flow, paths.flows) for flow in D1_FLOWS]
    dump_ovs_switch_state(paths.flows / "ovs_after_restconf_flows", RELEVANT_SWITCHES)
    n_ok = sum(1 for row in rows if row.get("status") == "PASS")
    status = "PASS" if n_ok == len(rows) else "CHECK_REQUIRED"
    summary = {
        "campaign_id": paths.campaign_id,
        "scope": "D.3 RESTCONF datastore PUT installation of D.1 h1-h16 exploratory flows",
        "status": status,
        "n_flows": len(rows),
        "n_ok": n_ok,
        "flows": rows,
    }
    write_json(paths.flows / "install_flows_restconf_summary.json", summary)

    print("D.3 install-flows-restconf")
    print("=" * 60)
    print(f"campaign_id: {paths.campaign_id}")
    print("method: RESTCONF datastore PUT")
    print(f"n_flows: {len(rows)}")
    print(f"n_ok: {n_ok}/{len(rows)}")
    for row in rows:
        if row.get("status") != "PASS":
            print(
                f"FAIL {row.get('bridge')} {row.get('flow_id')}: "
                f"http={row.get('http_status')} ovs_present={row.get('ovs_flow_present_after')} "
                f"preview={row.get('response_preview')}"
            )
    print(f"status: {status}")
    print(f"summary: {paths.flows / 'install_flows_restconf_summary.json'}")


def action_d3_prepare_traffic(paths: CampaignPaths) -> None:
    for sub in ["connectivity", "iperf", "after_traffic"]:
        (paths.traffic / sub).mkdir(parents=True, exist_ok=True)

    lines = [
        make_mininet_line("h1", "ip neigh flush all"),
        make_mininet_line("h16", "ip neigh flush all"),
        make_mininet_line("h1", f"ip neigh replace {HOST_IP['h16']} lladdr {HOST_MAC['h16']} nud permanent dev h1-eth1"),
        make_mininet_line("h16", f"ip neigh replace {HOST_IP['h1']} lladdr {HOST_MAC['h1']} nud permanent dev h16-eth1"),
        make_mininet_line("h1", f"ip neigh show | tee {paths.traffic / 'connectivity' / 'h1_static_arp.txt'}"),
        make_mininet_line("h16", f"ip neigh show | tee {paths.traffic / 'connectivity' / 'h16_static_arp.txt'}"),
        make_mininet_line("h1", f"ping -c 10 h16 | tee {paths.traffic / 'connectivity' / 'h1_h16_ping.txt'}"),
        make_mininet_line("h16", "pkill -f iperf3"),
    ]
    for port in range(5821, 5825):
        lines.append(make_mininet_line("h16", f"iperf3 -s -p {port} -D"))
    lines.append("sh sleep 1")
    lines.append(make_mininet_line("h16", f"ss -lnt | grep -E ':5821|:5822|:5823|:5824' | tee {paths.traffic / 'iperf' / 'h16_servers.txt'}"))
    for port in range(5821, 5825):
        out = paths.traffic / "iperf" / f"h1_h16_p{port}.json"
        lines.append(make_mininet_line("h1", f"bash -lc 'iperf3 -c {HOST_IP['h16']} -p {port} -t 8 -J > {out}'"))
    for br in GROUP_SWITCHES:
        lines.append(f"sh sudo ovs-ofctl -O {OPENFLOW13} dump-group-stats {br} | tee {paths.traffic / 'after_traffic' / f'{br}_group_stats.txt'}")
    for br in ["e1", "e8", "a1", "a2", "a7", "a8"]:
        lines.append(f"sh sudo ovs-ofctl -O {OPENFLOW13} dump-flows {br} | tee {paths.traffic / 'after_traffic' / f'{br}_flows.txt'}")

    cli_path = paths.traffic / "mininet_d3_restconf_traffic.cli"
    write_text(cli_path, "\n".join(lines) + "\n")
    print("D.3 prepare-traffic")
    print("=" * 60)
    print(f"campaign_id: {paths.campaign_id}")
    print("Ejecuta dentro de Mininet:")
    print(f"source {cli_path}")


def summarize_traffic_common(paths: CampaignPaths, block_dir: Path, label: str, stress: bool) -> Dict[str, Any]:
    if stress:
        pings = {
            "h1_h16": parse_ping(block_dir / "connectivity" / "h1_h16_ping.txt"),
            "h2_h15": parse_ping(block_dir / "connectivity" / "h2_h15_ping.txt"),
        }
    else:
        pings = {"h1_h16": parse_ping(block_dir / "connectivity" / "h1_h16_ping.txt")}
    iperf_rows, iperf_ok_rows, throughput_sum = summarize_iperf_dir(block_dir / "iperf")
    groups = summarize_groups(block_dir / "after_traffic")
    used_group_switches = [br for br, row in groups.items() if row["group_packets"] > 0]
    multi_bucket_switches = [br for br, row in groups.items() if row["active_buckets"] >= 2]
    aggregation_multi_bucket = [br for br in AGGREGATION_GROUP_SWITCHES if groups[br]["active_buckets"] >= 2]
    all_pings_ok = all(row["ping_ok"] for row in pings.values())
    all_iperf_ok = len(iperf_rows) > 0 and len(iperf_ok_rows) == len(iperf_rows)
    edges_multibucket = groups["e1"]["active_buckets"] >= 2 and groups["e8"]["active_buckets"] >= 2

    if all_pings_ok and all_iperf_ok and edges_multibucket and len(aggregation_multi_bucket) >= 2:
        status = f"PASS_{label}_STRONG"
    elif all_pings_ok and len(iperf_ok_rows) > 0 and edges_multibucket:
        status = f"PARTIAL_{label}"
    elif all_pings_ok and len(iperf_ok_rows) > 0:
        status = f"PASS_TRAFFIC_NO_MULTIBUCKET_{label}"
    else:
        status = "CHECK_REQUIRED"

    return {
        "campaign_id": paths.campaign_id,
        "scope": f"D.3 {label} over RESTCONF-installed exploratory ECMP prototype",
        "status": status,
        "pings": pings,
        "iperf_total": len(iperf_rows),
        "iperf_ok": len(iperf_ok_rows),
        "throughput_sum_gbps": throughput_sum,
        "group_evidence": groups,
        "used_group_switches": used_group_switches,
        "multi_bucket_switches": multi_bucket_switches,
        "aggregation_multi_bucket_switches": aggregation_multi_bucket,
        "interpretation": (
            "This RESTCONF phase checks whether the exploratory datapath prototype can be installed through ODL. "
            "It does not by itself prove complete four-path ECMP if aggregation groups remain single-bucket."
        ),
    }


def action_d3_summarize_traffic(paths: CampaignPaths) -> None:
    summary = summarize_traffic_common(paths, paths.traffic, "RESTCONF_TRAFFIC", stress=False)
    write_json(paths.traffic / "d3_restconf_traffic_summary.json", summary)
    print("D.3 summarize-traffic")
    print("=" * 60)
    print(f"campaign_id: {paths.campaign_id}")
    print(f"status: {summary['status']}")
    print(f"iperf_ok: {summary['iperf_ok']}/{summary['iperf_total']}")
    print(f"throughput_sum_gbps: {summary['throughput_sum_gbps']:.2f}")
    print(f"used_group_switches: {summary['used_group_switches']}")
    print(f"multi_bucket_switches: {summary['multi_bucket_switches']}")
    print(f"aggregation_multi_bucket_switches: {summary['aggregation_multi_bucket_switches']}")
    print(f"summary: {paths.traffic / 'd3_restconf_traffic_summary.json'}")


def action_d3_install_stress_flows_restconf(paths: CampaignPaths) -> None:
    require_sudo_cached()
    client = restconf_client_from_env()
    rows = [install_flow_via_restconf(client, paths, flow, paths.stress_flows) for flow in D2_STRESS_FLOWS]
    dump_ovs_switch_state(paths.stress_flows / "ovs_after_restconf_stress_flows", RELEVANT_SWITCHES)
    n_ok = sum(1 for row in rows if row.get("status") == "PASS")
    status = "PASS" if n_ok == len(rows) else "CHECK_REQUIRED"
    summary = {
        "campaign_id": paths.campaign_id,
        "scope": "D.3 RESTCONF datastore PUT installation of stress h2-h15 flows",
        "status": status,
        "n_flows": len(rows),
        "n_ok": n_ok,
        "flows": rows,
    }
    write_json(paths.stress_flows / "install_stress_flows_restconf_summary.json", summary)

    print("D.3 install-stress-flows-restconf")
    print("=" * 60)
    print(f"campaign_id: {paths.campaign_id}")
    print(f"n_flows: {len(rows)}")
    print(f"n_ok: {n_ok}/{len(rows)}")
    for row in rows:
        if row.get("status") != "PASS":
            print(
                f"FAIL {row.get('bridge')} {row.get('flow_id')}: "
                f"http={row.get('http_status')} ovs_present={row.get('ovs_flow_present_after')}"
            )
    print(f"status: {status}")
    print(f"summary: {paths.stress_flows / 'install_stress_flows_restconf_summary.json'}")


def action_d3_prepare_stress(paths: CampaignPaths) -> None:
    for sub in ["connectivity", "iperf", "after_traffic"]:
        (paths.stress / sub).mkdir(parents=True, exist_ok=True)

    lines = [
        make_mininet_line("h1", "ip neigh flush all"),
        make_mininet_line("h16", "ip neigh flush all"),
        make_mininet_line("h2", "ip neigh flush all"),
        make_mininet_line("h15", "ip neigh flush all"),
        make_mininet_line("h1", f"ip neigh replace {HOST_IP['h16']} lladdr {HOST_MAC['h16']} nud permanent dev h1-eth1"),
        make_mininet_line("h16", f"ip neigh replace {HOST_IP['h1']} lladdr {HOST_MAC['h1']} nud permanent dev h16-eth1"),
        make_mininet_line("h2", f"ip neigh replace {HOST_IP['h15']} lladdr {HOST_MAC['h15']} nud permanent dev h2-eth1"),
        make_mininet_line("h15", f"ip neigh replace {HOST_IP['h2']} lladdr {HOST_MAC['h2']} nud permanent dev h15-eth1"),
        make_mininet_line("h1", f"ping -c 10 h16 | tee {paths.stress / 'connectivity' / 'h1_h16_ping.txt'}"),
        make_mininet_line("h2", f"ping -c 10 h15 | tee {paths.stress / 'connectivity' / 'h2_h15_ping.txt'}"),
        make_mininet_line("h16", "pkill -f iperf3"),
        make_mininet_line("h15", "pkill -f iperf3"),
    ]
    for port in range(5830, 5838):
        lines.append(make_mininet_line("h16", f"iperf3 -s -p {port} -D"))
    for port in range(5930, 5938):
        lines.append(make_mininet_line("h15", f"iperf3 -s -p {port} -D"))
    lines.append("sh sleep 1")
    lines.append(make_mininet_line("h16", f"ss -lnt | grep -E ':5830|:5831|:5832|:5833|:5834|:5835|:5836|:5837' | tee {paths.stress / 'iperf' / 'h16_servers.txt'}"))
    lines.append(make_mininet_line("h15", f"ss -lnt | grep -E ':5930|:5931|:5932|:5933|:5934|:5935|:5936|:5937' | tee {paths.stress / 'iperf' / 'h15_servers.txt'}"))
    for port in range(5830, 5838):
        out = paths.stress / "iperf" / f"h1_h16_p{port}.json"
        lines.append(make_mininet_line("h1", f"bash -lc 'iperf3 -c {HOST_IP['h16']} -p {port} -t 6 -J > {out}'"))
    for port in range(5930, 5938):
        out = paths.stress / "iperf" / f"h2_h15_p{port}.json"
        lines.append(make_mininet_line("h2", f"bash -lc 'iperf3 -c {HOST_IP['h15']} -p {port} -t 6 -J > {out}'"))
    for br in GROUP_SWITCHES:
        lines.append(f"sh sudo ovs-ofctl -O {OPENFLOW13} dump-group-stats {br} | tee {paths.stress / 'after_traffic' / f'{br}_group_stats.txt'}")
    for br in ["e1", "e8", "a1", "a2", "a7", "a8"]:
        lines.append(f"sh sudo ovs-ofctl -O {OPENFLOW13} dump-flows {br} | tee {paths.stress / 'after_traffic' / f'{br}_flows.txt'}")

    cli_path = paths.stress / "mininet_d3_restconf_stress.cli"
    write_text(cli_path, "\n".join(lines) + "\n")
    print("D.3 prepare-stress")
    print("=" * 60)
    print(f"campaign_id: {paths.campaign_id}")
    print("Ejecuta dentro de Mininet:")
    print(f"source {cli_path}")


def action_d3_summarize_stress(paths: CampaignPaths) -> None:
    summary = summarize_traffic_common(paths, paths.stress, "RESTCONF_STRESS", stress=True)
    write_json(paths.stress / "d3_restconf_stress_summary.json", summary)
    print("D.3 summarize-stress")
    print("=" * 60)
    print(f"campaign_id: {paths.campaign_id}")
    print(f"status: {summary['status']}")
    print(f"iperf_ok: {summary['iperf_ok']}/{summary['iperf_total']}")
    print(f"throughput_sum_gbps: {summary['throughput_sum_gbps']:.2f}")
    print(f"used_group_switches: {summary['used_group_switches']}")
    print(f"multi_bucket_switches: {summary['multi_bucket_switches']}")
    print(f"aggregation_multi_bucket_switches: {summary['aggregation_multi_bucket_switches']}")
    print(f"summary: {paths.stress / 'd3_restconf_stress_summary.json'}")


def action_status(paths: CampaignPaths) -> None:
    files = {
        "cleanup": paths.cleanup / "cleanup_ovs_summary.json",
        "groups_install": paths.groups / "install_groups_restconf_summary.json",
        "groups_verify": paths.groups / "verify_groups_summary.json",
        "flows_install": paths.flows / "install_flows_restconf_summary.json",
        "traffic": paths.traffic / "d3_restconf_traffic_summary.json",
        "stress_flows_install": paths.stress_flows / "install_stress_flows_restconf_summary.json",
        "stress": paths.stress / "d3_restconf_stress_summary.json",
    }
    print("D.3 status")
    print("=" * 60)
    print(f"campaign_id: {paths.campaign_id}")
    for name, path in files.items():
        if path.exists():
            try:
                data = json.loads(read_text(path))
                print(f"{name}: {data.get('status')} ({path})")
            except json.JSONDecodeError:
                print(f"{name}: UNREADABLE ({path})")
        else:
            print(f"{name}: MISSING ({path})")


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Capítulo 8 — Fase D.3 RESTCONF exploratory migration.")
    parser.add_argument("--tfg-root", default=str(default_tfg_root()))
    parser.add_argument("--campaign-id", default=None)
    parser.add_argument(
        "--action",
        required=True,
        choices=[
            "d3-cleanup-ovs",
            "d3-install-groups-restconf",
            "d3-verify-groups",
            "d3-install-flows-restconf",
            "d3-prepare-traffic",
            "d3-summarize-traffic",
            "d3-install-stress-flows-restconf",
            "d3-prepare-stress",
            "d3-summarize-stress",
            "status",
        ],
    )
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    tfg_root = Path(args.tfg_root).expanduser().resolve()
    campaign_id = resolve_campaign_id(tfg_root, args.campaign_id, args.action)
    paths = CampaignPaths(tfg_root=tfg_root, campaign_id=campaign_id)
    paths.ensure_base()

    try:
        if args.action == "d3-cleanup-ovs":
            action_d3_cleanup_ovs(paths)
        elif args.action == "d3-install-groups-restconf":
            action_d3_install_groups_restconf(paths)
        elif args.action == "d3-verify-groups":
            action_d3_verify_groups(paths)
        elif args.action == "d3-install-flows-restconf":
            action_d3_install_flows_restconf(paths)
        elif args.action == "d3-prepare-traffic":
            action_d3_prepare_traffic(paths)
        elif args.action == "d3-summarize-traffic":
            action_d3_summarize_traffic(paths)
        elif args.action == "d3-install-stress-flows-restconf":
            action_d3_install_stress_flows_restconf(paths)
        elif args.action == "d3-prepare-stress":
            action_d3_prepare_stress(paths)
        elif args.action == "d3-summarize-stress":
            action_d3_summarize_stress(paths)
        elif args.action == "status":
            action_status(paths)
        else:
            raise ValueError(args.action)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

