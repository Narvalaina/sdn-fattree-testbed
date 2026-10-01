#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import socket
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------
# Configuración general
# ---------------------------------------------------------------------

DEFAULT_ODL_HOST = "127.0.0.1"
DEFAULT_ODL_RESTCONF_PORT = 8181
DEFAULT_ODL_OPENFLOW_PORT = 6653
DEFAULT_ODL_USER = "admin"

EXPECTED_FATTREE_K4_SWITCHES = 20
EXPECTED_FATTREE_K4_HOSTS = 16

REPRESENTATIVE_PINGS = [
    ("T1_local", "h1", "10.0.0.2"),
    ("T2_intrapod", "h1", "10.0.0.3"),
    ("T3_interpod", "h1", "10.0.0.16"),
]


# ---------------------------------------------------------------------
# Utilidades básicas
# ---------------------------------------------------------------------

def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def default_campaign_id() -> str:
    return datetime.now().strftime("ch08_fattree_%Y%m%d_%H%M")


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def write_text(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def write_json(path: Path, data: Any) -> None:
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def run_cmd(
    cmd: list[str],
    *,
    timeout: int = 20,
    shell: bool = False,
) -> dict[str, Any]:
    try:
        if shell:
            completed = subprocess.run(
                " ".join(cmd),
                shell=True,
                check=False,
                text=True,
                capture_output=True,
                timeout=timeout,
            )
        else:
            completed = subprocess.run(
                cmd,
                check=False,
                text=True,
                capture_output=True,
                timeout=timeout,
            )

        return {
            "cmd": cmd if not shell else " ".join(cmd),
            "returncode": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "timeout": False,
        }

    except subprocess.TimeoutExpired as exc:
        return {
            "cmd": cmd if not shell else " ".join(cmd),
            "returncode": None,
            "stdout": exc.stdout or "",
            "stderr": exc.stderr or "",
            "timeout": True,
        }

    except FileNotFoundError as exc:
        return {
            "cmd": cmd if not shell else " ".join(cmd),
            "returncode": None,
            "stdout": "",
            "stderr": str(exc),
            "timeout": False,
        }


def port_open(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


# ---------------------------------------------------------------------
# RESTCONF OpenDaylight
# ---------------------------------------------------------------------

def restconf_get(
    *,
    host: str,
    port: int,
    path: str,
    user: str,
    password: str,
    timeout: int = 15,
) -> tuple[bool, dict[str, Any] | str]:
    import urllib.error
    import urllib.request

    url = f"http://{host}:{port}{path}"

    token = base64.b64encode(f"{user}:{password}".encode("utf-8")).decode("ascii")

    request = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Basic {token}",
            "Accept": "application/json",
        },
        method="GET",
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
            try:
                return True, json.loads(body)
            except json.JSONDecodeError:
                return False, body

    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        return False, {
            "error": "HTTPError",
            "status": exc.code,
            "reason": exc.reason,
            "body": body,
            "url": url,
        }

    except Exception as exc:
        return False, {
            "error": type(exc).__name__,
            "message": str(exc),
            "url": url,
        }


def extract_openflow_node_ids(inventory: dict[str, Any]) -> list[str]:
    """Extract OpenFlow switch node IDs from an OpenDaylight inventory reply.

    OpenDaylight RESTCONF may return the inventory under either a plain key
    such as 'nodes' or a namespaced key such as 'opendaylight-inventory:nodes'.
    This helper accepts both forms and deliberately keeps only switch node IDs
    of the form 'openflow:<dpid>', excluding connector IDs such as
    'openflow:<dpid>:<port>'.
    """
    containers = []

    for key in ("nodes", "opendaylight-inventory:nodes"):
        value = inventory.get(key)
        if isinstance(value, dict):
            containers.append(value)

    ids: list[str] = []

    for container in containers:
        node_list = (
            container.get("node")
            or container.get("opendaylight-inventory:node")
            or []
        )

        if isinstance(node_list, dict):
            node_list = [node_list]

        for node in node_list:
            if not isinstance(node, dict):
                continue

            node_id = str(node.get("id") or node.get("node-id") or "")

            if not node_id.startswith("openflow:"):
                continue

            # Keep only switch IDs such as openflow:1.
            # Exclude connector IDs such as openflow:1:LOCAL or openflow:1:2.
            if len(node_id.split(":")) == 2:
                ids.append(node_id)

    return sorted(set(ids))


# ---------------------------------------------------------------------
# OVS y Mininet
# ---------------------------------------------------------------------

def collect_ovs_state(preflight_dir: Path) -> dict[str, Any]:
    ovs_state: dict[str, Any] = {}

    commands = {
        "ovs_vsctl_show.txt": ["ovs-vsctl", "show"],
        "ovs_vsctl_list_bridge.txt": ["ovs-vsctl", "list", "bridge"],
        "ovs_vsctl_list_controller.txt": ["ovs-vsctl", "list", "controller"],
        "ovs_ofctl_version.txt": ["ovs-ofctl", "--version"],
        "ovs_vsctl_version.txt": ["ovs-vsctl", "--version"],
    }

    for filename, cmd in commands.items():
        result = run_cmd(cmd, timeout=20)
        write_text(
            preflight_dir / filename,
            format_command_result(result),
        )
        ovs_state[filename] = {
            "returncode": result["returncode"],
            "timeout": result["timeout"],
        }

    bridge_result = run_cmd(["ovs-vsctl", "list-br"], timeout=20)
    write_text(
        preflight_dir / "ovs_bridges.txt",
        format_command_result(bridge_result),
    )

    bridges = [
        line.strip()
        for line in bridge_result["stdout"].splitlines()
        if line.strip()
    ]

    ovs_state["bridges"] = bridges
    ovs_state["bridge_count"] = len(bridges)

    flows_sections: list[str] = []
    show_sections: list[str] = []

    for bridge in bridges:
        flows_sections.append(f"\n\n===== {bridge}: dump-flows OpenFlow13 =====\n")
        flow_result = run_cmd(
            ["ovs-ofctl", "-O", "OpenFlow13", "dump-flows", bridge],
            timeout=20,
        )
        flows_sections.append(format_command_result(flow_result))

        show_sections.append(f"\n\n===== {bridge}: ovs-ofctl show OpenFlow13 =====\n")
        show_result = run_cmd(
            ["ovs-ofctl", "-O", "OpenFlow13", "show", bridge],
            timeout=20,
        )
        show_sections.append(format_command_result(show_result))

    write_text(preflight_dir / "flows_before_tests.txt", "".join(flows_sections))
    write_text(preflight_dir / "ovs_ports_before_tests.txt", "".join(show_sections))

    ovs_state["flows_dump_created"] = True
    ovs_state["ports_dump_created"] = True

    return ovs_state


def find_mininet_host_pids() -> dict[str, int]:
    result = run_cmd(["ps", "-eo", "pid,args"], timeout=20)
    host_pids: dict[str, int] = {}

    for line in result["stdout"].splitlines():
        # Ejemplos habituales:
        # 12345 bash --norc -is mininet:h1
        # 12345 /bin/bash --norc -is mininet:h1
        match = re.search(r"^\s*(?P<pid>\d+)\s+.*mininet:(?P<host>h\d+)\b", line)
        if match:
            host = match.group("host")
            pid = int(match.group("pid"))
            host_pids[host] = pid

    return dict(sorted(host_pids.items(), key=lambda item: natural_host_key(item[0])))


def natural_host_key(host: str) -> int:
    match = re.search(r"\d+", host)
    return int(match.group(0)) if match else 0


def run_representative_pings(
    preflight_dir: Path,
    host_pids: dict[str, int],
) -> dict[str, Any]:
    connectivity_report: dict[str, Any] = {
        "ping_checks_attempted": True,
        "checks": [],
    }

    lines: list[str] = []
    lines.append("Representative connectivity checks using mnexec host namespaces\n")
    lines.append(f"Generated at: {now_iso()}\n\n")

    for check_id, src_host, dst_ip in REPRESENTATIVE_PINGS:
        lines.append(f"===== {check_id}: {src_host} -> {dst_ip} =====\n")

        if src_host not in host_pids:
            msg = f"Host PID not found for {src_host}. Ping not executed."
            lines.append(msg + "\n\n")
            connectivity_report["checks"].append(
                {
                    "check_id": check_id,
                    "src_host": src_host,
                    "dst_ip": dst_ip,
                    "executed": False,
                    "success": False,
                    "reason": "host_pid_not_found",
                }
            )
            continue

        pid = host_pids[src_host]
        cmd = ["mnexec", "-a", str(pid), "ping", "-c", "3", "-W", "1", dst_ip]
        result = run_cmd(cmd, timeout=15)
        lines.append(format_command_result(result))
        lines.append("\n")

        success = result["returncode"] == 0
        connectivity_report["checks"].append(
            {
                "check_id": check_id,
                "src_host": src_host,
                "src_pid": pid,
                "dst_ip": dst_ip,
                "executed": True,
                "success": success,
                "returncode": result["returncode"],
            }
        )

    write_text(preflight_dir / "connectivity_check.txt", "".join(lines))

    connectivity_report["all_success"] = all(
        item.get("success") is True
        for item in connectivity_report["checks"]
    ) if connectivity_report["checks"] else False

    return connectivity_report


def format_command_result(result: dict[str, Any]) -> str:
    cmd = result["cmd"]
    if isinstance(cmd, list):
        cmd_str = " ".join(cmd)
    else:
        cmd_str = str(cmd)

    return (
        f"$ {cmd_str}\n"
        f"returncode: {result['returncode']}\n"
        f"timeout: {result['timeout']}\n\n"
        f"[stdout]\n{result['stdout']}\n\n"
        f"[stderr]\n{result['stderr']}\n"
    )


# ---------------------------------------------------------------------
# Manifest y doctor
# ---------------------------------------------------------------------

def create_campaign_manifest(
    *,
    campaign_id: str,
    root: Path,
    run_dir: Path,
    raw_dir: Path,
    args: argparse.Namespace,
) -> dict[str, Any]:
    return {
        "manifest_version": "1.0",
        "campaign_id": campaign_id,
        "campaign_status": "preflight_started",
        "created_at": now_iso(),
        "project": {
            "repository_root": str(root),
            "chapter": "Capítulo 8 - Resultados experimentales",
        },
        "topology": {
            "type": "Fat-Tree",
            "k": args.k,
            "expected_switches": args.expected_switches,
            "expected_hosts": args.expected_hosts,
        },
        "sdn_environment": {
            "emulator": "Mininet",
            "datapath": "Open vSwitch",
            "controller": "OpenDaylight",
            "openflow_version": "OpenFlow 1.3",
            "odl_host": args.odl_host,
            "odl_restconf_port": args.odl_restconf_port,
            "odl_openflow_port": args.odl_openflow_port,
            "odl_user": args.odl_user,
        },
        "paths": {
            "run_dir": str(run_dir.relative_to(root)),
            "preflight_dir": str((run_dir / "preflight").relative_to(root)),
            "raw_dir": str(raw_dir.relative_to(root)),
            "processed_dir": "data/processed/ch08_fattree",
            "figures_dir": "figures/ch08_resultados",
            "tables_dir": "tables/ch08",
        },
        "notes": [
            "This manifest is created by ch08_preflight_collect.py.",
            "No performance results are stored in this manifest.",
            "Chapter 8 must only use results supported by raw artifacts and processed CSV files.",
        ],
    }


def build_doctor_report(
    *,
    args: argparse.Namespace,
    restconf_available: bool,
    openflow_port_available: bool,
    inventory_ok: bool,
    topology_ok: bool,
    openflow_nodes: list[str],
    ovs_state: dict[str, Any],
    host_pids: dict[str, int],
    connectivity_report: dict[str, Any],
) -> dict[str, Any]:
    checks: dict[str, Any] = {}

    checks["odl_restconf_port_open"] = {
        "status": restconf_available,
        "expected": True,
        "detail": f"{args.odl_host}:{args.odl_restconf_port}",
    }

    checks["odl_openflow_port_open"] = {
        "status": openflow_port_available,
        "expected": True,
        "detail": f"{args.odl_host}:{args.odl_openflow_port}",
    }

    checks["odl_inventory_retrieved"] = {
        "status": inventory_ok,
        "expected": True,
    }

    checks["odl_topology_retrieved"] = {
        "status": topology_ok,
        "expected": True,
    }

    checks["openflow_nodes_count"] = {
        "status": len(openflow_nodes) >= args.expected_switches,
        "expected_min": args.expected_switches,
        "observed": len(openflow_nodes),
        "nodes": openflow_nodes,
    }

    checks["ovs_bridge_count"] = {
        "status": int(ovs_state.get("bridge_count", 0)) >= args.expected_switches,
        "expected_min": args.expected_switches,
        "observed": ovs_state.get("bridge_count", 0),
        "bridges": ovs_state.get("bridges", []),
    }

    checks["mininet_host_process_count"] = {
        "status": len(host_pids) >= args.expected_hosts,
        "expected_min": args.expected_hosts,
        "observed": len(host_pids),
        "hosts": host_pids,
    }

    checks["representative_connectivity"] = {
        "status": connectivity_report.get("all_success", False),
        "expected": True,
        "checks": connectivity_report.get("checks", []),
    }

    overall_pass = all(item["status"] is True for item in checks.values())

    return {
        "generated_at": now_iso(),
        "campaign_id": args.campaign_id,
        "overall_status": "PASS" if overall_pass else "FAIL",
        "checks": checks,
        "interpretation": (
            "Preflight validado. Se pueden ejecutar ensayos de rendimiento."
            if overall_pass
            else "Preflight no validado. Revisar los artefactos antes de ejecutar rendimiento."
        ),
    }


def render_doctor_txt(report: dict[str, Any]) -> str:
    lines: list[str] = []

    lines.append("FT8.2 — Validación previa de campaña Fat-Tree\n")
    lines.append(f"Generated at: {report['generated_at']}\n")
    lines.append(f"Campaign ID: {report['campaign_id']}\n")
    lines.append(f"Overall status: {report['overall_status']}\n\n")

    for name, check in report["checks"].items():
        status = "PASS" if check["status"] else "FAIL"
        lines.append(f"[{status}] {name}\n")

        for key, value in check.items():
            if key == "status":
                continue
            rendered = json.dumps(value, ensure_ascii=False)
            if len(rendered) > 500:
                rendered = rendered[:500] + " ...[truncated]"
            lines.append(f"  - {key}: {rendered}\n")

        lines.append("\n")

    lines.append("Interpretation:\n")
    lines.append(report["interpretation"] + "\n")

    return "".join(lines)


# ---------------------------------------------------------------------
# Argumentos
# ---------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Genera artefactos de validación previa para la campaña experimental "
            "Fat-Tree k=4 del Capítulo 8."
        )
    )

    parser.add_argument(
        "--campaign-id",
        default=default_campaign_id(),
        help="Identificador de campaña. Por defecto: ch08_fattree_YYYYMMDD_HHMM.",
    )

    parser.add_argument("--k", type=int, default=4)
    parser.add_argument("--expected-switches", type=int, default=EXPECTED_FATTREE_K4_SWITCHES)
    parser.add_argument("--expected-hosts", type=int, default=EXPECTED_FATTREE_K4_HOSTS)

    parser.add_argument("--odl-host", default=DEFAULT_ODL_HOST)
    parser.add_argument("--odl-restconf-port", type=int, default=DEFAULT_ODL_RESTCONF_PORT)
    parser.add_argument("--odl-openflow-port", type=int, default=DEFAULT_ODL_OPENFLOW_PORT)
    parser.add_argument("--odl-user", default=os.getenv("ODL_USER", DEFAULT_ODL_USER))
    parser.add_argument(
        "--odl-pass",
        default=os.getenv("ODL_PASS", ""),
        help=(
            "Contraseña RESTCONF de OpenDaylight. "
            "También puede pasarse mediante la variable de entorno ODL_PASS."
        ),
    )

    parser.add_argument(
        "--skip-ping-checks",
        action="store_true",
        help="No ejecutar pings representativos con mnexec.",
    )

    return parser.parse_args()


# ---------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------

def main() -> int:
    args = parse_args()
    args.campaign_id = args.campaign_id.strip()

    root = repo_root()

    run_dir = root / "runs" / "ch08_fattree" / args.campaign_id
    preflight_dir = run_dir / "preflight"
    raw_dir = root / "data" / "raw" / "ch08_fattree" / args.campaign_id

    ensure_dir(preflight_dir)
    ensure_dir(raw_dir)
    ensure_dir(run_dir / "notes")
    ensure_dir(root / "data" / "processed" / "ch08_fattree")
    ensure_dir(root / "figures" / "ch08_resultados")
    ensure_dir(root / "tables" / "ch08")

    manifest = create_campaign_manifest(
        campaign_id=args.campaign_id,
        root=root,
        run_dir=run_dir,
        raw_dir=raw_dir,
        args=args,
    )
    write_json(run_dir / "campaign_manifest.json", manifest)

    # Puertos básicos
    restconf_available = port_open(args.odl_host, args.odl_restconf_port)
    openflow_port_available = port_open(args.odl_host, args.odl_openflow_port)

    # RESTCONF inventory
    inventory_path = "/rests/data/opendaylight-inventory:nodes?content=nonconfig"
    inventory_ok, inventory_data = restconf_get(
        host=args.odl_host,
        port=args.odl_restconf_port,
        path=inventory_path,
        user=args.odl_user,
        password=args.odl_pass,
    )
    write_json(preflight_dir / "inventory_nodes.json", inventory_data)

    # RESTCONF topology
    topology_path = "/rests/data/network-topology:network-topology?content=nonconfig"
    topology_ok, topology_data = restconf_get(
        host=args.odl_host,
        port=args.odl_restconf_port,
        path=topology_path,
        user=args.odl_user,
        password=args.odl_pass,
    )
    write_json(preflight_dir / "network_topology.json", topology_data)

    openflow_nodes: list[str] = []
    if inventory_ok and isinstance(inventory_data, dict):
        openflow_nodes = extract_openflow_node_ids(inventory_data)

    # Versiones / entorno
    env_sections: list[str] = []
    env_commands = [
        ["date"],
        ["uname", "-a"],
        ["python3", "--version"],
        ["mn", "--version"],
        ["java", "-version"],
        ["ss", "-lntp"],
    ]

    for cmd in env_commands:
        result = run_cmd(cmd, timeout=20)
        env_sections.append(format_command_result(result))
        env_sections.append("\n\n")

    write_text(preflight_dir / "environment_snapshot.txt", "".join(env_sections))

    # OVS
    ovs_state = collect_ovs_state(preflight_dir)

    # Mininet hosts
    host_pids = find_mininet_host_pids()
    write_json(preflight_dir / "mininet_host_pids.json", host_pids)

    if args.skip_ping_checks:
        connectivity_report = {
            "ping_checks_attempted": False,
            "all_success": False,
            "checks": [],
            "reason": "skip_ping_checks_enabled",
        }
        write_text(
            preflight_dir / "connectivity_check.txt",
            "Ping checks skipped by --skip-ping-checks.\n",
        )
    else:
        connectivity_report = run_representative_pings(preflight_dir, host_pids)

    # Doctor
    doctor = build_doctor_report(
        args=args,
        restconf_available=restconf_available,
        openflow_port_available=openflow_port_available,
        inventory_ok=inventory_ok,
        topology_ok=topology_ok,
        openflow_nodes=openflow_nodes,
        ovs_state=ovs_state,
        host_pids=host_pids,
        connectivity_report=connectivity_report,
    )

    write_json(preflight_dir / "doctor.json", doctor)
    write_text(preflight_dir / "doctor.txt", render_doctor_txt(doctor))

    # Resumen terminal
    print("FT8.2 preflight artifacts generated")
    print(f"Campaign ID: {args.campaign_id}")
    print(f"Run dir: {run_dir}")
    print(f"Preflight dir: {preflight_dir}")
    print(f"Doctor status: {doctor['overall_status']}")
    print()
    print("Main artifacts:")
    print(f" - {run_dir / 'campaign_manifest.json'}")
    print(f" - {preflight_dir / 'doctor.txt'}")
    print(f" - {preflight_dir / 'doctor.json'}")
    print(f" - {preflight_dir / 'inventory_nodes.json'}")
    print(f" - {preflight_dir / 'network_topology.json'}")
    print(f" - {preflight_dir / 'ovs_bridges.txt'}")
    print(f" - {preflight_dir / 'flows_before_tests.txt'}")
    print(f" - {preflight_dir / 'connectivity_check.txt'}")

    return 0 if doctor["overall_status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
