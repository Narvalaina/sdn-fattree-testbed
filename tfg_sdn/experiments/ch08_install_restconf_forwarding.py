#!/usr/bin/env python3
"""
FT8.R — Instalación de forwarding determinista vía OpenDaylight RESTCONF.

Proyecto:
    TFG SDN Fat-Tree — Capítulo 8

Ruta esperada:
    tfg_sdn/experiments/ch08_install_restconf_forwarding.py

Objetivo:
    Instalar reglas deterministas para la topología Fat-Tree k=4 usando
    OpenDaylight RESTCONF y verificar que dichas reglas llegan al datapath
    de Open vSwitch mediante ovs-ofctl dump-flows.

Notas metodológicas:
    - La limpieza opcional con ovs-ofctl solo se usa para retirar estado
      residual antes de la instalación. Las reglas de forwarding evaluadas
      se instalan mediante OpenDaylight RESTCONF.
    - Las reglas usan dl_dst/MAC destino, igual que la campaña piloto, pero
      se inyectan desde ODL para que la campaña final sea SDN/RESTCONF.
    - No implementa ECMP ni reconfiguración dinámica.
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import json
import os
import socket
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


TOPOLOGY_BRIDGES: List[str] = [
    "e1", "e2", "e3", "e4", "e5", "e6", "e7", "e8",
    "a1", "a2", "a3", "a4", "a5", "a6", "a7", "a8",
    "c1", "c2", "c3", "c4",
]

# Mapa real de puertos Fat-Tree k=4 extraído del comando `net` de Mininet.
PORTS: Dict[str, Dict[str, int]] = {
    "e1": {"h1": 1, "h2": 2, "a1": 3, "a2": 4},
    "e2": {"h3": 1, "h4": 2, "a1": 3, "a2": 4},
    "e3": {"h5": 1, "h6": 2, "a3": 3, "a4": 4},
    "e4": {"h7": 1, "h8": 2, "a3": 3, "a4": 4},
    "e5": {"h9": 1, "h10": 2, "a5": 3, "a6": 4},
    "e6": {"h11": 1, "h12": 2, "a5": 3, "a6": 4},
    "e7": {"h13": 1, "h14": 2, "a7": 3, "a8": 4},
    "e8": {"h15": 1, "h16": 2, "a7": 3, "a8": 4},

    "a1": {"e1": 1, "e2": 2, "c1": 3, "c2": 4},
    "a2": {"e1": 1, "e2": 2, "c3": 3, "c4": 4},
    "a3": {"e3": 1, "e4": 2, "c1": 3, "c2": 4},
    "a4": {"e3": 1, "e4": 2, "c3": 3, "c4": 4},
    "a5": {"e5": 1, "e6": 2, "c1": 3, "c2": 4},
    "a6": {"e5": 1, "e6": 2, "c3": 3, "c4": 4},
    "a7": {"e7": 1, "e8": 2, "c1": 3, "c2": 4},
    "a8": {"e7": 1, "e8": 2, "c3": 3, "c4": 4},

    "c1": {"a1": 1, "a3": 2, "a5": 3, "a7": 4},
    "c2": {"a1": 1, "a3": 2, "a5": 3, "a7": 4},
    "c3": {"a2": 1, "a4": 2, "a6": 3, "a8": 4},
    "c4": {"a2": 1, "a4": 2, "a6": 3, "a8": 4},
}

# Caminos deterministas que reproducen las pruebas cerradas en la campaña piloto.
P2P_PATHS: List[Tuple[str, int, int, List[str]]] = [
    ("t1_local_h1_h2", 1, 2, ["e1"]),
    ("t2_intrapod_h1_h3", 1, 3, ["e1", "a1", "e2"]),
    ("t3_interpod_h1_h16", 1, 16, ["e1", "a1", "c1", "a7", "e8"]),
]

CONCURRENT_PATHS: List[Tuple[str, int, int, List[str]]] = [
    ("t4_h1_h16", 1, 16, ["e1", "a1", "c1", "a7", "e8"]),
    ("t4_h3_h14", 3, 14, ["e2", "a2", "c4", "a8", "e7"]),
    ("t4_h5_h12", 5, 12, ["e3", "a3", "c2", "a5", "e6"]),
    ("t4_h7_h10", 7, 10, ["e4", "a4", "c3", "a6", "e5"]),
]


@dataclass
class CmdResult:
    command: List[str]
    returncode: int
    stdout: str
    stderr: str


@dataclass
class HttpResult:
    method: str
    url: str
    status: int
    ok: bool
    response: str
    error: Optional[str] = None


@dataclass
class FlowSpec:
    flow_id: str
    bridge: str
    node_id: str
    table_id: int
    priority: int
    cookie: int
    match_type: str  # all | arp | eth_dst
    eth_dst: Optional[str]
    out_port: Optional[int]
    description: str
    sources: List[str]


def now_campaign_id() -> str:
    return "ch08_fattree_restconf_" + dt.datetime.now().strftime("%Y%m%d_%H%M")


def host_mac(host_id: int) -> str:
    if host_id < 1 or host_id > 254:
        raise ValueError(f"host_id fuera de rango: {host_id}")
    return f"00:00:00:00:00:{host_id:02x}"


def sudo_prefix(use_sudo: bool) -> List[str]:
    if not use_sudo or os.geteuid() == 0:
        return []
    return ["sudo"]


def run_cmd(command: Sequence[str], check: bool = False) -> CmdResult:
    completed = subprocess.run(
        list(command),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    result = CmdResult(
        command=list(command),
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )
    if check and completed.returncode != 0:
        cmd = " ".join(command)
        raise RuntimeError(
            f"Comando fallido ({completed.returncode}): {cmd}\n"
            f"STDOUT:\n{completed.stdout}\nSTDERR:\n{completed.stderr}"
        )
    return result


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=False, ensure_ascii=False), encoding="utf-8")


def append_jsonl(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(data, ensure_ascii=False, sort_keys=False) + "\n")


def tcp_open(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def basic_auth_header(user: str, password: str) -> str:
    token = base64.b64encode(f"{user}:{password}".encode("utf-8")).decode("ascii")
    return f"Basic {token}"


def http_request(
    method: str,
    url: str,
    user: str,
    password: str,
    body: Optional[Dict[str, Any]] = None,
    timeout: float = 20.0,
) -> HttpResult:
    data = None
    headers = {
        "Accept": "application/json",
        "Authorization": basic_auth_header(user, password),
    }
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = Request(url=url, data=data, headers=headers, method=method.upper())

    try:
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            status = int(resp.getcode())
            return HttpResult(method=method.upper(), url=url, status=status, ok=200 <= status < 300, response=raw)
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        return HttpResult(
            method=method.upper(),
            url=url,
            status=int(exc.code),
            ok=False,
            response=raw,
            error=str(exc),
        )
    except URLError as exc:
        return HttpResult(
            method=method.upper(),
            url=url,
            status=0,
            ok=False,
            response="",
            error=str(exc),
        )


def restconf_data_url(host: str, port: int, path: str) -> str:
    encoded_path = quote(path.lstrip("/"), safe="/:=,[]'()?&")
    return f"http://{host}:{port}/rests/data/{encoded_path}"


def restconf_ops_url(host: str, port: int, operation: str) -> str:
    encoded_op = quote(operation.lstrip("/"), safe="/:")
    return f"http://{host}:{port}/rests/operations/{encoded_op}"


def list_ovs_bridges(use_sudo: bool) -> List[str]:
    cmd = sudo_prefix(use_sudo) + ["ovs-vsctl", "list-br"]
    result = run_cmd(cmd, check=True)
    return sorted([line.strip() for line in result.stdout.splitlines() if line.strip()])


def get_bridge_dpid_hex(bridge: str, use_sudo: bool) -> str:
    cmd = sudo_prefix(use_sudo) + ["ovs-vsctl", "get", "bridge", bridge, "datapath_id"]
    result = run_cmd(cmd, check=True)
    dpid = result.stdout.strip().strip('"').strip()
    if not dpid:
        raise RuntimeError(f"No se pudo obtener datapath_id para bridge={bridge}")
    int(dpid, 16)  # validación
    return dpid.lower()


def build_bridge_node_map(bridges: Iterable[str], use_sudo: bool) -> Dict[str, Dict[str, str]]:
    mapping: Dict[str, Dict[str, str]] = {}
    for bridge in bridges:
        dpid_hex = get_bridge_dpid_hex(bridge, use_sudo=use_sudo)
        node_id = f"openflow:{int(dpid_hex, 16)}"
        mapping[bridge] = {
            "bridge": bridge,
            "datapath_id_hex": dpid_hex,
            "datapath_id_decimal": str(int(dpid_hex, 16)),
            "node_id": node_id,
        }
    return mapping


def selected_paths(profile: str) -> List[Tuple[str, int, int, List[str]]]:
    if profile == "p2p":
        return list(P2P_PATHS)
    if profile == "t4":
        return list(CONCURRENT_PATHS)
    if profile == "all":
        # Se devuelven también caminos repetidos para conservar trazabilidad de fuentes.
        # La deduplicación de reglas se hace después por bridge+MAC destino.
        return list(P2P_PATHS) + list(CONCURRENT_PATHS)
    raise ValueError(f"Perfil desconocido: {profile}")


def add_or_merge_rule(
    rules: Dict[Tuple[str, str], Dict[str, Any]],
    bridge: str,
    dst_host_id: int,
    out_port: int,
    source_tag: str,
    description: str,
) -> None:
    mac = host_mac(dst_host_id)
    key = (bridge, mac)

    if key in rules:
        previous = rules[key]
        if previous["out_port"] != out_port:
            raise RuntimeError(
                "Conflicto de forwarding determinista: "
                f"bridge={bridge}, dst={mac}, out_prev={previous['out_port']}, "
                f"out_new={out_port}, source={source_tag}"
            )
        previous["sources"].append(source_tag)
        return

    rules[key] = {
        "bridge": bridge,
        "dst_host_id": dst_host_id,
        "eth_dst": mac,
        "out_port": out_port,
        "sources": [source_tag],
        "description": description,
    }


def generate_forward_rules(profile: str, bridge_node_map: Dict[str, Dict[str, str]], first_cookie: int) -> List[FlowSpec]:
    rules: Dict[Tuple[str, str], Dict[str, Any]] = {}

    for tag, host_a, host_b, path in selected_paths(profile):
        if not path:
            raise RuntimeError(f"Camino vacío en {tag}")

        for idx, bridge in enumerate(path):
            if bridge not in PORTS:
                raise RuntimeError(f"Bridge sin mapa de puertos: {bridge}")

            # Tráfico cuyo destino es host_a: avanzar hacia el extremo izquierdo del camino.
            next_hop_to_a = f"h{host_a}" if idx == 0 else path[idx - 1]
            # Tráfico cuyo destino es host_b: avanzar hacia el extremo derecho del camino.
            next_hop_to_b = f"h{host_b}" if idx == len(path) - 1 else path[idx + 1]

            if next_hop_to_a not in PORTS[bridge]:
                raise RuntimeError(f"Puerto no definido: {bridge} -> {next_hop_to_a} en {tag}")
            if next_hop_to_b not in PORTS[bridge]:
                raise RuntimeError(f"Puerto no definido: {bridge} -> {next_hop_to_b} en {tag}")

            add_or_merge_rule(
                rules=rules,
                bridge=bridge,
                dst_host_id=host_a,
                out_port=PORTS[bridge][next_hop_to_a],
                source_tag=tag,
                description=f"{bridge}: dst h{host_a} hacia {next_hop_to_a}",
            )
            add_or_merge_rule(
                rules=rules,
                bridge=bridge,
                dst_host_id=host_b,
                out_port=PORTS[bridge][next_hop_to_b],
                source_tag=tag,
                description=f"{bridge}: dst h{host_b} hacia {next_hop_to_b}",
            )

    specs: List[FlowSpec] = []
    cookie = first_cookie
    for item in sorted(rules.values(), key=lambda x: (x["bridge"], x["dst_host_id"])):
        bridge = item["bridge"]
        if bridge not in bridge_node_map:
            raise RuntimeError(f"No existe mapeo bridge->node para {bridge}")

        flow = FlowSpec(
            flow_id=f"ft8r_fwd_{bridge}_dst_h{item['dst_host_id']}",
            bridge=bridge,
            node_id=bridge_node_map[bridge]["node_id"],
            table_id=0,
            priority=250,
            cookie=cookie,
            match_type="eth_dst",
            eth_dst=item["eth_dst"],
            out_port=int(item["out_port"]),
            description=item["description"],
            sources=sorted(set(item["sources"])),
        )
        specs.append(flow)
        cookie += 1

    return specs


def generate_base_drop_rules(
    bridge_node_map: Dict[str, Dict[str, str]],
    install_arp_drop: bool,
    install_table_miss_drop: bool,
    first_cookie: int,
) -> List[FlowSpec]:
    specs: List[FlowSpec] = []
    cookie = first_cookie

    for bridge in TOPOLOGY_BRIDGES:
        if bridge not in bridge_node_map:
            raise RuntimeError(f"No existe mapeo bridge->node para {bridge}")

        node_id = bridge_node_map[bridge]["node_id"]

        if install_arp_drop:
            specs.append(
                FlowSpec(
                    flow_id=f"ft8r_base_{bridge}_arp_drop",
                    bridge=bridge,
                    node_id=node_id,
                    table_id=0,
                    priority=300,
                    cookie=cookie,
                    match_type="arp",
                    eth_dst=None,
                    out_port=None,
                    description=f"{bridge}: drop ARP para usar ARP estático en los ensayos",
                    sources=["base"],
                )
            )
            cookie += 1

        if install_table_miss_drop:
            specs.append(
                FlowSpec(
                    flow_id=f"ft8r_base_{bridge}_table_miss_drop",
                    bridge=bridge,
                    node_id=node_id,
                    table_id=0,
                    priority=0,
                    cookie=cookie,
                    match_type="all",
                    eth_dst=None,
                    out_port=None,
                    description=f"{bridge}: table-miss drop determinista",
                    sources=["base"],
                )
            )
            cookie += 1

    return specs


def build_match(flow: FlowSpec) -> Dict[str, Any]:
    if flow.match_type == "all":
        return {}
    if flow.match_type == "arp":
        return {
            "ethernet-match": {
                "ethernet-type": {
                    "type": 2054
                }
            }
        }
    if flow.match_type == "eth_dst":
        if not flow.eth_dst:
            raise RuntimeError(f"Flow eth_dst sin MAC destino: {flow.flow_id}")
        return {
            "ethernet-match": {
                "ethernet-destination": {
                    "address": flow.eth_dst
                }
            }
        }
    raise RuntimeError(f"match_type no soportado: {flow.match_type}")


def build_instructions(flow: FlowSpec) -> Optional[Dict[str, Any]]:
    if flow.out_port is None:
        return None

    return {
        "instruction": [
            {
                "order": 0,
                "apply-actions": {
                    "action": [
                        {
                            "order": 0,
                            "output-action": {
                                "output-node-connector": str(flow.out_port),
                                "max-length": 65535,
                            },
                        }
                    ]
                },
            }
        ]
    }


def build_flow_object(flow: FlowSpec) -> Dict[str, Any]:
    obj: Dict[str, Any] = {
        "id": flow.flow_id,
        "flow-name": flow.flow_id,
        "table_id": flow.table_id,
        "priority": flow.priority,
        "cookie": flow.cookie,
        "idle-timeout": 0,
        "hard-timeout": 0,
        "match": build_match(flow),
    }
    instructions = build_instructions(flow)
    if instructions is not None:
        obj["instructions"] = instructions
    return obj


def build_put_payload(flow: FlowSpec, namespaced: bool = True) -> Dict[str, Any]:
    key = "flow-node-inventory:flow" if namespaced else "flow"
    return {key: [build_flow_object(flow)]}


def build_rpc_payload(flow: FlowSpec) -> Dict[str, Any]:
    node_ref = (
        "/opendaylight-inventory:nodes/"
        f"opendaylight-inventory:node[opendaylight-inventory:id='{flow.node_id}']"
    )

    obj = build_flow_object(flow)
    obj["node"] = node_ref
    obj["barrier"] = True
    return {"input": obj}


def flow_data_path(flow: FlowSpec) -> str:
    return (
        f"opendaylight-inventory:nodes/node={flow.node_id}/"
        f"flow-node-inventory:table={flow.table_id}/"
        f"flow={flow.flow_id}"
    )


def delete_flow_if_present(
    flow: FlowSpec,
    args: argparse.Namespace,
    request_log: Path,
) -> HttpResult:
    url = restconf_data_url(args.odl_host, args.odl_rest_port, flow_data_path(flow))
    result = http_request("DELETE", url, args.odl_user, args.odl_pass)
    append_jsonl(request_log, {
        "phase": "delete_existing",
        "flow_id": flow.flow_id,
        "bridge": flow.bridge,
        "node_id": flow.node_id,
        "method": result.method,
        "url": result.url,
        "status": result.status,
        "ok": result.ok or result.status == 404,
        "error": result.error,
        "response": result.response[:1000],
    })
    return result


def install_flow(
    flow: FlowSpec,
    args: argparse.Namespace,
    request_log: Path,
) -> Tuple[bool, List[Dict[str, Any]]]:
    attempts: List[Dict[str, Any]] = []

    def record_attempt(kind: str, result: HttpResult, payload: Dict[str, Any]) -> None:
        item = {
            "phase": "install",
            "kind": kind,
            "flow_id": flow.flow_id,
            "bridge": flow.bridge,
            "node_id": flow.node_id,
            "method": result.method,
            "url": result.url,
            "status": result.status,
            "ok": result.ok,
            "error": result.error,
            "payload": payload,
            "response": result.response[:2000],
        }
        attempts.append(item)
        append_jsonl(request_log, item)

    if args.restconf_method in ("put", "auto"):
        url = restconf_data_url(args.odl_host, args.odl_rest_port, flow_data_path(flow))

        payload_ns = build_put_payload(flow, namespaced=True)
        result_ns = http_request("PUT", url, args.odl_user, args.odl_pass, body=payload_ns)
        record_attempt("put_namespaced", result_ns, payload_ns)
        if result_ns.ok:
            return True, attempts

        payload_plain = build_put_payload(flow, namespaced=False)
        result_plain = http_request("PUT", url, args.odl_user, args.odl_pass, body=payload_plain)
        record_attempt("put_plain", result_plain, payload_plain)
        if result_plain.ok:
            return True, attempts

        if args.restconf_method == "put":
            return False, attempts

    if args.restconf_method in ("rpc", "auto"):
        url = restconf_ops_url(args.odl_host, args.odl_rest_port, "sal-flow:add-flow")
        payload_rpc = build_rpc_payload(flow)
        result_rpc = http_request("POST", url, args.odl_user, args.odl_pass, body=payload_rpc)
        record_attempt("rpc_sal_flow_add_flow", result_rpc, payload_rpc)
        if result_rpc.ok:
            return True, attempts

    return False, attempts


def cleanup_ovs_flows(bridges: Iterable[str], use_sudo: bool, artifact_dir: Path) -> Dict[str, Any]:
    results: Dict[str, Any] = {}
    lines: List[str] = []
    for bridge in bridges:
        cmd = sudo_prefix(use_sudo) + ["ovs-ofctl", "-O", "OpenFlow13", "del-flows", bridge]
        result = run_cmd(cmd, check=False)
        results[bridge] = asdict(result)
        lines.append(f"$ {' '.join(cmd)}")
        lines.append(f"returncode={result.returncode}")
        if result.stdout:
            lines.append("STDOUT:")
            lines.append(result.stdout.rstrip())
        if result.stderr:
            lines.append("STDERR:")
            lines.append(result.stderr.rstrip())
        lines.append("")
    write_json(artifact_dir / "ovs_cleanup_before_restconf.json", results)
    write_text(artifact_dir / "ovs_cleanup_before_restconf.txt", "\n".join(lines))
    return results


def dump_ovs_flows(bridges: Iterable[str], use_sudo: bool, artifact_dir: Path) -> Dict[str, str]:
    dumps: Dict[str, str] = {}
    for bridge in bridges:
        cmd = sudo_prefix(use_sudo) + ["ovs-ofctl", "-O", "OpenFlow13", "dump-flows", bridge]
        result = run_cmd(cmd, check=False)
        content = (
            f"$ {' '.join(cmd)}\n"
            f"returncode={result.returncode}\n\n"
            f"STDOUT:\n{result.stdout}\n\n"
            f"STDERR:\n{result.stderr}\n"
        )
        write_text(artifact_dir / f"ovs_dump_{bridge}.txt", content)
        dumps[bridge] = result.stdout
    return dumps


def expected_in_dump(flow: FlowSpec, dump: str) -> Tuple[bool, str]:
    if flow.match_type == "eth_dst":
        assert flow.eth_dst is not None
        assert flow.out_port is not None
        has_match = f"dl_dst={flow.eth_dst}" in dump
        has_action = f"actions=output:{flow.out_port}" in dump or f"actions={flow.out_port}" in dump
        if has_match and has_action:
            return True, "eth_dst/output encontrados"
        return False, f"no aparece dl_dst={flow.eth_dst} con output:{flow.out_port}"

    if flow.match_type == "arp":
        has_arp = "arp" in dump and f"priority={flow.priority}" in dump
        has_drop = "actions=drop" in dump
        if has_arp and has_drop:
            return True, "ARP drop encontrado"
        return False, "no aparece ARP drop esperado"

    if flow.match_type == "all":
        has_priority = f"priority={flow.priority}" in dump
        has_drop = "actions=drop" in dump
        if has_priority and has_drop:
            return True, "table-miss/drop encontrado"
        return False, "no aparece table-miss drop esperado"

    return False, f"match_type no soportado: {flow.match_type}"


def verify_flows_once(
    flows: List[FlowSpec],
    use_sudo: bool,
    artifact_dir: Path,
) -> Dict[str, Any]:
    dump_ovs_flows(TOPOLOGY_BRIDGES, use_sudo=use_sudo, artifact_dir=artifact_dir)

    dumps: Dict[str, str] = {}
    for bridge in TOPOLOGY_BRIDGES:
        dump_path = artifact_dir / f"ovs_dump_{bridge}.txt"
        dumps[bridge] = dump_path.read_text(encoding="utf-8")

    checks: List[Dict[str, Any]] = []
    for flow in flows:
        dump = dumps.get(flow.bridge, "")
        ok, reason = expected_in_dump(flow, dump)
        checks.append({
            "flow_id": flow.flow_id,
            "bridge": flow.bridge,
            "node_id": flow.node_id,
            "match_type": flow.match_type,
            "eth_dst": flow.eth_dst,
            "out_port": flow.out_port,
            "priority": flow.priority,
            "ok": ok,
            "reason": reason,
        })

    n_ok = sum(1 for item in checks if item["ok"])
    summary = {
        "status": "PASS" if n_ok == len(checks) else "FAIL",
        "n_checks": len(checks),
        "n_ok": n_ok,
        "n_fail": len(checks) - n_ok,
        "checks": checks,
    }
    write_json(artifact_dir / "ovs_verification.json", summary)
    return summary


def verify_flows_with_retries(
    flows: List[FlowSpec],
    args: argparse.Namespace,
    artifact_dir: Path,
) -> Dict[str, Any]:
    last: Dict[str, Any] = {}
    for attempt in range(1, args.verify_retries + 1):
        attempt_dir = artifact_dir / f"verification_attempt_{attempt:02d}"
        attempt_dir.mkdir(parents=True, exist_ok=True)
        last = verify_flows_once(flows, use_sudo=args.sudo, artifact_dir=attempt_dir)
        if last["status"] == "PASS":
            write_json(artifact_dir / "ovs_verification_final.json", last)
            return last
        time.sleep(args.verify_sleep)

    write_json(artifact_dir / "ovs_verification_final.json", last)
    return last


def fetch_odl_inventory(args: argparse.Namespace, artifact_dir: Path, suffix: str) -> None:
    url = restconf_data_url(args.odl_host, args.odl_rest_port, "opendaylight-inventory:nodes?content=nonconfig")
    result = http_request("GET", url, args.odl_user, args.odl_pass)
    write_json(artifact_dir / f"odl_inventory_{suffix}_http.json", asdict(result))
    if result.ok and result.response:
        try:
            parsed = json.loads(result.response)
            write_json(artifact_dir / f"odl_inventory_{suffix}.json", parsed)
        except json.JSONDecodeError:
            write_text(artifact_dir / f"odl_inventory_{suffix}.txt", result.response)


def build_report(
    args: argparse.Namespace,
    artifact_dir: Path,
    planned_flows: List[FlowSpec],
    install_summary: Dict[str, Any],
    verification: Dict[str, Any],
    bridge_node_map: Dict[str, Dict[str, str]],
) -> str:
    lines: List[str] = []
    lines.append("FT8.R — Instalación RESTCONF de forwarding determinista")
    lines.append("=" * 68)
    lines.append("")
    lines.append(f"campaign_id: {args.campaign_id}")
    lines.append(f"profile: {args.profile}")
    lines.append(f"restconf_method: {args.restconf_method}")
    lines.append(f"ODL: http://{args.odl_host}:{args.odl_rest_port}")
    lines.append(f"artifact_dir: {artifact_dir}")
    lines.append("")
    lines.append("Resultado instalación RESTCONF")
    lines.append("-" * 32)
    lines.append(f"status: {install_summary['status']}")
    lines.append(f"n_flows: {install_summary['n_flows']}")
    lines.append(f"n_installed_ok: {install_summary['n_installed_ok']}")
    lines.append(f"n_install_failed: {install_summary['n_install_failed']}")
    lines.append("")
    lines.append("Resultado verificación OVS")
    lines.append("-" * 32)
    lines.append(f"status: {verification.get('status', 'SKIPPED')}")
    lines.append(f"n_checks: {verification.get('n_checks', 0)}")
    lines.append(f"n_ok: {verification.get('n_ok', 0)}")
    lines.append(f"n_fail: {verification.get('n_fail', 0)}")
    lines.append("")
    lines.append("Mapa bridge -> node-id")
    lines.append("-" * 32)
    for bridge in TOPOLOGY_BRIDGES:
        item = bridge_node_map.get(bridge, {})
        lines.append(f"{bridge:>2} -> {item.get('node_id', 'MISSING')}  dpid={item.get('datapath_id_hex', 'MISSING')}")
    lines.append("")
    lines.append("Reglas planificadas")
    lines.append("-" * 32)
    for flow in planned_flows:
        if flow.match_type == "eth_dst":
            lines.append(
                f"{flow.bridge:>2} {flow.flow_id:<28} "
                f"prio={flow.priority:<3} dl_dst={flow.eth_dst} -> output:{flow.out_port} "
                f"sources={','.join(flow.sources)}"
            )
        elif flow.match_type == "arp":
            lines.append(f"{flow.bridge:>2} {flow.flow_id:<28} prio={flow.priority:<3} arp -> drop")
        else:
            lines.append(f"{flow.bridge:>2} {flow.flow_id:<28} prio={flow.priority:<3} table-miss -> drop")
    lines.append("")
    lines.append("Lectura metodológica")
    lines.append("-" * 32)
    lines.append(
        "Estas reglas constituyen forwarding determinista instalado mediante "
        "OpenDaylight RESTCONF. No implementan ECMP, balanceo automático ni "
        "reconfiguración dinámica."
    )
    lines.append("")
    return "\n".join(lines)


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    default_root = os.environ.get("TFG_ROOT", str(Path(__file__).resolve().parents[2]))

    parser = argparse.ArgumentParser(
        description="FT8.R - Instalador RESTCONF de forwarding determinista para Fat-Tree k=4."
    )
    parser.add_argument("--tfg-root", default=default_root, help="Ruta raíz del proyecto TFG-SDN.")
    parser.add_argument("--campaign-id", default=now_campaign_id(), help="ID de campaña RESTCONF.")
    parser.add_argument("--profile", choices=["p2p", "t4", "all"], default="all",
                        help="Conjunto de caminos deterministas a instalar.")
    parser.add_argument("--odl-host", default=os.environ.get("ODL_HOST", "127.0.0.1"))
    parser.add_argument("--odl-rest-port", type=int, default=int(os.environ.get("ODL_REST_PORT", "8181")))
    parser.add_argument("--odl-user", default=os.environ.get("ODL_USER", "admin"))
    parser.add_argument("--odl-pass", default=os.environ.get("ODL_PASS", ""))
    parser.add_argument("--restconf-method", choices=["put", "rpc", "auto"], default="auto",
                        help="Método RESTCONF. auto intenta PUT y, si falla, RPC sal-flow:add-flow.")
    parser.add_argument("--no-sudo", dest="sudo", action="store_false",
                        help="No anteponer sudo a comandos ovs-*.")
    parser.set_defaults(sudo=True)
    parser.add_argument("--cleanup-ovs", action="store_true",
                        help="Limpia flows existentes con ovs-ofctl antes de instalar por RESTCONF.")
    parser.add_argument("--no-delete-existing", dest="delete_existing", action="store_false",
                        help="No intenta borrar por RESTCONF flows con los mismos IDs antes de reinstalar.")
    parser.set_defaults(delete_existing=True)
    parser.add_argument("--no-arp-drop", dest="install_arp_drop", action="store_false",
                        help="No instalar reglas ARP drop de base.")
    parser.set_defaults(install_arp_drop=True)
    parser.add_argument("--no-table-miss-drop", dest="install_table_miss_drop", action="store_false",
                        help="No instalar reglas table-miss drop de base.")
    parser.set_defaults(install_table_miss_drop=True)
    parser.add_argument("--skip-ovs-verify", action="store_true",
                        help="Instala por RESTCONF pero no verifica con ovs-ofctl dump-flows.")
    parser.add_argument("--verify-retries", type=int, default=6)
    parser.add_argument("--verify-sleep", type=float, default=1.5)
    parser.add_argument("--dry-run", action="store_true",
                        help="Genera artefactos y planificación, pero no instala reglas.")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)

    tfg_root = Path(args.tfg_root).expanduser().resolve()
    campaign_root = tfg_root / "runs" / "ch08_fattree" / args.campaign_id
    artifact_dir = campaign_root / "restconf_forwarding"
    artifact_dir.mkdir(parents=True, exist_ok=True)

    request_log = artifact_dir / "restconf_requests.jsonl"
    if request_log.exists():
        request_log.unlink()

    preflight: Dict[str, Any] = {
        "campaign_id": args.campaign_id,
        "tfg_root": str(tfg_root),
        "artifact_dir": str(artifact_dir),
        "odl_host": args.odl_host,
        "odl_rest_port": args.odl_rest_port,
        "profile": args.profile,
        "restconf_method": args.restconf_method,
        "dry_run": args.dry_run,
    }

    if not tcp_open(args.odl_host, args.odl_rest_port):
        preflight["odl_restconf_port_open"] = False
        write_json(artifact_dir / "preflight_restconf.json", preflight)
        print(f"[FAIL] RESTCONF no responde en {args.odl_host}:{args.odl_rest_port}", file=sys.stderr)
        return 2

    preflight["odl_restconf_port_open"] = True

    try:
        ovs_bridges = list_ovs_bridges(use_sudo=args.sudo)
    except Exception as exc:
        preflight["ovs_list_bridges_ok"] = False
        preflight["ovs_error"] = str(exc)
        write_json(artifact_dir / "preflight_restconf.json", preflight)
        print(f"[FAIL] No se pudieron listar bridges OVS: {exc}", file=sys.stderr)
        return 3

    missing = [br for br in TOPOLOGY_BRIDGES if br not in ovs_bridges]
    preflight["ovs_bridges_detected"] = ovs_bridges
    preflight["ovs_required_bridges_missing"] = missing
    if missing:
        write_json(artifact_dir / "preflight_restconf.json", preflight)
        print(f"[FAIL] Faltan bridges Fat-Tree k=4 en OVS: {missing}", file=sys.stderr)
        return 4

    bridge_node_map = build_bridge_node_map(TOPOLOGY_BRIDGES, use_sudo=args.sudo)
    preflight["bridge_node_map_ok"] = True
    write_json(artifact_dir / "preflight_restconf.json", preflight)
    write_json(artifact_dir / "bridge_node_map.json", bridge_node_map)

    fetch_odl_inventory(args, artifact_dir, suffix="before")

    base_flows = generate_base_drop_rules(
        bridge_node_map=bridge_node_map,
        install_arp_drop=args.install_arp_drop,
        install_table_miss_drop=args.install_table_miss_drop,
        first_cookie=800000,
    )
    fwd_flows = generate_forward_rules(
        profile=args.profile,
        bridge_node_map=bridge_node_map,
        first_cookie=810000,
    )
    planned_flows = base_flows + fwd_flows

    write_json(artifact_dir / "planned_flows.json", [asdict(flow) for flow in planned_flows])
    write_json(artifact_dir / "selected_paths.json", [
        {"tag": tag, "host_a": host_a, "host_b": host_b, "path": path}
        for tag, host_a, host_b, path in selected_paths(args.profile)
    ])

    if args.cleanup_ovs and not args.dry_run:
        cleanup_ovs_flows(TOPOLOGY_BRIDGES, use_sudo=args.sudo, artifact_dir=artifact_dir)

    install_results: List[Dict[str, Any]] = []

    if args.dry_run:
        install_summary = {
            "status": "DRY_RUN",
            "n_flows": len(planned_flows),
            "n_installed_ok": 0,
            "n_install_failed": 0,
            "results": [],
        }
        verification = {"status": "SKIPPED", "reason": "dry_run"}
    else:
        for flow in planned_flows:
            if args.delete_existing:
                delete_flow_if_present(flow, args, request_log)

            ok, attempts = install_flow(flow, args, request_log)
            install_results.append({
                "flow_id": flow.flow_id,
                "bridge": flow.bridge,
                "node_id": flow.node_id,
                "ok": ok,
                "attempts": attempts,
            })

        n_ok = sum(1 for item in install_results if item["ok"])
        install_summary = {
            "status": "PASS" if n_ok == len(planned_flows) else "FAIL",
            "n_flows": len(planned_flows),
            "n_installed_ok": n_ok,
            "n_install_failed": len(planned_flows) - n_ok,
            "results": install_results,
        }

        fetch_odl_inventory(args, artifact_dir, suffix="after")

        if args.skip_ovs_verify:
            verification = {"status": "SKIPPED", "reason": "skip_ovs_verify"}
        else:
            verification = verify_flows_with_retries(planned_flows, args, artifact_dir)

    write_json(artifact_dir / "restconf_install_summary.json", install_summary)

    manifest = {
        "campaign_id": args.campaign_id,
        "block": "FT8.R",
        "purpose": "RESTCONF deterministic forwarding installation for Fat-Tree k=4",
        "created_at": dt.datetime.now().isoformat(timespec="seconds"),
        "tfg_root": str(tfg_root),
        "campaign_root": str(campaign_root),
        "artifact_dir": str(artifact_dir),
        "profile": args.profile,
        "restconf_method": args.restconf_method,
        "odl": {
            "host": args.odl_host,
            "rest_port": args.odl_rest_port,
            "user": args.odl_user,
        },
        "methodological_note": (
            "Forwarding determinista instalado mediante OpenDaylight RESTCONF. "
            "No implementa ECMP ni reconfiguración dinámica."
        ),
        "artifacts": {
            "preflight": "preflight_restconf.json",
            "bridge_node_map": "bridge_node_map.json",
            "planned_flows": "planned_flows.json",
            "selected_paths": "selected_paths.json",
            "request_log": "restconf_requests.jsonl",
            "install_summary": "restconf_install_summary.json",
            "ovs_verification": "ovs_verification_final.json",
            "report": "install_report.txt",
        },
    }
    write_json(artifact_dir / "install_manifest.json", manifest)

    report = build_report(
        args=args,
        artifact_dir=artifact_dir,
        planned_flows=planned_flows,
        install_summary=install_summary,
        verification=verification,
        bridge_node_map=bridge_node_map,
    )
    write_text(artifact_dir / "install_report.txt", report)

    write_text(
        artifact_dir / "commands_reference.txt",
        "\n".join([
            "# Comandos de referencia para esta campaña",
            f"cd {tfg_root}",
            "sudo -v",
            (
                "python3 tfg_sdn/experiments/ch08_install_restconf_forwarding.py "
                f"--campaign-id {args.campaign_id} --profile {args.profile}"
            ),
            "",
            "# Verificación manual de ejemplo",
            "sudo ovs-ofctl -O OpenFlow13 dump-flows e1",
            "sudo ovs-ofctl -O OpenFlow13 dump-flows a1",
            "sudo ovs-ofctl -O OpenFlow13 dump-flows c1",
            "",
        ])
    )

    print(report)

    if args.dry_run:
        return 0
    if install_summary["status"] != "PASS":
        return 10
    if verification.get("status") not in ("PASS", "SKIPPED"):
        return 11
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

