#!/usr/bin/env python3
"""
FT8.5R — Escenario hotspot RESTCONF para Fat-Tree k=4.

Ruta esperada:
    tfg_sdn/experiments/ch08_restconf_hotspot_tests.py

Función:
    1) Instalar, mediante OpenDaylight RESTCONF, reglas deterministas adicionales
       para un escenario hotspot T5 con varios emisores hacia un mismo destino.
    2) Preparar un archivo de comandos para ejecutar dentro de la CLI de Mininet:
         - ARP estático para los pares hotspot;
         - pings de precheck;
         - servidores iperf3 en h16 en puertos diferentes;
         - clientes concurrentes desde h1, h3, h5 y h7 hacia h16;
         - dumps de flows/contadores al final.
    3) Resumir los artefactos generados en JSON/CSV/TXT.

Escenario T5 propuesto:
    h1 -> h16   por e1-a1-c1-a7-e8
    h3 -> h16   por e2-a2-c4-a8-e8
    h5 -> h16   por e3-a3-c2-a7-e8
    h7 -> h16   por e4-a4-c3-a8-e8

Lectura metodológica:
    - Hay concentración de tráfico en el destino h16 y su edge e8.
    - Los caminos son deterministas y explícitos.
    - No es ECMP, no es balanceo dinámico y no es reconfiguración adaptativa.

Uso recomendado:

    cd $TFG_ROOT

    python3 tfg_sdn/experiments/ch08_restconf_hotspot_tests.py \
      --campaign-id ch08_fattree_restconf_20260519_0957 \
      --action install-forwarding

    python3 tfg_sdn/experiments/ch08_restconf_hotspot_tests.py \
      --campaign-id ch08_fattree_restconf_20260519_0957 \
      --action prepare

    # Dentro de mininet>:
    source $TFG_ROOT/runs/ch08_fattree/ch08_fattree_restconf_20260519_0957/ft8_5r_hotspot_restconf/mininet_ft8_5r_commands.cli

    # Terminal normal:
    python3 tfg_sdn/experiments/ch08_restconf_hotspot_tests.py \
      --campaign-id ch08_fattree_restconf_20260519_0957 \
      --action summarize
"""

from __future__ import annotations

import argparse
import base64
import csv
import datetime as dt
import json
import os
import re
import socket
import statistics
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class HostSpec:
    name: str
    ip: str
    mac: str
    iface: str


@dataclass(frozen=True)
class HotspotFlow:
    flow_id: str
    label: str
    src: str
    dst: str
    port: int
    path: List[str]
    description: str


@dataclass
class FlowSpec:
    flow_id: str
    bridge: str
    node_id: str
    table_id: int
    priority: int
    cookie: int
    eth_dst: str
    out_port: int
    description: str
    sources: List[str]


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


HOSTS: Dict[str, HostSpec] = {
    f"h{i}": HostSpec(
        name=f"h{i}",
        ip=f"10.0.0.{i}",
        mac=f"00:00:00:00:00:{i:02x}",
        iface=f"h{i}-eth1",
    )
    for i in range(1, 17)
}

HOTSPOT_FLOWS: List[HotspotFlow] = [
    HotspotFlow(
        flow_id="t5_h1_h16",
        label="T5 hotspot flow 1",
        src="h1",
        dst="h16",
        port=5501,
        path=["e1", "a1", "c1", "a7", "e8"],
        description="Emisor h1 hacia hotspot h16 por c1.",
    ),
    HotspotFlow(
        flow_id="t5_h3_h16",
        label="T5 hotspot flow 2",
        src="h3",
        dst="h16",
        port=5502,
        path=["e2", "a2", "c4", "a8", "e8"],
        description="Emisor h3 hacia hotspot h16 por c4.",
    ),
    HotspotFlow(
        flow_id="t5_h5_h16",
        label="T5 hotspot flow 3",
        src="h5",
        dst="h16",
        port=5503,
        path=["e3", "a3", "c2", "a7", "e8"],
        description="Emisor h5 hacia hotspot h16 por c2.",
    ),
    HotspotFlow(
        flow_id="t5_h7_h16",
        label="T5 hotspot flow 4",
        src="h7",
        dst="h16",
        port=5504,
        path=["e4", "a4", "c3", "a8", "e8"],
        description="Emisor h7 hacia hotspot h16 por c3.",
    ),
]

TOPOLOGY_BRIDGES: List[str] = [
    "e1", "e2", "e3", "e4", "e5", "e6", "e7", "e8",
    "a1", "a2", "a3", "a4", "a5", "a6", "a7", "a8",
    "c1", "c2", "c3", "c4",
]

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

ARP_DUMP_HOSTS: List[str] = sorted({flow.src for flow in HOTSPOT_FLOWS} | {flow.dst for flow in HOTSPOT_FLOWS})

PING_STATS_RE = re.compile(
    r"(?P<tx>\d+)\s+packets transmitted,\s+"
    r"(?P<rx>\d+)\s+received,\s+"
    r"(?:(?:\+\d+\s+errors,\s+)?)"
    r"(?P<loss>[0-9.]+)%\s+packet loss",
    re.IGNORECASE,
)

RTT_RE = re.compile(
    r"rtt\s+min/avg/max/(?:mdev|stddev)\s+=\s+"
    r"(?P<min>[0-9.]+)/(?P<avg>[0-9.]+)/(?P<max>[0-9.]+)/(?P<mdev>[0-9.]+)\s+ms",
    re.IGNORECASE,
)

PACKET_COUNTER_RE = re.compile(r"n_packets=(?P<packets>\d+)")
BYTE_COUNTER_RE = re.compile(r"n_bytes=(?P<bytes>\d+)")


def default_tfg_root() -> Path:
    return Path(os.environ.get("TFG_ROOT", str(Path(__file__).resolve().parents[2]))).expanduser()


def find_latest_campaign(tfg_root: Path) -> str:
    base = tfg_root / "runs" / "ch08_fattree"
    if not base.exists():
        raise FileNotFoundError(f"No existe el directorio de campañas: {base}")
    candidates = sorted(
        [p.name for p in base.iterdir() if p.is_dir() and p.name.startswith("ch08_fattree_restconf_")],
        reverse=True,
    )
    if not candidates:
        raise FileNotFoundError(f"No se han encontrado campañas RESTCONF en {base}")
    return candidates[0]


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, sort_keys=False), encoding="utf-8")


def write_csv(path: Path, rows: List[Dict[str, Any]], fieldnames: Sequence[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


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
    if check and result.returncode != 0:
        raise RuntimeError(
            f"Comando fallido ({result.returncode}): {' '.join(command)}\n"
            f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )
    return result


def tcp_open(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def basic_auth_header(user: str, password: str) -> str:
    token = base64.b64encode(f"{user}:{password}".encode("utf-8")).decode("ascii")
    return f"Basic {token}"


def http_request(method: str, url: str, user: str, password: str, body: Optional[Dict[str, Any]] = None, timeout: float = 20.0) -> HttpResult:
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
        return HttpResult(method=method.upper(), url=url, status=int(exc.code), ok=False, response=raw, error=str(exc))
    except URLError as exc:
        return HttpResult(method=method.upper(), url=url, status=0, ok=False, response="", error=str(exc))


def restconf_data_url(host: str, port: int, path: str) -> str:
    encoded_path = quote(path.lstrip("/"), safe="/:=,[]'()?&")
    return f"http://{host}:{port}/rests/data/{encoded_path}"


def restconf_ops_url(host: str, port: int, operation: str) -> str:
    encoded_op = quote(operation.lstrip("/"), safe="/:")
    return f"http://{host}:{port}/rests/operations/{encoded_op}"


def get_bridge_dpid_hex(bridge: str, use_sudo: bool) -> str:
    cmd = sudo_prefix(use_sudo) + ["ovs-vsctl", "get", "bridge", bridge, "datapath_id"]
    result = run_cmd(cmd, check=True)
    dpid = result.stdout.strip().strip('"').strip()
    int(dpid, 16)
    return dpid.lower()


def build_bridge_node_map(bridges: Iterable[str], use_sudo: bool) -> Dict[str, Dict[str, str]]:
    mapping: Dict[str, Dict[str, str]] = {}
    for bridge in bridges:
        dpid_hex = get_bridge_dpid_hex(bridge, use_sudo=use_sudo)
        mapping[bridge] = {
            "bridge": bridge,
            "datapath_id_hex": dpid_hex,
            "datapath_id_decimal": str(int(dpid_hex, 16)),
            "node_id": f"openflow:{int(dpid_hex, 16)}",
        }
    return mapping


def host_id(host: str) -> int:
    if not host.startswith("h"):
        raise ValueError(f"Host inválido: {host}")
    return int(host[1:])


def add_or_merge_rule(rules: Dict[Tuple[str, str], Dict[str, Any]], bridge: str, dst_host: str, out_port: int, source_tag: str, description: str) -> None:
    mac = HOSTS[dst_host].mac
    key = (bridge, mac)
    if key in rules:
        previous = rules[key]
        if previous["out_port"] != out_port:
            raise RuntimeError(
                "Conflicto de reglas hotspot: "
                f"bridge={bridge}, dst={dst_host}/{mac}, old_out={previous['out_port']}, new_out={out_port}"
            )
        previous["sources"].append(source_tag)
        return
    rules[key] = {
        "bridge": bridge,
        "dst_host": dst_host,
        "eth_dst": mac,
        "out_port": out_port,
        "sources": [source_tag],
        "description": description,
    }


def generate_hotspot_flows(bridge_node_map: Dict[str, Dict[str, str]], first_cookie: int = 850000) -> List[FlowSpec]:
    rules: Dict[Tuple[str, str], Dict[str, Any]] = {}

    for flow in HOTSPOT_FLOWS:
        path = flow.path
        for idx, bridge in enumerate(path):
            next_hop_to_src = flow.src if idx == 0 else path[idx - 1]
            next_hop_to_dst = flow.dst if idx == len(path) - 1 else path[idx + 1]

            if bridge not in PORTS:
                raise RuntimeError(f"Bridge sin mapa de puertos: {bridge}")
            if next_hop_to_src not in PORTS[bridge]:
                raise RuntimeError(f"Puerto no definido: {bridge} -> {next_hop_to_src} en {flow.flow_id}")
            if next_hop_to_dst not in PORTS[bridge]:
                raise RuntimeError(f"Puerto no definido: {bridge} -> {next_hop_to_dst} en {flow.flow_id}")

            add_or_merge_rule(
                rules=rules,
                bridge=bridge,
                dst_host=flow.src,
                out_port=PORTS[bridge][next_hop_to_src],
                source_tag=flow.flow_id,
                description=f"{bridge}: retorno hacia {flow.src} por {next_hop_to_src}",
            )
            add_or_merge_rule(
                rules=rules,
                bridge=bridge,
                dst_host=flow.dst,
                out_port=PORTS[bridge][next_hop_to_dst],
                source_tag=flow.flow_id,
                description=f"{bridge}: hotspot destino {flow.dst} por {next_hop_to_dst}",
            )

    specs: List[FlowSpec] = []
    cookie = first_cookie
    for item in sorted(rules.values(), key=lambda x: (x["bridge"], host_id(x["dst_host"]))):
        bridge = item["bridge"]
        if bridge not in bridge_node_map:
            raise RuntimeError(f"No existe mapeo bridge->node para {bridge}")
        dst = item["dst_host"]
        specs.append(
            FlowSpec(
                flow_id=f"ft8r_hotspot_{bridge}_dst_{dst}",
                bridge=bridge,
                node_id=bridge_node_map[bridge]["node_id"],
                table_id=0,
                priority=260,
                cookie=cookie,
                eth_dst=item["eth_dst"],
                out_port=int(item["out_port"]),
                description=item["description"],
                sources=sorted(set(item["sources"])),
            )
        )
        cookie += 1
    return specs


def flow_data_path(flow: FlowSpec) -> str:
    return (
        f"opendaylight-inventory:nodes/node={flow.node_id}/"
        f"flow-node-inventory:table={flow.table_id}/"
        f"flow={flow.flow_id}"
    )


def build_flow_object(flow: FlowSpec) -> Dict[str, Any]:
    return {
        "id": flow.flow_id,
        "flow-name": flow.flow_id,
        "table_id": flow.table_id,
        "priority": flow.priority,
        "cookie": flow.cookie,
        "idle-timeout": 0,
        "hard-timeout": 0,
        "match": {
            "ethernet-match": {
                "ethernet-destination": {
                    "address": flow.eth_dst,
                }
            }
        },
        "instructions": {
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
        },
    }


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


def append_jsonl(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(data, ensure_ascii=False, sort_keys=False) + "\n")


def delete_flow_if_present(flow: FlowSpec, args: argparse.Namespace, request_log: Path) -> None:
    url = restconf_data_url(args.odl_host, args.odl_rest_port, flow_data_path(flow))
    result = http_request("DELETE", url, args.odl_user, args.odl_pass)
    append_jsonl(request_log, {
        "phase": "delete_existing_hotspot",
        "flow_id": flow.flow_id,
        "bridge": flow.bridge,
        "node_id": flow.node_id,
        "status": result.status,
        "ok": result.ok or result.status == 404,
        "error": result.error,
        "response": result.response[:1000],
    })


def install_flow(flow: FlowSpec, args: argparse.Namespace, request_log: Path) -> Tuple[bool, List[Dict[str, Any]]]:
    attempts: List[Dict[str, Any]] = []

    def record(kind: str, result: HttpResult, payload: Dict[str, Any]) -> None:
        item = {
            "phase": "install_hotspot",
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
        record("put_namespaced", result_ns, payload_ns)
        if result_ns.ok:
            return True, attempts

        payload_plain = build_put_payload(flow, namespaced=False)
        result_plain = http_request("PUT", url, args.odl_user, args.odl_pass, body=payload_plain)
        record("put_plain", result_plain, payload_plain)
        if result_plain.ok:
            return True, attempts

        if args.restconf_method == "put":
            return False, attempts

    if args.restconf_method in ("rpc", "auto"):
        url = restconf_ops_url(args.odl_host, args.odl_rest_port, "sal-flow:add-flow")
        payload_rpc = build_rpc_payload(flow)
        result_rpc = http_request("POST", url, args.odl_user, args.odl_pass, body=payload_rpc)
        record("rpc_sal_flow_add_flow", result_rpc, payload_rpc)
        if result_rpc.ok:
            return True, attempts

    return False, attempts


def dump_and_verify_hotspot_flows(flows: List[FlowSpec], artifact_dir: Path, use_sudo: bool) -> Dict[str, Any]:
    dump_dir = artifact_dir / "hotspot_forwarding_dumps"
    dump_dir.mkdir(parents=True, exist_ok=True)

    bridges = sorted({flow.bridge for flow in flows})
    dumps: Dict[str, str] = {}
    for bridge in bridges:
        cmd = sudo_prefix(use_sudo) + ["ovs-ofctl", "-O", "OpenFlow13", "dump-flows", bridge]
        result = run_cmd(cmd, check=False)
        content = (
            f"$ {' '.join(cmd)}\n"
            f"returncode={result.returncode}\n\n"
            f"STDOUT:\n{result.stdout}\n\nSTDERR:\n{result.stderr}\n"
        )
        write_text(dump_dir / f"ovs_dump_{bridge}.txt", content)
        dumps[bridge] = result.stdout

    checks: List[Dict[str, Any]] = []
    for flow in flows:
        dump = dumps.get(flow.bridge, "")
        expected_action = f"actions=output:{flow.out_port}"
        ok = (
            f"priority={flow.priority}" in dump
            and f"dl_dst={flow.eth_dst}" in dump
            and (expected_action in dump or f"actions={flow.out_port}" in dump)
        )
        checks.append({
            "flow_id": flow.flow_id,
            "bridge": flow.bridge,
            "eth_dst": flow.eth_dst,
            "out_port": flow.out_port,
            "priority": flow.priority,
            "ok": ok,
        })

    n_ok = sum(1 for item in checks if item["ok"])
    summary = {
        "status": "PASS" if n_ok == len(checks) else "FAIL",
        "n_checks": len(checks),
        "n_ok": n_ok,
        "n_fail": len(checks) - n_ok,
        "checks": checks,
    }
    write_json(artifact_dir / "hotspot_forwarding_ovs_verification.json", summary)
    return summary


def install_hotspot_forwarding(args: argparse.Namespace, artifact_dir: Path) -> Dict[str, Any]:
    if not tcp_open(args.odl_host, args.odl_rest_port):
        raise RuntimeError(f"RESTCONF no responde en {args.odl_host}:{args.odl_rest_port}")

    request_log = artifact_dir / "hotspot_restconf_requests.jsonl"
    if request_log.exists():
        request_log.unlink()

    bridge_node_map = build_bridge_node_map(TOPOLOGY_BRIDGES, use_sudo=args.sudo)
    flows = generate_hotspot_flows(bridge_node_map)

    write_json(artifact_dir / "hotspot_bridge_node_map.json", bridge_node_map)
    write_json(artifact_dir / "hotspot_planned_flows.json", [asdict(flow) for flow in flows])
    write_json(artifact_dir / "hotspot_paths.json", [asdict(flow) for flow in HOTSPOT_FLOWS])

    install_results: List[Dict[str, Any]] = []
    for flow in flows:
        if args.delete_existing:
            delete_flow_if_present(flow, args, request_log)
        ok, attempts = install_flow(flow, args, request_log)
        install_results.append({
            "flow_id": flow.flow_id,
            "bridge": flow.bridge,
            "node_id": flow.node_id,
            "eth_dst": flow.eth_dst,
            "out_port": flow.out_port,
            "priority": flow.priority,
            "ok": ok,
            "attempts": attempts,
        })

    n_ok = sum(1 for item in install_results if item["ok"])
    verification = dump_and_verify_hotspot_flows(flows, artifact_dir=artifact_dir, use_sudo=args.sudo)

    summary = {
        "status": "PASS" if n_ok == len(flows) and verification["status"] == "PASS" else "FAIL",
        "created_at": dt.datetime.now().isoformat(timespec="seconds"),
        "n_flows": len(flows),
        "n_installed_ok": n_ok,
        "n_install_failed": len(flows) - n_ok,
        "ovs_verification_status": verification["status"],
        "results": install_results,
        "methodological_note": (
            "Reglas adicionales T5 instaladas por ODL RESTCONF con prioridad 260. "
            "Estas reglas definen caminos deterministas para el escenario hotspot; no son ECMP."
        ),
    }
    write_json(artifact_dir / "hotspot_forwarding_install_summary.json", summary)
    return summary


def mininet_safe_path(path: Path) -> str:
    return str(path)


def rep_dir(base: Path, rep: int) -> Path:
    return base / f"rep{rep:02d}"


def build_mininet_commands(artifact_dir: Path, ping_count: int, repetitions: int, iperf_seconds: int) -> str:
    lines: List[str] = []
    raw_dir = artifact_dir / "raw"
    flow_dir = artifact_dir / "flow_counter_checks"
    arp_dir = artifact_dir / "arp_checks"
    precheck_dir = artifact_dir / "connectivity_precheck"

    lines.append(f"sh mkdir -p {mininet_safe_path(raw_dir)}")
    lines.append(f"sh mkdir -p {mininet_safe_path(flow_dir)}")
    lines.append(f"sh mkdir -p {mininet_safe_path(arp_dir)}")
    lines.append(f"sh mkdir -p {mininet_safe_path(precheck_dir)}")
    for rep in range(1, repetitions + 1):
        lines.append(f"sh mkdir -p {mininet_safe_path(rep_dir(raw_dir, rep))}")

    for host in ARP_DUMP_HOSTS:
        lines.append(f"{host} pkill -f iperf3 || true")
        lines.append(f"{host} ip neigh flush all")

    for flow in HOTSPOT_FLOWS:
        lines.append(host_peer_arp_command(flow.src, flow.dst))
        lines.append(host_peer_arp_command(flow.dst, flow.src))

    for host in ARP_DUMP_HOSTS:
        lines.append(f"{host} ip neigh show | tee {mininet_safe_path(arp_dir / f'{host}_static_arp.txt')}")

    for flow in HOTSPOT_FLOWS:
        lines.append(
            f"{flow.src} ping -c {ping_count} {flow.dst} | tee "
            f"{mininet_safe_path(precheck_dir / f'{flow.flow_id}_ping.txt')}"
        )

    for rep in range(1, repetitions + 1):
        current_rep_dir = rep_dir(raw_dir, rep)
        rep_manifest = {
            "rep": rep,
            "flows": [asdict(flow) for flow in HOTSPOT_FLOWS],
            "iperf_seconds": iperf_seconds,
            "ping_count": ping_count,
            "methodological_note": "Ensayo hotspot T5 sobre forwarding determinista instalado mediante ODL RESTCONF.",
        }
        lines.append(f"sh printf '%s' '{json.dumps(rep_manifest, ensure_ascii=False)}' > {mininet_safe_path(current_rep_dir / 'rep_manifest.json')}")

        for flow in HOTSPOT_FLOWS:
            lines.append(f"{flow.dst} pkill -f 'iperf3.*-p {flow.port}' || true")

        for flow in HOTSPOT_FLOWS:
            server_out = current_rep_dir / f"{flow.flow_id}_iperf_server.txt"
            server_err = current_rep_dir / f"{flow.flow_id}_iperf_server.stderr.txt"
            lines.append(
                f"{flow.dst} iperf3 -s -1 -p {flow.port} > {mininet_safe_path(server_out)} "
                f"2> {mininet_safe_path(server_err)} &"
            )

        lines.append("sh sleep 1")

        for flow in HOTSPOT_FLOWS:
            ready_file = current_rep_dir / f"{flow.flow_id}_server_ready.txt"
            lines.append(f"{flow.dst} ss -lnt | grep ':{flow.port}' | tee {mininet_safe_path(ready_file)}")

        for flow in HOTSPOT_FLOWS:
            dst_ip = HOSTS[flow.dst].ip
            client_json = current_rep_dir / f"{flow.flow_id}_iperf.json"
            client_err = current_rep_dir / f"{flow.flow_id}_iperf_client.stderr.txt"
            lines.append(
                f"{flow.src} iperf3 -c {dst_ip} -p {flow.port} -t {iperf_seconds} -J "
                f"> {mininet_safe_path(client_json)} 2> {mininet_safe_path(client_err)} &"
            )

        lines.append(f"sh sleep {iperf_seconds + 4}")
        for host in ARP_DUMP_HOSTS:
            lines.append(f"{host} pkill -f iperf3 || true")
        lines.append("sh sleep 1")

    # Evidencia de contadores para todas las reglas T5 instaladas con prioridad 260.
    required_rules = build_required_rule_index()
    for bridge, macs in sorted(required_rules.items()):
        for mac in sorted(macs):
            safe_mac = mac.replace(":", "")
            lines.append(
                f"sh ovs-ofctl -O OpenFlow13 dump-flows {bridge} | grep 'priority=260' | grep '{mac}' | tee "
                f"{mininet_safe_path(flow_dir / f'{bridge}_{safe_mac}.txt')}"
            )

    lines.append("")
    return "\n".join(lines)


def host_peer_arp_command(src: str, dst: str) -> str:
    src_host = HOSTS[src]
    dst_host = HOSTS[dst]
    return (
        f"{src_host.name} ip neigh replace {dst_host.ip} "
        f"lladdr {dst_host.mac} nud permanent dev {src_host.iface}"
    )


def build_required_rule_index() -> Dict[str, List[str]]:
    dummy_map = {bridge: {"node_id": "openflow:0"} for bridge in TOPOLOGY_BRIDGES}
    flows = generate_hotspot_flows(dummy_map)
    index: Dict[str, List[str]] = {}
    for flow in flows:
        index.setdefault(flow.bridge, []).append(flow.eth_dst)
    return index


def prepare(args: argparse.Namespace, tfg_root: Path, campaign_id: str, artifact_dir: Path) -> Dict[str, Any]:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    commands_path = artifact_dir / "mininet_ft8_5r_commands.cli"
    commands = build_mininet_commands(
        artifact_dir=artifact_dir,
        ping_count=args.ping_count,
        repetitions=args.repetitions,
        iperf_seconds=args.iperf_seconds,
    )
    write_text(commands_path, commands)

    manifest = {
        "campaign_id": campaign_id,
        "block": "FT8.5R",
        "purpose": "RESTCONF hotspot tests for Fat-Tree k=4",
        "created_at": dt.datetime.now().isoformat(timespec="seconds"),
        "tfg_root": str(tfg_root),
        "artifact_dir": str(artifact_dir),
        "mininet_commands_file": str(commands_path),
        "ping_count": args.ping_count,
        "iperf_seconds": args.iperf_seconds,
        "repetitions": args.repetitions,
        "flows": [asdict(item) for item in HOTSPOT_FLOWS],
        "methodological_note": (
            "FT8.5R evalúa un hotspot determinista con varios emisores hacia h16. "
            "Las reglas T5 se instalan mediante ODL RESTCONF y no representan ECMP."
        ),
    }
    write_json(artifact_dir / "ft8_5r_manifest.json", manifest)
    return manifest


def parse_ping_output(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {"exists": False, "ok": False, "reason": "missing file"}
    text = path.read_text(encoding="utf-8", errors="replace")
    stats = PING_STATS_RE.search(text)
    rtt = RTT_RE.search(text)
    result: Dict[str, Any] = {
        "exists": True,
        "ok": False,
        "tx": None,
        "rx": None,
        "loss_pct": None,
        "rtt_min_ms": None,
        "rtt_avg_ms": None,
        "rtt_max_ms": None,
        "rtt_mdev_ms": None,
        "reason": None,
    }
    if not stats:
        result["reason"] = "ping statistics not found"
        return result
    tx = int(stats.group("tx"))
    rx = int(stats.group("rx"))
    loss = float(stats.group("loss"))
    result.update({"tx": tx, "rx": rx, "loss_pct": loss})
    if rtt:
        result.update({
            "rtt_min_ms": float(rtt.group("min")),
            "rtt_avg_ms": float(rtt.group("avg")),
            "rtt_max_ms": float(rtt.group("max")),
            "rtt_mdev_ms": float(rtt.group("mdev")),
        })
    if tx > 0 and rx == tx and loss == 0.0:
        result["ok"] = True
        result["reason"] = "0% packet loss"
    else:
        result["reason"] = f"tx={tx}, rx={rx}, loss={loss}%"
    return result


def safe_get(data: Dict[str, Any], path: Sequence[Any], default: Any = None) -> Any:
    cur: Any = data
    for key in path:
        try:
            if isinstance(cur, dict):
                cur = cur[key]
            elif isinstance(cur, list) and isinstance(key, int):
                cur = cur[key]
            else:
                return default
        except (KeyError, IndexError, TypeError):
            return default
    return cur


def parse_iperf_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {"exists": False, "json_valid": False, "ok": False, "reason": "missing file"}
    if path.stat().st_size == 0:
        return {"exists": True, "json_valid": False, "ok": False, "reason": "empty file"}
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except json.JSONDecodeError as exc:
        return {"exists": True, "json_valid": False, "ok": False, "reason": f"json decode error: {exc}"}
    error = data.get("error")
    if error:
        return {"exists": True, "json_valid": True, "ok": False, "reason": f"iperf error: {error}"}

    receiver_bps = safe_get(data, ["end", "sum_received", "bits_per_second"])
    if receiver_bps is None:
        receiver_bps = safe_get(data, ["end", "sum", "bits_per_second"])
    if receiver_bps is None:
        receiver_bps = safe_get(data, ["end", "streams", 0, "receiver", "bits_per_second"])

    sender_bps = safe_get(data, ["end", "sum_sent", "bits_per_second"])
    bytes_received = safe_get(data, ["end", "sum_received", "bytes"])
    retransmits = safe_get(data, ["end", "sum_sent", "retransmits"])
    seconds = safe_get(data, ["end", "sum_received", "seconds"])
    if seconds is None:
        seconds = safe_get(data, ["end", "sum", "seconds"])

    ok = receiver_bps is not None and float(receiver_bps) > 0
    return {
        "exists": True,
        "json_valid": True,
        "ok": ok,
        "receiver_bps": float(receiver_bps) if receiver_bps is not None else None,
        "receiver_mbps": float(receiver_bps) / 1e6 if receiver_bps is not None else None,
        "sender_bps": float(sender_bps) if sender_bps is not None else None,
        "sender_mbps": float(sender_bps) / 1e6 if sender_bps is not None else None,
        "bytes_received": int(bytes_received) if bytes_received is not None else None,
        "retransmits": int(retransmits) if retransmits is not None else None,
        "seconds": float(seconds) if seconds is not None else None,
        "reason": "valid throughput" if ok else "missing throughput",
    }


def parse_flow_file(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {"exists": False, "non_empty": False, "packets": None, "bytes": None, "ok": False}
    text = path.read_text(encoding="utf-8", errors="replace")
    packets_match = PACKET_COUNTER_RE.search(text)
    bytes_match = BYTE_COUNTER_RE.search(text)
    packets = int(packets_match.group("packets")) if packets_match else None
    byte_count = int(bytes_match.group("bytes")) if bytes_match else None
    return {
        "exists": True,
        "non_empty": bool(text.strip()),
        "packets": packets,
        "bytes": byte_count,
        "ok": packets is not None and packets > 0,
    }


def summarize(artifact_dir: Path) -> Dict[str, Any]:
    raw_dir = artifact_dir / "raw"
    flow_dir = artifact_dir / "flow_counter_checks"
    precheck_dir = artifact_dir / "connectivity_precheck"

    precheck_rows: List[Dict[str, Any]] = []
    for flow in HOTSPOT_FLOWS:
        path = precheck_dir / f"{flow.flow_id}_ping.txt"
        ping = parse_ping_output(path)
        precheck_rows.append({
            "flow_id": flow.flow_id,
            "src": flow.src,
            "dst": flow.dst,
            "path": "-".join(flow.path),
            "ping_file": str(path),
            **ping,
        })

    rows: List[Dict[str, Any]] = []
    rep_dirs = sorted([p for p in raw_dir.glob("rep*") if p.is_dir()])
    for current_rep_dir in rep_dirs:
        rep_match = re.search(r"rep(\d+)$", current_rep_dir.name)
        rep = int(rep_match.group(1)) if rep_match else None
        for flow in HOTSPOT_FLOWS:
            iperf_path = current_rep_dir / f"{flow.flow_id}_iperf.json"
            server_out = current_rep_dir / f"{flow.flow_id}_iperf_server.txt"
            server_err = current_rep_dir / f"{flow.flow_id}_iperf_server.stderr.txt"
            client_err = current_rep_dir / f"{flow.flow_id}_iperf_client.stderr.txt"
            ready_file = current_rep_dir / f"{flow.flow_id}_server_ready.txt"
            iperf = parse_iperf_json(iperf_path)
            rows.append({
                "rep": rep,
                "flow_id": flow.flow_id,
                "label": flow.label,
                "src": flow.src,
                "dst": flow.dst,
                "port": flow.port,
                "path": "-".join(flow.path),
                "iperf_ok": iperf.get("ok"),
                "iperf_json_valid": iperf.get("json_valid"),
                "receiver_mbps": iperf.get("receiver_mbps"),
                "sender_mbps": iperf.get("sender_mbps"),
                "bytes_received": iperf.get("bytes_received"),
                "retransmits": iperf.get("retransmits"),
                "seconds": iperf.get("seconds"),
                "reason": iperf.get("reason"),
                "iperf_file": str(iperf_path),
                "server_ready_exists": ready_file.exists(),
                "server_ready_non_empty": ready_file.exists() and ready_file.stat().st_size > 0,
                "server_out_exists": server_out.exists(),
                "server_err_exists": server_err.exists(),
                "client_err_exists": client_err.exists(),
                "ok": bool(iperf.get("ok")),
            })

    flow_rows: List[Dict[str, Any]] = []
    required_rules = build_required_rule_index()
    for bridge, macs in sorted(required_rules.items()):
        for mac in sorted(macs):
            safe_mac = mac.replace(":", "")
            path = flow_dir / f"{bridge}_{safe_mac}.txt"
            parsed = parse_flow_file(path)
            flow_rows.append({
                "bridge": bridge,
                "mac": mac,
                "file": str(path),
                **parsed,
            })

    if not rows:
        summary = {
            "status": "FAIL",
            "reason": "no hotspot iperf results found",
            "artifact_dir": str(artifact_dir),
        }
        write_json(artifact_dir / "ft8_5r_summary.json", summary)
        return summary

    rep_summaries: List[Dict[str, Any]] = []
    for rep in sorted({row["rep"] for row in rows if row["rep"] is not None}):
        subset = [row for row in rows if row["rep"] == rep]
        mbps_values = [row["receiver_mbps"] for row in subset if row["receiver_mbps"] is not None]
        rep_summaries.append({
            "rep": rep,
            "n_flows": len(subset),
            "n_ok": sum(1 for row in subset if row["ok"]),
            "aggregate_receiver_mbps": sum(mbps_values),
            "mean_flow_receiver_mbps": statistics.mean(mbps_values) if mbps_values else None,
            "min_flow_receiver_mbps": min(mbps_values) if mbps_values else None,
            "max_flow_receiver_mbps": max(mbps_values) if mbps_values else None,
        })

    flow_summaries: List[Dict[str, Any]] = []
    for flow in HOTSPOT_FLOWS:
        subset = [row for row in rows if row["flow_id"] == flow.flow_id]
        mbps_values = [row["receiver_mbps"] for row in subset if row["receiver_mbps"] is not None]
        flow_summaries.append({
            "flow_id": flow.flow_id,
            "src": flow.src,
            "dst": flow.dst,
            "path": "-".join(flow.path),
            "n_repetitions": len(subset),
            "n_ok": sum(1 for row in subset if row["ok"]),
            "throughput_mean_mbps": statistics.mean(mbps_values) if mbps_values else None,
            "throughput_median_mbps": statistics.median(mbps_values) if mbps_values else None,
            "throughput_min_mbps": min(mbps_values) if mbps_values else None,
            "throughput_max_mbps": max(mbps_values) if mbps_values else None,
        })

    n_ok = sum(1 for row in rows if row["ok"])
    precheck_ok = all(row["ok"] for row in precheck_rows)
    flow_counter_evidence_ok = all(row["ok"] for row in flow_rows)
    status = "PASS" if n_ok == len(rows) and precheck_ok and flow_counter_evidence_ok else "FAIL"

    summary = {
        "status": status,
        "created_at": dt.datetime.now().isoformat(timespec="seconds"),
        "n_results": len(rows),
        "n_ok": n_ok,
        "n_fail": len(rows) - n_ok,
        "precheck_ok": precheck_ok,
        "flow_counter_evidence_ok": flow_counter_evidence_ok,
        "precheck_results": precheck_rows,
        "results": rows,
        "rep_summaries": rep_summaries,
        "flow_summaries": flow_summaries,
        "flow_evidence": flow_rows,
        "methodological_note": (
            "FT8.5R contiene un escenario hotspot con varios emisores hacia h16 sobre "
            "forwarding determinista instalado mediante ODL RESTCONF. No representa ECMP, "
            "balanceo automático ni reconfiguración dinámica."
        ),
    }

    write_json(artifact_dir / "ft8_5r_summary.json", summary)
    write_csv(
        artifact_dir / "ft8_5r_results.csv",
        rows,
        fieldnames=[
            "rep", "flow_id", "label", "src", "dst", "port", "path",
            "iperf_ok", "iperf_json_valid", "receiver_mbps", "sender_mbps", "bytes_received",
            "retransmits", "seconds", "reason", "ok", "server_ready_exists", "server_ready_non_empty",
            "server_out_exists", "server_err_exists", "client_err_exists", "iperf_file",
        ],
    )
    write_csv(
        artifact_dir / "ft8_5r_rep_summary.csv",
        rep_summaries,
        fieldnames=[
            "rep", "n_flows", "n_ok", "aggregate_receiver_mbps", "mean_flow_receiver_mbps",
            "min_flow_receiver_mbps", "max_flow_receiver_mbps",
        ],
    )
    write_csv(
        artifact_dir / "ft8_5r_flow_summary.csv",
        flow_summaries,
        fieldnames=[
            "flow_id", "src", "dst", "path", "n_repetitions", "n_ok",
            "throughput_mean_mbps", "throughput_median_mbps", "throughput_min_mbps", "throughput_max_mbps",
        ],
    )
    write_csv(
        artifact_dir / "ft8_5r_precheck_summary.csv",
        precheck_rows,
        fieldnames=[
            "flow_id", "src", "dst", "path", "exists", "ok", "tx", "rx", "loss_pct",
            "rtt_min_ms", "rtt_avg_ms", "rtt_max_ms", "rtt_mdev_ms", "reason", "ping_file",
        ],
    )
    write_csv(
        artifact_dir / "ft8_5r_flow_evidence.csv",
        flow_rows,
        fieldnames=["bridge", "mac", "file", "exists", "non_empty", "packets", "bytes", "ok"],
    )

    report_lines: List[str] = []
    report_lines.append("FT8.5R — Escenario hotspot RESTCONF")
    report_lines.append("=" * 64)
    report_lines.append("")
    report_lines.append(f"status: {status}")
    report_lines.append(f"n_results: {len(rows)}")
    report_lines.append(f"n_ok: {n_ok}")
    report_lines.append(f"n_fail: {len(rows) - n_ok}")
    report_lines.append(f"precheck_ok: {precheck_ok}")
    report_lines.append(f"flow_counter_evidence_ok: {flow_counter_evidence_ok}")
    report_lines.append("")
    report_lines.append("Resumen por repetición")
    report_lines.append("-" * 64)
    for item in rep_summaries:
        report_lines.append(
            f"rep{int(item['rep']):02d} ok={item['n_ok']}/{item['n_flows']} "
            f"aggregate_receiver_mbps={item['aggregate_receiver_mbps']} "
            f"mean_flow_receiver_mbps={item['mean_flow_receiver_mbps']}"
        )
    report_lines.append("")
    report_lines.append("Resumen por flujo")
    report_lines.append("-" * 64)
    for item in flow_summaries:
        report_lines.append(
            f"{item['flow_id']:<12} {item['src']:>3}->{item['dst']:<3} "
            f"ok={item['n_ok']}/{item['n_repetitions']} "
            f"thr_mean={item['throughput_mean_mbps']} Mbps"
        )
    report_lines.append("")
    report_lines.append("Lectura metodológica")
    report_lines.append("-" * 64)
    report_lines.append(summary["methodological_note"])
    report_lines.append("")
    write_text(artifact_dir / "ft8_5r_report.txt", "\n".join(report_lines))

    return summary


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="FT8.5R - hotspot RESTCONF para Fat-Tree k=4.")
    parser.add_argument("--tfg-root", default=str(default_tfg_root()))
    parser.add_argument("--campaign-id", default=None,
                        help="ID de campaña. Si se omite, se usa la última ch08_fattree_restconf_*.")
    parser.add_argument("--action", choices=["install-forwarding", "prepare", "summarize", "setup"], default="prepare")
    parser.add_argument("--odl-host", default=os.environ.get("ODL_HOST", "127.0.0.1"))
    parser.add_argument("--odl-rest-port", type=int, default=int(os.environ.get("ODL_REST_PORT", "8181")))
    parser.add_argument("--odl-user", default=os.environ.get("ODL_USER", "admin"))
    parser.add_argument("--odl-pass", default=os.environ.get("ODL_PASS", ""))
    parser.add_argument("--restconf-method", choices=["put", "rpc", "auto"], default="auto")
    parser.add_argument("--no-delete-existing", dest="delete_existing", action="store_false")
    parser.set_defaults(delete_existing=True)
    parser.add_argument("--no-sudo", dest="sudo", action="store_false")
    parser.set_defaults(sudo=True)
    parser.add_argument("--ping-count", type=int, default=10)
    parser.add_argument("--iperf-seconds", type=int, default=10)
    parser.add_argument("--repetitions", type=int, default=3)
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    tfg_root = Path(args.tfg_root).expanduser().resolve()
    campaign_id = args.campaign_id or find_latest_campaign(tfg_root)
    campaign_root = tfg_root / "runs" / "ch08_fattree" / campaign_id
    artifact_dir = campaign_root / "ft8_5r_hotspot_restconf"

    if not campaign_root.exists():
        print(f"[FAIL] No existe la campaña: {campaign_root}", file=sys.stderr)
        return 2

    if args.action in ("install-forwarding", "setup"):
        artifact_dir.mkdir(parents=True, exist_ok=True)
        summary = install_hotspot_forwarding(args, artifact_dir)
        print("FT8.5R hotspot forwarding:", summary["status"])
        print(f"n_installed_ok: {summary['n_installed_ok']}/{summary['n_flows']}")
        print(f"ovs_verification_status: {summary['ovs_verification_status']}")
        if summary["status"] != "PASS":
            return 10

    if args.action in ("prepare", "setup"):
        manifest = prepare(args, tfg_root, campaign_id, artifact_dir)
        print("FT8.5R prepare: OK")
        print(f"campaign_id: {campaign_id}")
        print(f"artifact_dir: {artifact_dir}")
        print("Ejecuta dentro de Mininet:")
        print(f"source {manifest['mininet_commands_file']}")

    if args.action == "summarize":
        summary = summarize(artifact_dir)
        print("FT8.5R summarize:", summary["status"])
        print(f"n_ok: {summary.get('n_ok')}/{summary.get('n_results')}")
        print(f"precheck_ok: {summary.get('precheck_ok')}")
        print(f"flow_counter_evidence_ok: {summary.get('flow_counter_evidence_ok')}")
        print(f"report: {artifact_dir / 'ft8_5r_report.txt'}")
        return 0 if summary["status"] == "PASS" else 11

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

