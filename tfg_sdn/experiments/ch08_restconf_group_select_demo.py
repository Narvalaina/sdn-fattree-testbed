#!/usr/bin/env python3
"""
Fase C.2 — Instalación RESTCONF de grupos OpenFlow select y forwarding ECMP limitado.

Ruta esperada:
    tfg_sdn/experiments/ch08_restconf_group_select_demo.py

Objetivo:
    Intentar reproducir el demostrador ECMP limitado de C.1, pero instalando
    los grupos OpenFlow select y los flows asociados mediante OpenDaylight
    RESTCONF/RPC, no mediante ovs-ofctl como mecanismo de programación principal.

Alcance metodológico:
    - OVS/ovs-ofctl se usa para limpieza previa y verificación/captura de evidencias.
    - La instalación de grupos se realiza mediante RESTCONF/RPC sal-group:add-group.
    - La instalación de flows se realiza mediante RESTCONF/RPC sal-flow:add-flow.
    - No demuestra ECMP completo en toda la Fat-Tree.
    - No demuestra balanceo dinámico ni reconfiguración automática.

Acciones principales:
    --action probe-existing
    --action cleanup-ovs
    --action cleanup-forwarding-ovs
    --action install-groups-restconf
    --action verify-groups
    --action install-flows-restconf
    --action prepare-traffic
    --action summarize-traffic

Uso recomendado para C.2.2:
    cd /path/to/sdn-fattree-testbed
    export TFG_ROOT="$PWD"
    export PYTHONPATH="$TFG_ROOT:$PYTHONPATH"
    export ODL_HOST="127.0.0.1"
    export ODL_REST_PORT="8181"
    export ODL_USER="admin"
    export ODL_PASS="<set-locally>"
    sudo -v

    # Puedes reutilizar el CAMPAIGN_ID de C.2.1:
    # export CAMPAIGN_ID="ch08_fattree_ecmp_restconf_groups_20260520_1009"

    python3 tfg_sdn/experiments/ch08_restconf_group_select_demo.py \
      --campaign-id "$CAMPAIGN_ID" --action cleanup-forwarding-ovs

    python3 tfg_sdn/experiments/ch08_restconf_group_select_demo.py \
      --campaign-id "$CAMPAIGN_ID" --action install-groups-restconf

    python3 tfg_sdn/experiments/ch08_restconf_group_select_demo.py \
      --campaign-id "$CAMPAIGN_ID" --action verify-groups

    python3 tfg_sdn/experiments/ch08_restconf_group_select_demo.py \
      --campaign-id "$CAMPAIGN_ID" --action install-flows-restconf

    python3 tfg_sdn/experiments/ch08_restconf_group_select_demo.py \
      --campaign-id "$CAMPAIGN_ID" --action prepare-traffic

    # Ejecutar dentro de Mininet el source indicado.

    python3 tfg_sdn/experiments/ch08_restconf_group_select_demo.py \
      --campaign-id "$CAMPAIGN_ID" --action summarize-traffic
"""

from __future__ import annotations

import argparse
import base64
import csv
import json
import math
import os
import re
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


CAMPAIGN_PREFIX = "ch08_fattree_ecmp_restconf_groups_"
GROUP_ID = 100
OPENFLOW13 = "OpenFlow13"

H1_IP = "10.0.0.1"
H16_IP = "10.0.0.16"
H1_MAC = "00:00:00:00:00:01"
H16_MAC = "00:00:00:00:00:10"

IPERF_PORT = 5701
IPERF_PARALLEL = 8
IPERF_SECONDS = 20
N_REPETITIONS = 3

# En la topología Fat-Tree k=4:
# e1-eth3 -> a1, e1-eth4 -> a2
# e8-eth3 -> a7, e8-eth4 -> a8
GROUP_TARGETS = {
    "e1": {
        "description": "forward h1->h16 branching from e1 to a1/a2",
        "node_id_expected": "openflow:13",
        "buckets": [3, 4],
    },
    "e8": {
        "description": "reverse h16->h1 branching from e8 to a7/a8",
        "node_id_expected": "openflow:20",
        "buckets": [3, 4],
    },
}

GROUP_SWITCHES = ["e1", "e8"]
DOWNSTREAM_SWITCHES = ["a1", "a2", "c1", "c3", "a7", "a8"]
ALL_RELEVANT_SWITCHES = ["e1", "e8", "a1", "a2", "c1", "c3", "a7", "a8"]
RELEVANT_SWITCHES = GROUP_SWITCHES

# Flows de forwarding para ECMP limitado h1 <-> h16.
# e1/e8 instalan acciones group:100 para la bifurcación; el resto son rutas deterministas.
RESTCONF_FORWARDING_FLOWS: List[Dict[str, Any]] = [
    {"bridge": "e1", "flow_id": "c2r_e1_to_h16_group", "dst_mac": H16_MAC, "action": "group", "group_id": GROUP_ID, "priority": 260},
    {"bridge": "e1", "flow_id": "c2r_e1_to_h1_local", "dst_mac": H1_MAC, "action": "output", "out_port": 1, "priority": 250},
    {"bridge": "e8", "flow_id": "c2r_e8_to_h1_group", "dst_mac": H1_MAC, "action": "group", "group_id": GROUP_ID, "priority": 260},
    {"bridge": "e8", "flow_id": "c2r_e8_to_h16_local", "dst_mac": H16_MAC, "action": "output", "out_port": 2, "priority": 250},
    {"bridge": "a1", "flow_id": "c2r_a1_to_h16", "dst_mac": H16_MAC, "action": "output", "out_port": 3, "priority": 250},
    {"bridge": "a1", "flow_id": "c2r_a1_to_h1", "dst_mac": H1_MAC, "action": "output", "out_port": 1, "priority": 250},
    {"bridge": "a2", "flow_id": "c2r_a2_to_h16", "dst_mac": H16_MAC, "action": "output", "out_port": 3, "priority": 250},
    {"bridge": "a2", "flow_id": "c2r_a2_to_h1", "dst_mac": H1_MAC, "action": "output", "out_port": 1, "priority": 250},
    {"bridge": "c1", "flow_id": "c2r_c1_to_h16", "dst_mac": H16_MAC, "action": "output", "out_port": 4, "priority": 250},
    {"bridge": "c1", "flow_id": "c2r_c1_to_h1", "dst_mac": H1_MAC, "action": "output", "out_port": 1, "priority": 250},
    {"bridge": "c3", "flow_id": "c2r_c3_to_h16", "dst_mac": H16_MAC, "action": "output", "out_port": 4, "priority": 250},
    {"bridge": "c3", "flow_id": "c2r_c3_to_h1", "dst_mac": H1_MAC, "action": "output", "out_port": 1, "priority": 250},
    {"bridge": "a7", "flow_id": "c2r_a7_to_h16", "dst_mac": H16_MAC, "action": "output", "out_port": 2, "priority": 250},
    {"bridge": "a7", "flow_id": "c2r_a7_to_h1", "dst_mac": H1_MAC, "action": "output", "out_port": 3, "priority": 250},
    {"bridge": "a8", "flow_id": "c2r_a8_to_h16", "dst_mac": H16_MAC, "action": "output", "out_port": 2, "priority": 250},
    {"bridge": "a8", "flow_id": "c2r_a8_to_h1", "dst_mac": H1_MAC, "action": "output", "out_port": 3, "priority": 250},
]

BRANCHES = {
    "forward_1": {"switch": "a1", "dst_mac": H16_MAC, "description": "h1->h16 via e1-a1-c1-a7-e8"},
    "forward_2": {"switch": "a2", "dst_mac": H16_MAC, "description": "h1->h16 via e1-a2-c3-a8-e8"},
    "reverse_1": {"switch": "a7", "dst_mac": H1_MAC, "description": "h16->h1 via e8-a7-c1-a1-e1"},
    "reverse_2": {"switch": "a8", "dst_mac": H1_MAC, "description": "h16->h1 via e8-a8-c3-a2-e1"},
}


# ---------------------------------------------------------------------------
# Utilidades generales
# ---------------------------------------------------------------------------


def default_tfg_root() -> Path:
    return Path(os.environ.get("TFG_ROOT", str(Path(__file__).resolve().parents[2]))).expanduser()


def now_id() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M")


def find_latest_campaign(tfg_root: Path) -> Optional[str]:
    base = tfg_root / "runs" / "ch08_fattree"
    if not base.exists():
        return None
    candidates = sorted([p.name for p in base.iterdir() if p.is_dir() and p.name.startswith(CAMPAIGN_PREFIX)], reverse=True)
    return candidates[0] if candidates else None


def resolve_campaign_id(tfg_root: Path, campaign_id: Optional[str], action: str) -> str:
    if campaign_id:
        return campaign_id
    latest = find_latest_campaign(tfg_root)
    if latest and action not in {"probe-existing"}:
        return latest
    return CAMPAIGN_PREFIX + now_id()


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


def read_json(path: Path) -> Optional[Dict[str, Any]]:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except json.JSONDecodeError:
        return None


def write_csv(path: Path, rows: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    keys: List[str] = []
    for row in rows:
        for key in row.keys():
            if key not in keys:
                keys.append(key)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=keys)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in keys})


def as_float(value: Any) -> Optional[float]:
    if value in (None, "", "None", "nan", "NaN"):
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(v) or math.isinf(v):
        return None
    return v


def mean(values: Iterable[Any]) -> Optional[float]:
    nums = [v for v in (as_float(x) for x in values) if v is not None]
    return statistics.mean(nums) if nums else None


def fmt_num(value: Any, decimals: int = 2) -> str:
    v = as_float(value)
    return "--" if v is None else f"{v:.{decimals}f}"


@dataclass
class CampaignPaths:
    tfg_root: Path
    campaign_id: str

    @property
    def root(self) -> Path:
        return self.tfg_root / "runs" / "ch08_fattree" / self.campaign_id

    @property
    def probe(self) -> Path:
        return self.root / "c2_probe_existing_groups"

    @property
    def cleanup(self) -> Path:
        return self.root / "c2_cleanup_ovs"

    @property
    def cleanup_full(self) -> Path:
        return self.root / "c2_2_cleanup_forwarding_ovs"

    @property
    def install(self) -> Path:
        return self.root / "c2_install_groups_restconf"

    @property
    def verify(self) -> Path:
        return self.root / "c2_verify_groups"

    @property
    def flows(self) -> Path:
        return self.root / "c2_2_install_flows_restconf"

    @property
    def traffic(self) -> Path:
        return self.root / "c2_2_traffic_restconf"

    @property
    def processed(self) -> Path:
        return self.tfg_root / "data" / "processed" / "ch08_fattree" / self.campaign_id

    def ensure(self) -> None:
        for path in [self.root, self.probe, self.cleanup, self.cleanup_full, self.install, self.verify, self.flows, self.traffic, self.processed]:
            path.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Ejecución local / OVS
# ---------------------------------------------------------------------------


def run_cmd(cmd: Sequence[str], check: bool = True, output_path: Optional[Path] = None) -> subprocess.CompletedProcess:
    proc = subprocess.run(list(cmd), text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("$ " + " ".join(cmd) + "\n\n" + proc.stdout + ("\n[stderr]\n" + proc.stderr if proc.stderr else ""), encoding="utf-8")
    if check and proc.returncode != 0:
        raise RuntimeError(f"[FAIL] Comando fallido ({proc.returncode}): {' '.join(cmd)}\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}")
    return proc


def sudo_cmd(args: Sequence[str], check: bool = True, output_path: Optional[Path] = None) -> subprocess.CompletedProcess:
    return run_cmd(["sudo", "-n", *args], check=check, output_path=output_path)


def require_sudo_cached() -> None:
    proc = subprocess.run(["sudo", "-n", "true"], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        raise SystemExit("[FAIL] sudo no está cacheado. Ejecuta primero:\n\n    sudo -v\n\ny repite el comando.")


def ovs_bridge_exists(bridge: str) -> bool:
    return sudo_cmd(["ovs-vsctl", "br-exists", bridge], check=False).returncode == 0


def bridge_to_node_id(bridge: str) -> str:
    proc = sudo_cmd(["ovs-vsctl", "get", "Bridge", bridge, "datapath_id"])
    dpid = proc.stdout.strip().strip('"')
    return f"openflow:{int(dpid, 16)}"


def capture_ovs_state(out_dir: Path, bridges: Sequence[str]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    sudo_cmd(["ovs-vsctl", "show"], check=False, output_path=out_dir / "ovs_vsctl_show.txt")
    for br in bridges:
        sudo_cmd(["ovs-ofctl", "-O", OPENFLOW13, "dump-flows", br], check=False, output_path=out_dir / f"{br}_flows.txt")
        sudo_cmd(["ovs-ofctl", "-O", OPENFLOW13, "dump-groups", br], check=False, output_path=out_dir / f"{br}_groups.txt")
        sudo_cmd(["ovs-ofctl", "-O", OPENFLOW13, "dump-group-stats", br], check=False, output_path=out_dir / f"{br}_group_stats.txt")


def capture_ovs_group_state(out_dir: Path, bridges: Sequence[str] = RELEVANT_SWITCHES) -> None:
    capture_ovs_state(out_dir, bridges)


def ovs_group_present(bridge: str) -> bool:
    proc = sudo_cmd(["ovs-ofctl", "-O", OPENFLOW13, "dump-groups", bridge], check=False)
    return proc.returncode == 0 and f"group_id={GROUP_ID}" in proc.stdout and "type=select" in proc.stdout


def ovs_flow_present(bridge: str, dst_mac: str, expected_action: str) -> bool:
    proc = sudo_cmd(["ovs-ofctl", "-O", OPENFLOW13, "dump-flows", bridge], check=False)
    if proc.returncode != 0:
        return False
    for line in proc.stdout.splitlines():
        if f"dl_dst={dst_mac}" not in line:
            continue
        if expected_action == "group" and f"group:{GROUP_ID}" in line:
            return True
        if expected_action.startswith("output:") and expected_action in line:
            return True
    return False


def cleanup_bridges(paths: CampaignPaths, bridges: Sequence[str], out_dir: Path, delete_groups: bool) -> Dict[str, Any]:
    require_sudo_cached()
    result: Dict[str, Any] = {"bridges": {}, "status": "PASS", "delete_groups": delete_groups}
    for br in bridges:
        if not ovs_bridge_exists(br):
            result["bridges"][br] = {"exists": False, "status": "MISSING"}
            result["status"] = "CHECK_REQUIRED"
            continue
        sudo_cmd(["ovs-ofctl", "-O", OPENFLOW13, "del-flows", br], check=False)
        if delete_groups:
            sudo_cmd(["ovs-ofctl", "-O", OPENFLOW13, "del-groups", br], check=False)
        result["bridges"][br] = {"exists": True, "status": "CLEANED"}
    capture_ovs_state(out_dir / "ovs_after_cleanup", bridges)
    return result


def cleanup_ovs_groups_and_flows(paths: CampaignPaths) -> Dict[str, Any]:
    return cleanup_bridges(paths, RELEVANT_SWITCHES, paths.cleanup, delete_groups=True)


# ---------------------------------------------------------------------------
# RESTCONF
# ---------------------------------------------------------------------------


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

    def request(self, method: str, path: str, body: Optional[bytes] = None, content_type: Optional[str] = None, accept: str = "application/json") -> Tuple[int, str, Dict[str, str]]:
        headers = {"Authorization": self._auth_header(), "Accept": accept}
        if content_type:
            headers["Content-Type"] = content_type
        req = urllib.request.Request(self.base_url + path, data=body, headers=headers, method=method.upper())
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return resp.status, resp.read().decode("utf-8", errors="replace"), dict(resp.headers.items())
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode("utf-8", errors="replace"), dict(exc.headers.items())
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
    return f"{node_ref(node_id)}/flow-node-inventory:group[flow-node-inventory:group-id='{GROUP_ID}']"


def flow_ref(node_id: str, flow_id: str) -> str:
    return f"{node_ref(node_id)}/flow-node-inventory:table[flow-node-inventory:id='0']/flow-node-inventory:flow[flow-node-inventory:id='{flow_id}']"


def xml_escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def make_group_rpc_xml_input_only(node_id: str, bridge: str, buckets: Sequence[int]) -> str:
    bucket_xml = []
    for idx, port in enumerate(buckets):
        bucket_xml.append(f"""
        <bucket>
            <bucket-id>{idx}</bucket-id>
            <weight>1</weight>
            <watch_port>4294967295</watch_port>
            <watch_group>4294967295</watch_group>
            <action>
                <order>0</order>
                <output-action>
                    <output-node-connector>{port}</output-node-connector>
                    <max-length>65535</max-length>
                </output-action>
            </action>
        </bucket>""")
    return f"""<input xmlns="urn:opendaylight:group:service">
    <node>{xml_escape(node_ref(node_id))}</node>
    <group-ref>{xml_escape(group_ref(node_id))}</group-ref>
    <group-id>{GROUP_ID}</group-id>
    <group-name>c2_restconf_select_{bridge}</group-name>
    <group-type>group-select</group-type>
    <barrier>false</barrier>
    <buckets>{''.join(bucket_xml)}</buckets>
</input>"""


def make_group_rpc_xml_wrapped(node_id: str, bridge: str, buckets: Sequence[int]) -> str:
    return f"<add-group xmlns=\"urn:opendaylight:group:service\">{make_group_rpc_xml_input_only(node_id, bridge, buckets)}</add-group>"


def make_group_rpc_json(node_id: str, bridge: str, buckets: Sequence[int]) -> Dict[str, Any]:
    bucket_items = []
    for idx, port in enumerate(buckets):
        bucket_items.append({
            "bucket-id": idx,
            "weight": 1,
            "watch_port": 4294967295,
            "watch_group": 4294967295,
            "action": [{"order": 0, "output-action": {"output-node-connector": str(port), "max-length": 65535}}],
        })
    return {"input": {
        "node": node_ref(node_id),
        "group-ref": group_ref(node_id),
        "group-id": GROUP_ID,
        "group-name": f"c2_restconf_select_{bridge}",
        "group-type": "group-select",
        "barrier": False,
        "buckets": {"bucket": bucket_items},
    }}


def make_group_datastore_json(bridge: str, buckets: Sequence[int]) -> Dict[str, Any]:
    bucket_items = []
    for idx, port in enumerate(buckets, start=1):
        bucket_items.append(
            {
                "bucket-id": idx,
                "weight": 1,
                "action": [
                    {
                        "order": 1,
                        "output-action": {"output-node-connector": str(port)},
                    }
                ],
            }
        )
    return {
        "flow-node-inventory:group": [
            {
                "group-id": GROUP_ID,
                "barrier": False,
                "group-name": f"c2_restconf_select_{bridge}",
                "buckets": {"bucket": bucket_items},
                "group-type": "group-select",
            }
        ]
    }


def group_datastore_paths(node_id: str) -> List[Tuple[str, str]]:
    return [
        ("plain_group", f"/rests/data/opendaylight-inventory:nodes/node={node_id}/group={GROUP_ID}"),
        ("ns_group", f"/rests/data/opendaylight-inventory:nodes/node={node_id}/flow-node-inventory:group={GROUP_ID}"),
    ]


def put_group_datastore_with_variants(
    client: RestconfClient,
    bridge: str,
    node_id: str,
    buckets: Sequence[int],
    out_dir: Path,
) -> Dict[str, Any]:
    attempts: List[Dict[str, Any]] = []
    payload = make_group_datastore_json(bridge, buckets)
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    for path_name, endpoint in group_datastore_paths(node_id):
        variant_dir = out_dir / bridge / path_name
        variant_dir.mkdir(parents=True, exist_ok=True)
        write_text(variant_dir / "datastore_group_put_request.json", body.decode("utf-8"))
        status, response, headers = client.request(
            "PUT",
            endpoint,
            body=body,
            content_type="application/json",
            accept="application/json",
        )
        write_text(variant_dir / "datastore_group_put_response.txt", response)
        write_json(variant_dir / "datastore_group_put_meta.json", {"http_status": status, "headers": headers, "endpoint": endpoint})
        time.sleep(1.0)
        present = ovs_group_present(bridge)
        attempt = {
            "variant": path_name,
            "endpoint": endpoint,
            "http_status": status,
            "restconf_success_status": status in {200, 201, 204},
            "ovs_group_present_after": present,
            "response_preview": response[:300],
        }
        attempts.append(attempt)
        if attempt["restconf_success_status"] and present:
            return {"status": "PASS", "winning_variant": path_name, "attempts": attempts}

    return {"status": "FAIL", "winning_variant": None, "attempts": attempts}


def ethernet_match(dst_mac: Optional[str] = None, eth_type: Optional[int] = None) -> Dict[str, Any]:
    match: Dict[str, Any] = {}
    eth_match: Dict[str, Any] = {}
    if dst_mac:
        eth_match["ethernet-destination"] = {"address": dst_mac}
    if eth_type is not None:
        eth_match["ethernet-type"] = {"type": eth_type}
    if eth_match:
        match["ethernet-match"] = eth_match
    return match


def action_list_for_flow(
    flow: Dict[str, Any],
    group_action_variant: str = "group_id_int",
    action_order: int = 0,
) -> List[Dict[str, Any]]:
    if flow["action"] == "output":
        return [
            {
                "order": 0,
                "output-action": {
                    "output-node-connector": str(flow["out_port"]),
                    "max-length": 65535,
                },
            }
        ]

    if flow["action"] == "group":
        gid = int(flow["group_id"])
        if group_action_variant == "group_id_int":
            group_action: Dict[str, Any] = {"group-id": gid}
        elif group_action_variant == "group_id_str":
            group_action = {"group-id": str(gid)}
        elif group_action_variant == "group_with_id":
            group_action = {"group": str(gid), "group-id": gid}
        elif group_action_variant == "group_only":
            group_action = {"group": str(gid)}
        else:
            raise ValueError(f"Variante group-action no soportada: {group_action_variant}")
        return [{"order": action_order, "group-action": group_action}]

    raise ValueError(f"Acción no soportada: {flow['action']}")


def make_flow_rpc_json(node_id: str, flow: Dict[str, Any], variant: str) -> Dict[str, Any]:
    flow_id = flow["flow_id"]
    if flow.get("kind") == "arp_drop":
        match = ethernet_match(eth_type=2054)
        instructions: Dict[str, Any] = {"instruction": []}
    elif flow.get("kind") == "table_miss":
        match = {}
        instructions = {"instruction": []}
    else:
        match = ethernet_match(dst_mac=flow["dst_mac"])
        instructions = {"instruction": [{"order": 0, "apply-actions": {"action": action_list_for_flow(flow)}}]}

    table_key = "table_id" if variant == "underscore" else "table-id"
    return {"input": {
        "node": node_ref(node_id),
        "flow-ref": flow_ref(node_id, flow_id),
        "id": flow_id,
        "flow-name": flow_id,
        table_key: 0,
        "priority": int(flow.get("priority", 250)),
        "barrier": False,
        "strict": False,
        "installHw": False,
        "match": match,
        "instructions": instructions,
    }}


def make_flow_datastore_json(
    flow: Dict[str, Any],
    table_key: str = "table_id",
    wrapper: str = "flow-node-inventory:flow",
    group_action_variant: str = "group_id_int",
    action_order: int = 0,
) -> Dict[str, Any]:
    """Construye payload RESTCONF datastore para instalar un flow en ODL."""
    flow_id = flow["flow_id"]

    match = ethernet_match(dst_mac=flow["dst_mac"])
    instructions = {
        "instruction": [
            {
                "order": 0,
                "apply-actions": {
                    "action": action_list_for_flow(
                        flow,
                        group_action_variant=group_action_variant,
                        action_order=action_order,
                    )
                },
            }
        ]
    }

    flow_obj: Dict[str, Any] = {
        "id": flow_id,
        "flow-name": flow_id,
        table_key: 0,
        "priority": int(flow.get("priority", 250)),
        "hard-timeout": 0,
        "idle-timeout": 0,
        "barrier": False,
        "strict": False,
        "installHw": False,
        "match": match,
        "instructions": instructions,
    }

    return {wrapper: [flow_obj]}


def make_flow_datastore_xml(flow: Dict[str, Any], action_order: int = 1) -> str:
    flow_id = flow["flow_id"]
    dst_mac = flow["dst_mac"]
    priority = int(flow.get("priority", 250))

    if flow["action"] == "group":
        action_xml = f"""<group-action><group-id>{int(flow['group_id'])}</group-id></group-action>"""
    else:
        action_xml = (
            f"<output-action><output-node-connector>{int(flow['out_port'])}</output-node-connector>"
            f"<max-length>65535</max-length></output-action>"
        )

    return f"""<flow xmlns="urn:opendaylight:flow:inventory">
    <id>{xml_escape(flow_id)}</id>
    <flow-name>{xml_escape(flow_id)}</flow-name>
    <table_id>0</table_id>
    <priority>{priority}</priority>
    <hard-timeout>0</hard-timeout>
    <idle-timeout>0</idle-timeout>
    <barrier>false</barrier>
    <strict>false</strict>
    <installHw>false</installHw>
    <match>
        <ethernet-match>
            <ethernet-destination>
                <address>{dst_mac}</address>
            </ethernet-destination>
        </ethernet-match>
    </match>
    <instructions>
        <instruction>
            <order>0</order>
            <apply-actions>
                <action>
                    {action_xml}
                    <order>{action_order}</order>
                </action>
            </apply-actions>
        </instruction>
    </instructions>
</flow>"""


def flow_datastore_path(node_id: str, flow_id: str) -> str:
    return (
        f"/rests/data/opendaylight-inventory:nodes/node={node_id}"
        f"/flow-node-inventory:table=0/flow={flow_id}"
    )


def expected_ovs_action(flow: Dict[str, Any]) -> str:
    if flow["action"] == "group":
        return "group"
    return f"output:{flow['out_port']}"


def verify_flow_in_ovs(bridge: str, flow: Dict[str, Any]) -> bool:
    return ovs_flow_present(bridge, flow["dst_mac"], expected_ovs_action(flow))


def put_flow_datastore_with_variants(
    client: RestconfClient,
    bridge: str,
    node_id: str,
    flow: Dict[str, Any],
    out_dir: Path,
) -> Dict[str, Any]:
    """Instala un flow por RESTCONF datastore PUT probando variantes compatibles.

    En ODL Titanium, los flows con output-action entran bien con JSON datastore.
    Para flows con group-action se prueban además variantes de group-action y
    una variante XML, porque la documentación oficial muestra el group-action
    en XML con <order>1</order>.
    """
    flow_id = flow["flow_id"]
    endpoint = flow_datastore_path(node_id, flow_id)

    json_variants: List[Tuple[str, str, str, str, int]] = []
    if flow["action"] == "group":
        for group_variant in ["group_id_int", "group_id_str", "group_with_id", "group_only"]:
            for action_order in [0, 1]:
                json_variants.append((f"json_ns_{group_variant}_order{action_order}", "table_id", "flow-node-inventory:flow", group_variant, action_order))
                json_variants.append((f"json_plain_{group_variant}_order{action_order}", "table_id", "flow", group_variant, action_order))
    else:
        json_variants = [
            ("json_ns_table_id", "table_id", "flow-node-inventory:flow", "group_id_int", 0),
            ("json_plain_table_id", "table_id", "flow", "group_id_int", 0),
        ]

    attempts: List[Dict[str, Any]] = []

    for name, table_key, wrapper, group_action_variant, action_order in json_variants:
        variant_dir = out_dir / bridge / flow_id / name
        payload = make_flow_datastore_json(
            flow,
            table_key=table_key,
            wrapper=wrapper,
            group_action_variant=group_action_variant,
            action_order=action_order,
        )
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        write_text(variant_dir / "datastore_put_request.json", body.decode("utf-8"))

        status, response, headers = client.request(
            "PUT",
            endpoint,
            body=body,
            content_type="application/json",
            accept="application/json",
        )
        write_text(variant_dir / "datastore_put_response.txt", response)
        write_json(variant_dir / "datastore_put_meta.json", {"http_status": status, "headers": headers, "endpoint": endpoint})

        time.sleep(1.0)
        ovs_ok = verify_flow_in_ovs(bridge, flow)
        attempt = {
            "variant": name,
            "http_status": status,
            "restconf_success_status": status in {200, 201, 204},
            "ovs_flow_present_after": ovs_ok,
            "response_preview": response[:220],
        }
        attempts.append(attempt)

        if attempt["restconf_success_status"] and ovs_ok:
            return {"status": "PASS", "winning_variant": name, "attempts": attempts}

        if attempt["restconf_success_status"]:
            time.sleep(2.0)
            ovs_ok_late = verify_flow_in_ovs(bridge, flow)
            attempt["ovs_flow_present_after_second_wait"] = ovs_ok_late
            if ovs_ok_late:
                return {"status": "PASS", "winning_variant": name, "attempts": attempts}

    if flow["action"] == "group":
        for action_order in [1, 0]:
            name = f"xml_group_order{action_order}"
            variant_dir = out_dir / bridge / flow_id / name
            variant_dir.mkdir(parents=True, exist_ok=True)
            xml_payload = make_flow_datastore_xml(flow, action_order=action_order).encode("utf-8")
            write_text(variant_dir / "datastore_put_request.xml", xml_payload.decode("utf-8"))
            status, response, headers = client.request(
                "PUT",
                endpoint,
                body=xml_payload,
                content_type="application/xml",
                accept="application/xml",
            )
            write_text(variant_dir / "datastore_put_response.xml", response)
            write_json(variant_dir / "datastore_put_meta.json", {"http_status": status, "headers": headers, "endpoint": endpoint})
            time.sleep(1.0)
            ovs_ok = verify_flow_in_ovs(bridge, flow)
            attempt = {
                "variant": name,
                "http_status": status,
                "restconf_success_status": status in {200, 201, 204},
                "ovs_flow_present_after": ovs_ok,
                "response_preview": response[:220],
            }
            attempts.append(attempt)
            if attempt["restconf_success_status"] and ovs_ok:
                return {"status": "PASS", "winning_variant": name, "attempts": attempts}
            if attempt["restconf_success_status"]:
                time.sleep(2.0)
                ovs_ok_late = verify_flow_in_ovs(bridge, flow)
                attempt["ovs_flow_present_after_second_wait"] = ovs_ok_late
                if ovs_ok_late:
                    return {"status": "PASS", "winning_variant": name, "attempts": attempts}

    return {"status": "FAIL", "winning_variant": None, "attempts": attempts}


def post_json_rpc(client: RestconfClient, endpoint: str, payload: Dict[str, Any], out_dir: Path, name: str) -> Tuple[int, str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    write_text(out_dir / f"{name}_request.json", body.decode("utf-8"))
    status, response, headers = client.request("POST", endpoint, body=body, content_type="application/json", accept="application/json")
    write_text(out_dir / f"{name}_response.txt", response)
    write_json(out_dir / f"{name}_meta.json", {"http_status": status, "headers": headers})
    return status, response


def post_group_with_variants(client: RestconfClient, bridge: str, node_id: str, buckets: Sequence[int], out_dir: Path) -> Dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    endpoint = "/rests/operations/sal-group:add-group"
    attempts: List[Dict[str, Any]] = []
    variants: List[Tuple[str, bytes, str, str]] = [
        ("xml_input_only", make_group_rpc_xml_input_only(node_id, bridge, buckets).encode("utf-8"), "application/xml", "application/xml"),
        ("xml_wrapped_add_group", make_group_rpc_xml_wrapped(node_id, bridge, buckets).encode("utf-8"), "application/xml", "application/xml"),
        ("json_input", json.dumps(make_group_rpc_json(node_id, bridge, buckets), ensure_ascii=False).encode("utf-8"), "application/json", "application/json"),
    ]
    for name, body, content_type, accept in variants:
        variant_dir = out_dir / bridge / name
        variant_dir.mkdir(parents=True, exist_ok=True)
        write_text(variant_dir / "request_body.txt", body.decode("utf-8", errors="replace"))
        status, response, headers = client.request("POST", endpoint, body=body, content_type=content_type, accept=accept)
        write_text(variant_dir / "response_body.txt", response)
        write_json(variant_dir / "response_meta.json", {"http_status": status, "headers": headers})
        time.sleep(1.0)
        present_after = ovs_group_present(bridge)
        attempt = {"variant": name, "http_status": status, "restconf_success_status": status in {200, 201, 204}, "ovs_group_present_after": present_after, "response_preview": response[:300]}
        attempts.append(attempt)
        if attempt["restconf_success_status"] and present_after:
            return {"bridge": bridge, "node_id": node_id, "status": "PASS", "winning_variant": name, "attempts": attempts}
        if attempt["restconf_success_status"]:
            time.sleep(2.0)
            present_after_second_wait = ovs_group_present(bridge)
            attempt["ovs_group_present_after_second_wait"] = present_after_second_wait
            if present_after_second_wait:
                return {"bridge": bridge, "node_id": node_id, "status": "PASS", "winning_variant": name, "attempts": attempts}
    return {"bridge": bridge, "node_id": node_id, "status": "FAIL", "winning_variant": None, "attempts": attempts}


def fetch_node_operational(client: RestconfClient, node_id: str, out_path: Path) -> Tuple[int, str]:
    status, body, headers = client.request("GET", f"/rests/data/opendaylight-inventory:nodes/node={node_id}?content=nonconfig", accept="application/json")
    write_text(out_path, body)
    write_json(out_path.with_suffix(".meta.json"), {"http_status": status, "headers": headers})
    return status, body


def node_operational_has_group(body: str) -> bool:
    def local_key(key: Any) -> str:
        return str(key).split(":")[-1]
    def group_id_matches(value: Any) -> bool:
        try:
            return int(value) == GROUP_ID
        except (TypeError, ValueError):
            return str(value).strip() == str(GROUP_ID)
    def walk(obj: Any) -> bool:
        if isinstance(obj, dict):
            found_group_id = None
            found_group_type = None
            for key, value in obj.items():
                lk = local_key(key)
                if lk == "group-id":
                    found_group_id = value
                elif lk == "group-type":
                    found_group_type = value
            if found_group_id is not None and group_id_matches(found_group_id):
                type_text = str(found_group_type or "").lower()
                if not type_text or "select" in type_text:
                    return True
            return any(walk(value) for value in obj.values())
        if isinstance(obj, list):
            return any(walk(item) for item in obj)
        return False
    try:
        return walk(json.loads(body))
    except json.JSONDecodeError:
        compact = body.replace(" ", "").replace("\n", "").replace("\t", "")
        return "group-id" in compact and str(GROUP_ID) in compact and "select" in compact.lower()


# ---------------------------------------------------------------------------
# Parseo de evidencias
# ---------------------------------------------------------------------------


def parse_ping(path: Path) -> Dict[str, Any]:
    text = read_text(path)
    loss_match = re.search(r"(\d+(?:\.\d+)?)% packet loss", text)
    rtt_match = re.search(r"rtt min/avg/max/(?:mdev|stddev) = ([0-9.]+)/([0-9.]+)/([0-9.]+)/([0-9.]+) ms", text)
    return {
        "packet_loss_pct": float(loss_match.group(1)) if loss_match else None,
        "rtt_avg_ms": float(rtt_match.group(2)) if rtt_match else None,
        "ping_ok": bool(loss_match and float(loss_match.group(1)) == 0.0),
    }


def parse_iperf_json(path: Path) -> Dict[str, Any]:
    data = read_json(path)
    if not data:
        return {"file": str(path), "ok": False, "receiver_gbps": None, "error": "invalid_json"}
    end = data.get("end", {})
    sum_received = end.get("sum_received", {}) or {}
    sum_sent = end.get("sum_sent", {}) or {}
    receiver_bps = as_float(sum_received.get("bits_per_second"))
    sender_bps = as_float(sum_sent.get("bits_per_second"))
    return {
        "file": str(path),
        "ok": receiver_bps is not None,
        "receiver_gbps": receiver_bps / 1e9 if receiver_bps is not None else None,
        "sender_gbps": sender_bps / 1e9 if sender_bps is not None else None,
        "seconds": as_float(sum_received.get("seconds")),
        "error": data.get("error", ""),
    }


def parse_flow_counter(path: Path, dst_mac: str) -> Dict[str, int]:
    text = read_text(path)
    packets = 0
    bytes_ = 0
    for line in text.splitlines():
        if f"dl_dst={dst_mac}" not in line:
            continue
        pkt_match = re.search(r"n_packets=(\d+)", line)
        byte_match = re.search(r"n_bytes=(\d+)", line)
        if pkt_match:
            packets += int(pkt_match.group(1))
        if byte_match:
            bytes_ += int(byte_match.group(1))
    return {"packets": packets, "bytes": bytes_}


def parse_branch_usage(stage_dir: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    flows_dir = stage_dir / "flows_after_traffic"
    for branch_name, info in BRANCHES.items():
        switch = info["switch"]
        dst_mac = info["dst_mac"]
        flow_file = flows_dir / f"{switch}_flows.txt"
        counters = parse_flow_counter(flow_file, dst_mac)
        rows.append({"branch": branch_name, "switch": switch, "dst_mac": dst_mac, "description": info["description"], "packets": counters["packets"], "bytes": counters["bytes"], "evidence_file": str(flow_file)})
    forward_total = sum(r["bytes"] for r in rows if r["branch"].startswith("forward_"))
    reverse_total = sum(r["bytes"] for r in rows if r["branch"].startswith("reverse_"))
    for row in rows:
        if row["branch"].startswith("forward_"):
            row["share_pct"] = (100.0 * row["bytes"] / forward_total) if forward_total > 0 else None
        else:
            row["share_pct"] = (100.0 * row["bytes"] / reverse_total) if reverse_total > 0 else None
    return rows


# ---------------------------------------------------------------------------
# Acciones
# ---------------------------------------------------------------------------


def action_probe_existing(paths: CampaignPaths) -> None:
    paths.ensure()
    require_sudo_cached()
    client = restconf_client_from_env()
    bridge_rows: Dict[str, Any] = {}
    for br, info in GROUP_TARGETS.items():
        exists = ovs_bridge_exists(br)
        node_id = bridge_to_node_id(br) if exists else None
        bridge_rows[br] = {"exists": exists, "node_id": node_id, "node_id_expected": info["node_id_expected"], "node_id_matches_expected": node_id == info["node_id_expected"], "ovs_group_present": ovs_group_present(br) if exists else False, "buckets": info["buckets"]}
        if exists:
            capture_ovs_group_state(paths.probe / "ovs_existing", [br])
            status, body = fetch_node_operational(client, node_id, paths.probe / f"odl_operational_{br}_{node_id.replace(':', '_')}.json")
            bridge_rows[br]["odl_get_status"] = status
            bridge_rows[br]["odl_group_present"] = node_operational_has_group(body)
    status = "PASS" if all(v.get("exists") for v in bridge_rows.values()) else "CHECK_REQUIRED"
    summary = {"campaign_id": paths.campaign_id, "status": status, "scope": "C.2 RESTCONF group-select feasibility probe", "bridges": bridge_rows, "odl_endpoint": client.base_url}
    write_json(paths.probe / "probe_existing_summary.json", summary)
    print("C.2 probe-existing")
    print("=" * 60)
    print(f"campaign_id: {paths.campaign_id}")
    for br, row in bridge_rows.items():
        print(f"{br}: exists={row.get('exists')} node_id={row.get('node_id')} ovs_group_present={row.get('ovs_group_present')} odl_group_present={row.get('odl_group_present')}")
    print(f"status: {status}")
    print(f"report: {paths.probe / 'probe_existing_summary.json'}")


def action_cleanup_ovs(paths: CampaignPaths) -> None:
    paths.ensure()
    result = cleanup_ovs_groups_and_flows(paths)
    write_json(paths.cleanup / "cleanup_ovs_summary.json", result)
    print("C.2 cleanup-ovs")
    print("=" * 60)
    print(f"campaign_id: {paths.campaign_id}")
    for br, row in result["bridges"].items():
        print(f"{br}: {row}")
    print(f"status: {result['status']}")
    print(f"report: {paths.cleanup / 'cleanup_ovs_summary.json'}")


def action_cleanup_forwarding_ovs(paths: CampaignPaths) -> None:
    paths.ensure()
    result = cleanup_bridges(paths, ALL_RELEVANT_SWITCHES, paths.cleanup_full, delete_groups=True)
    write_json(paths.cleanup_full / "cleanup_forwarding_ovs_summary.json", result)
    print("C.2.2 cleanup-forwarding-ovs")
    print("=" * 60)
    print(f"campaign_id: {paths.campaign_id}")
    for br, row in result["bridges"].items():
        print(f"{br}: {row}")
    print(f"status: {result['status']}")
    print(f"report: {paths.cleanup_full / 'cleanup_forwarding_ovs_summary.json'}")


def action_install_groups_restconf(paths: CampaignPaths) -> None:
    paths.ensure()
    require_sudo_cached()
    client = restconf_client_from_env()
    results: Dict[str, Any] = {}

    for br, info in GROUP_TARGETS.items():
        if not ovs_bridge_exists(br):
            results[br] = {"status": "MISSING_BRIDGE"}
            continue

        node_id = bridge_to_node_id(br)

        # 1) RPC sal-group:add-group: ya sabemos que funciona con json_input.
        rpc_result = post_group_with_variants(
            client=client,
            bridge=br,
            node_id=node_id,
            buckets=info["buckets"],
            out_dir=paths.install / "restconf_attempts",
        )

        # 2) Datastore PUT del grupo: necesario para que los flows configurados
        #    por datastore puedan referenciar group:100 de forma coherente.
        datastore_result = put_group_datastore_with_variants(
            client=client,
            bridge=br,
            node_id=node_id,
            buckets=info["buckets"],
            out_dir=paths.install / "datastore_group_put_attempts",
        )

        final_ok = (
            rpc_result.get("status") == "PASS"
            and datastore_result.get("status") == "PASS"
            and ovs_group_present(br)
        )

        results[br] = {
            "status": "PASS" if final_ok else "CHECK_REQUIRED",
            "node_id": node_id,
            "rpc": rpc_result,
            "datastore": datastore_result,
            "ovs_group_present": ovs_group_present(br),
        }

    capture_ovs_group_state(paths.install / "ovs_after_restconf_install")

    for br in GROUP_SWITCHES:
        if ovs_bridge_exists(br):
            node_id = bridge_to_node_id(br)
            fetch_node_operational(client, node_id, paths.install / f"odl_operational_after_{br}_{node_id.replace(':', '_')}.json")

    status = "PASS" if all(r.get("status") == "PASS" for r in results.values()) else "CHECK_REQUIRED"
    summary = {
        "campaign_id": paths.campaign_id,
        "status": status,
        "scope": "RESTCONF RPC + datastore PUT installation for e1/e8 group-select",
        "results": results,
    }
    write_json(paths.install / "install_groups_restconf_summary.json", summary)

    print("C.2 install-groups-restconf")
    print("=" * 60)
    print(f"campaign_id: {paths.campaign_id}")
    for br, row in results.items():
        rpc = row.get("rpc", {})
        ds = row.get("datastore", {})
        print(
            f"{br}: status={row.get('status')} node_id={row.get('node_id')} "
            f"rpc_variant={rpc.get('winning_variant')} datastore_variant={ds.get('winning_variant')} "
            f"ovs_group_present={row.get('ovs_group_present')}"
        )
        for attempt in rpc.get("attempts", []):
            print(
                f"  RPC - {attempt.get('variant')}: http={attempt.get('http_status')} "
                f"rest_ok={attempt.get('restconf_success_status')} "
                f"ovs_present={attempt.get('ovs_group_present_after')}"
            )
        for attempt in ds.get("attempts", []):
            print(
                f"  DS  - {attempt.get('variant')}: http={attempt.get('http_status')} "
                f"rest_ok={attempt.get('restconf_success_status')} "
                f"ovs_present={attempt.get('ovs_group_present_after')}"
            )
    print(f"status: {status}")
    print(f"report: {paths.install / 'install_groups_restconf_summary.json'}")


def action_verify_groups(paths: CampaignPaths) -> None:
    paths.ensure()
    require_sudo_cached()
    client = restconf_client_from_env()
    capture_ovs_group_state(paths.verify / "ovs_verify")
    rows: Dict[str, Any] = {}
    for br in GROUP_SWITCHES:
        exists = ovs_bridge_exists(br)
        node_id = bridge_to_node_id(br) if exists else None
        ovs_present = ovs_group_present(br) if exists else False
        odl_present = False
        odl_status = None
        if exists and node_id:
            odl_status, body = fetch_node_operational(client, node_id, paths.verify / f"odl_operational_verify_{br}_{node_id.replace(':', '_')}.json")
            odl_present = node_operational_has_group(body)
        rows[br] = {"exists": exists, "node_id": node_id, "ovs_group_present": ovs_present, "odl_get_status": odl_status, "odl_group_present": odl_present}
    status = "PASS" if all(row["ovs_group_present"] and row["odl_group_present"] for row in rows.values()) else "CHECK_REQUIRED"
    summary = {"campaign_id": paths.campaign_id, "status": status, "scope": "Verification of RESTCONF-installed group-select entries in OVS and ODL operational inventory", "bridges": rows}
    write_json(paths.verify / "verify_groups_summary.json", summary)
    print("C.2 verify-groups")
    print("=" * 60)
    print(f"campaign_id: {paths.campaign_id}")
    for br, row in rows.items():
        print(f"{br}: node_id={row.get('node_id')} ovs_group_present={row.get('ovs_group_present')} odl_group_present={row.get('odl_group_present')} odl_get_status={row.get('odl_get_status')}")
    print(f"status: {status}")
    print(f"report: {paths.verify / 'verify_groups_summary.json'}")


def action_install_flows_restconf(paths: CampaignPaths) -> None:
    paths.ensure()
    require_sudo_cached()
    client = restconf_client_from_env()
    flow_results: List[Dict[str, Any]] = []

    # En C.2.2 instalamos solo los flows de forwarding necesarios para h1<->h16.
    # No instalamos ARP-drop ni table-miss auxiliares porque, tras cleanup, OVS
    # ya no tiene flows residuales y la prueba usa ARP estático. Esto evita que
    # reglas auxiliares no esenciales contaminen el criterio de aceptación.
    all_flows = list(RESTCONF_FORWARDING_FLOWS)

    for flow in all_flows:
        br = flow["bridge"]
        node_id = bridge_to_node_id(br)
        result = put_flow_datastore_with_variants(
            client=client,
            bridge=br,
            node_id=node_id,
            flow=flow,
            out_dir=paths.flows / "datastore_put_attempts",
        )
        flow_results.append({
            "flow_id": flow["flow_id"],
            "bridge": br,
            "node_id": node_id,
            "kind": flow.get("kind", "forwarding"),
            "action": flow.get("action"),
            "dst_mac": flow.get("dst_mac"),
            "status": result["status"],
            "winning_variant": result.get("winning_variant"),
            "attempts": result.get("attempts", []),
        })

    capture_ovs_state(paths.flows / "ovs_after_restconf_flows", ALL_RELEVANT_SWITCHES)
    status = "PASS" if all(row["status"] == "PASS" for row in flow_results) else "CHECK_REQUIRED"
    summary = {
        "campaign_id": paths.campaign_id,
        "status": status,
        "scope": "RESTCONF datastore PUT installation for ECMP-limited h1-h16 forwarding",
        "method": "PUT /rests/data/.../flow=<flow_id>",
        "n_flows": len(flow_results),
        "n_ok": sum(1 for r in flow_results if r["status"] == "PASS"),
        "results": flow_results,
    }
    write_json(paths.flows / "install_flows_restconf_summary.json", summary)

    print("C.2.2 install-flows-restconf")
    print("=" * 60)
    print(f"campaign_id: {paths.campaign_id}")
    print("method: RESTCONF datastore PUT")
    print(f"n_flows: {summary['n_flows']}")
    print(f"n_ok: {summary['n_ok']}/{summary['n_flows']}")
    for row in flow_results:
        if row["status"] != "PASS":
            print(f"FAIL {row['bridge']} {row['flow_id']}: attempts={row['attempts']}")
    print(f"status: {status}")
    print(f"report: {paths.flows / 'install_flows_restconf_summary.json'}")



def action_prepare_traffic(paths: CampaignPaths) -> None:
    paths.ensure()
    raw = paths.traffic / "raw"
    arp = paths.traffic / "arp_checks"
    conn = paths.traffic / "connectivity_checks"
    flows_after = paths.traffic / "flows_after_traffic"
    groups_after = paths.traffic / "groups_after_traffic"
    cli_path = paths.traffic / "mininet_c2_2_restconf_traffic.cli"
    commands: List[str] = []
    commands.append(f"sh mkdir -p {raw} {arp} {conn} {flows_after} {groups_after}")
    commands.append("h1 ip neigh flush all")
    commands.append("h16 ip neigh flush all")
    commands.append(f"h1 ip neigh replace {H16_IP} lladdr {H16_MAC} nud permanent dev h1-eth1")
    commands.append(f"h16 ip neigh replace {H1_IP} lladdr {H1_MAC} nud permanent dev h16-eth1")
    commands.append(f"h1 ip neigh show | tee {arp}/h1_static_arp.txt")
    commands.append(f"h16 ip neigh show | tee {arp}/h16_static_arp.txt")
    commands.append(f"h1 ping -c 10 h16 | tee {conn}/h1_h16_ping.txt")
    commands.append(f"h16 pkill -f 'iperf3 -s -p {IPERF_PORT}'")
    commands.append(f"h16 iperf3 -s -p {IPERF_PORT} -D")
    commands.append("sh sleep 1")
    commands.append(f"h16 ss -lnt | grep :{IPERF_PORT} | tee {paths.traffic}/iperf_server_ready.txt")
    for rep in range(1, N_REPETITIONS + 1):
        commands.append(f"h1 iperf3 -c {H16_IP} -p {IPERF_PORT} -P {IPERF_PARALLEL} -t {IPERF_SECONDS} -J > {raw}/iperf_restconf_select_rep{rep:02d}.json")
        commands.append("sh sleep 1")
    for sw in ALL_RELEVANT_SWITCHES:
        commands.append(f"sh ovs-ofctl -O OpenFlow13 dump-flows {sw} > {flows_after}/{sw}_flows.txt")
        commands.append(f"sh ovs-ofctl -O OpenFlow13 dump-groups {sw} > {groups_after}/{sw}_groups.txt")
        commands.append(f"sh ovs-ofctl -O OpenFlow13 dump-group-stats {sw} > {groups_after}/{sw}_group_stats.txt")
    write_text(cli_path, "\n".join(commands) + "\n")
    print("C.2.2 prepare-traffic: OK")
    print(f"campaign_id: {paths.campaign_id}")
    print("Ejecuta dentro de Mininet:")
    print(f"source {cli_path}")


def action_summarize_traffic(paths: CampaignPaths) -> None:
    paths.ensure()
    ping = parse_ping(paths.traffic / "connectivity_checks" / "h1_h16_ping.txt")
    iperf_rows: List[Dict[str, Any]] = []
    for rep in range(1, N_REPETITIONS + 1):
        row = parse_iperf_json(paths.traffic / "raw" / f"iperf_restconf_select_rep{rep:02d}.json")
        row["rep"] = rep
        iperf_rows.append(row)
    valid = [r for r in iperf_rows if r.get("ok")]
    branch_rows = parse_branch_usage(paths.traffic)
    write_csv(paths.processed / "c2_2_restconf_iperf_summary.csv", iperf_rows)
    write_csv(paths.processed / "c2_2_restconf_branch_usage.csv", branch_rows)
    f1 = next((r for r in branch_rows if r["branch"] == "forward_1"), {})
    f2 = next((r for r in branch_rows if r["branch"] == "forward_2"), {})
    r1 = next((r for r in branch_rows if r["branch"] == "reverse_1"), {})
    r2 = next((r for r in branch_rows if r["branch"] == "reverse_2"), {})
    group_ok = ovs_group_present("e1") and ovs_group_present("e8")
    branch_ok = f1.get("bytes", 0) > 0 and f2.get("bytes", 0) > 0
    status = "PASS" if ping.get("ping_ok") and len(valid) == N_REPETITIONS and group_ok and branch_ok else "CHECK_REQUIRED"
    summary = {
        "campaign_id": paths.campaign_id,
        "status": status,
        "scope": "C.2.2 RESTCONF-installed ECMP-limited forwarding h1-h16",
        "ping_ok": ping.get("ping_ok"),
        "packet_loss_pct": ping.get("packet_loss_pct"),
        "rtt_avg_ms": ping.get("rtt_avg_ms"),
        "n_iperf_ok": len(valid),
        "n_iperf_total": N_REPETITIONS,
        "throughput_mean_gbps": mean(r.get("receiver_gbps") for r in valid),
        "group_evidence_ok": group_ok,
        "branch_evidence_ok": branch_ok,
        "forward_1_share_pct": f1.get("share_pct"),
        "forward_2_share_pct": f2.get("share_pct"),
        "reverse_1_share_pct": r1.get("share_pct"),
        "reverse_2_share_pct": r2.get("share_pct"),
        "forward_1_bytes": f1.get("bytes", 0),
        "forward_2_bytes": f2.get("bytes", 0),
        "reverse_1_bytes": r1.get("bytes", 0),
        "reverse_2_bytes": r2.get("bytes", 0),
    }
    write_json(paths.traffic / "c2_2_restconf_traffic_summary.json", summary)
    write_json(paths.processed / "c2_2_restconf_traffic_summary.json", summary)
    print("C.2.2 summarize-traffic")
    print("=" * 60)
    print(f"campaign_id: {paths.campaign_id}")
    print(f"ping_ok: {summary['ping_ok']}")
    print(f"n_iperf_ok: {summary['n_iperf_ok']}/{summary['n_iperf_total']}")
    print(f"throughput_mean_gbps: {fmt_num(summary['throughput_mean_gbps'], 2)}")
    print(f"group_evidence_ok: {summary['group_evidence_ok']}")
    print(f"branch_evidence_ok: {summary['branch_evidence_ok']}")
    print(f"forward_1_share_pct: {fmt_num(summary['forward_1_share_pct'], 1)}")
    print(f"forward_2_share_pct: {fmt_num(summary['forward_2_share_pct'], 1)}")
    print(f"status: {status}")
    print(f"report: {paths.traffic / 'c2_2_restconf_traffic_summary.json'}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fase C.2 — RESTCONF group-select and flow demo.")
    parser.add_argument("--tfg-root", default=str(default_tfg_root()))
    parser.add_argument("--campaign-id", default=None)
    parser.add_argument("--action", required=True, choices=[
        "probe-existing",
        "cleanup-ovs",
        "cleanup-forwarding-ovs",
        "install-groups-restconf",
        "verify-groups",
        "install-flows-restconf",
        "prepare-traffic",
        "summarize-traffic",
    ])
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    tfg_root = Path(args.tfg_root).expanduser().resolve()
    campaign_id = resolve_campaign_id(tfg_root, args.campaign_id, args.action)
    paths = CampaignPaths(tfg_root=tfg_root, campaign_id=campaign_id)
    paths.ensure()
    try:
        if args.action == "probe-existing":
            action_probe_existing(paths)
        elif args.action == "cleanup-ovs":
            action_cleanup_ovs(paths)
        elif args.action == "cleanup-forwarding-ovs":
            action_cleanup_forwarding_ovs(paths)
        elif args.action == "install-groups-restconf":
            action_install_groups_restconf(paths)
        elif args.action == "verify-groups":
            action_verify_groups(paths)
        elif args.action == "install-flows-restconf":
            action_install_flows_restconf(paths)
        elif args.action == "prepare-traffic":
            action_prepare_traffic(paths)
        elif args.action == "summarize-traffic":
            action_summarize_traffic(paths)
        else:
            raise ValueError(args.action)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
