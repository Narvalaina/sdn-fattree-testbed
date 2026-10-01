#!/usr/bin/env python3
"""
Fase C.1 — Demostrador ECMP limitado con ovs-ofctl.

Ruta esperada:
    tfg_sdn/experiments/ch08_ecmp_limited_demo.py

Objetivo:
    Demostrar de forma limitada y controlada el uso de grupos OpenFlow select
    en OVS sobre una topología Fat-Tree k=4, comparando:

        C1 — Camino único single-path h1 <-> h16
        C2 — Grupo select limitado h1 <-> h16 con dos ramas equivalentes

Alcance metodológico:
    - Programación directa del datapath OVS mediante ovs-ofctl.
    - No instala ECMP mediante OpenDaylight RESTCONF.
    - No demuestra ECMP completo en toda la Fat-Tree.
    - No demuestra balanceo dinámico ni reconfiguración automática.
    - Es una evidencia adicional de bajo nivel sobre grupos OpenFlow select.

Uso recomendado:
    Terminal 1: ODL puede estar arrancado, pero no será quien instale estos flows.
    Terminal 2: Mininet Fat-Tree k=4 activo.
    Terminal 3:

        cd /path/to/sdn-fattree-testbed
        export TFG_ROOT="$PWD"
        export PYTHONPATH="$TFG_ROOT:$PYTHONPATH"
        sudo -v

        export CAMPAIGN_ID="ch08_fattree_ecmp_limited_$(date +%Y%m%d_%H%M)"

        python3 tfg_sdn/experiments/ch08_ecmp_limited_demo.py \
          --campaign-id "$CAMPAIGN_ID" --action preflight

        python3 tfg_sdn/experiments/ch08_ecmp_limited_demo.py \
          --campaign-id "$CAMPAIGN_ID" --action install-single

        python3 tfg_sdn/experiments/ch08_ecmp_limited_demo.py \
          --campaign-id "$CAMPAIGN_ID" --action prepare-single

        # Ejecutar dentro de Mininet el source indicado.

        python3 tfg_sdn/experiments/ch08_ecmp_limited_demo.py \
          --campaign-id "$CAMPAIGN_ID" --action summarize-single

        python3 tfg_sdn/experiments/ch08_ecmp_limited_demo.py \
          --campaign-id "$CAMPAIGN_ID" --action install-select

        python3 tfg_sdn/experiments/ch08_ecmp_limited_demo.py \
          --campaign-id "$CAMPAIGN_ID" --action prepare-select

        # Ejecutar dentro de Mininet el source indicado.

        python3 tfg_sdn/experiments/ch08_ecmp_limited_demo.py \
          --campaign-id "$CAMPAIGN_ID" --action summarize-select

        python3 tfg_sdn/experiments/ch08_ecmp_limited_demo.py \
          --campaign-id "$CAMPAIGN_ID" --action process
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import statistics
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


# ---------------------------------------------------------------------------
# Constantes de topología y campaña
# ---------------------------------------------------------------------------

CAMPAIGN_PREFIX = "ch08_fattree_ecmp_limited_"

H1_IP = "10.0.0.1"
H16_IP = "10.0.0.16"
H1_MAC = "00:00:00:00:00:01"
H16_MAC = "00:00:00:00:00:10"

IPERF_PORT = 5601
IPERF_PARALLEL = 8
IPERF_SECONDS = 20
N_REPETITIONS = 3

GROUP_ID = 100

RELEVANT_SWITCHES = ["e1", "a1", "a2", "c1", "c3", "a7", "a8", "e8"]
GROUP_SWITCHES = ["e1", "e8"]
REQUIRED_SWITCHES = set(RELEVANT_SWITCHES)

# Puertos Fat-Tree k=4 observados en Mininet:
# e1: eth1 h1, eth2 h2, eth3 a1, eth4 a2
# e8: eth1 h15, eth2 h16, eth3 a7, eth4 a8
# a1: eth1 e1, eth2 e2, eth3 c1, eth4 c2
# a2: eth1 e1, eth2 e2, eth3 c3, eth4 c4
# a7: eth1 e7, eth2 e8, eth3 c1, eth4 c2
# a8: eth1 e7, eth2 e8, eth3 c3, eth4 c4
# c1: eth1 a1, eth2 a3, eth3 a5, eth4 a7
# c3: eth1 a2, eth2 a4, eth3 a6, eth4 a8

# Camino A:
# h1 -> e1 -> a1 -> c1 -> a7 -> e8 -> h16
SINGLE_PATH_FLOWS: Dict[str, List[Tuple[str, int]]] = {
    "e1": [(H16_MAC, 3), (H1_MAC, 1)],
    "a1": [(H16_MAC, 3), (H1_MAC, 1)],
    "c1": [(H16_MAC, 4), (H1_MAC, 1)],
    "a7": [(H16_MAC, 2), (H1_MAC, 3)],
    "e8": [(H16_MAC, 2), (H1_MAC, 3)],
}

# Grupo select en e1/e8 y reglas deterministas aguas abajo para dos ramas:
# Rama A: e1-a1-c1-a7-e8
# Rama B: e1-a2-c3-a8-e8
SELECT_DOWNSTREAM_FLOWS: Dict[str, List[Tuple[str, int]]] = {
    "e1": [(H1_MAC, 1)],
    "a1": [(H16_MAC, 3), (H1_MAC, 1)],
    "a2": [(H16_MAC, 3), (H1_MAC, 1)],
    "c1": [(H16_MAC, 4), (H1_MAC, 1)],
    "c3": [(H16_MAC, 4), (H1_MAC, 1)],
    "a7": [(H16_MAC, 2), (H1_MAC, 3)],
    "a8": [(H16_MAC, 2), (H1_MAC, 3)],
    "e8": [(H16_MAC, 2)],
}

BRANCHES = {
    "forward_A": {"switch": "a1", "dst_mac": H16_MAC, "description": "h1->h16 via e1-a1-c1-a7-e8"},
    "forward_B": {"switch": "a2", "dst_mac": H16_MAC, "description": "h1->h16 via e1-a2-c3-a8-e8"},
    "reverse_A": {"switch": "a7", "dst_mac": H1_MAC, "description": "h16->h1 via e8-a7-c1-a1-e1"},
    "reverse_B": {"switch": "a8", "dst_mac": H1_MAC, "description": "h16->h1 via e8-a8-c3-a2-e1"},
}


# ---------------------------------------------------------------------------
# Utilidades generales
# ---------------------------------------------------------------------------


def default_tfg_root() -> Path:
    return Path(
        os.environ.get(
            "TFG_ROOT",
            str(Path(__file__).resolve().parents[2]),
        )
    ).expanduser()


def timestamp_id() -> str:
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
    if latest and not action.startswith("install") and action not in {"preflight"}:
        return latest
    return CAMPAIGN_PREFIX + timestamp_id()


def rel(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


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


def write_csv(path: Path, rows: List[Dict[str, Any]], fieldnames: Optional[Sequence[str]] = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if fieldnames is None:
        keys: List[str] = []
        for row in rows:
            for key in row.keys():
                if key not in keys:
                    keys.append(key)
        fieldnames = keys
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(fieldnames))
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in fieldnames})


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


def fmt_num(value: Any, decimals: int = 2) -> str:
    v = as_float(value)
    if v is None:
        return "--"
    return f"{v:.{decimals}f}".replace(".", ",")


def tick_es(value: float, _pos=None) -> str:
    """Formateador de ejes con coma decimal para figuras finales."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if abs(number - round(number)) < 1e-9:
        return f"{int(round(number))}"
    return f"{number:g}".replace(".", ",")


def apply_spanish_yaxis(ax: Any) -> None:
    from matplotlib.ticker import FuncFormatter
    ax.yaxis.set_major_formatter(FuncFormatter(tick_es))


def mean(values: Iterable[Any]) -> Optional[float]:
    nums = [v for v in (as_float(x) for x in values) if v is not None]
    return statistics.mean(nums) if nums else None


def min_num(values: Iterable[Any]) -> Optional[float]:
    nums = [v for v in (as_float(x) for x in values) if v is not None]
    return min(nums) if nums else None


def max_num(values: Iterable[Any]) -> Optional[float]:
    nums = [v for v in (as_float(x) for x in values) if v is not None]
    return max(nums) if nums else None


class CampaignPaths:
    def __init__(self, tfg_root: Path, campaign_id: str) -> None:
        self.tfg_root = tfg_root
        self.campaign_id = campaign_id
        self.root = tfg_root / "runs" / "ch08_fattree" / campaign_id
        self.preflight = self.root / "c0_preflight"
        self.single = self.root / "c1_single_path"
        self.select = self.root / "c2_select_group"
        self.processed = tfg_root / "data" / "processed" / "ch08_fattree" / campaign_id
        self.figures = tfg_root / "figures" / "ch08_resultados" / campaign_id
        self.tables = tfg_root / "tables" / "ch08" / f"ecmp_limited_{campaign_id}"
        self.docs = tfg_root / "docs" / "ch08_resultados" / campaign_id

    def ensure(self) -> None:
        for p in [self.root, self.preflight, self.single, self.select, self.processed, self.figures, self.tables, self.docs]:
            p.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Ejecución de comandos
# ---------------------------------------------------------------------------


def run_cmd(cmd: Sequence[str], check: bool = True, output_path: Optional[Path] = None) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        list(cmd),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            "$ " + " ".join(cmd) + "\n\n" + proc.stdout + ("\n[stderr]\n" + proc.stderr if proc.stderr else ""),
            encoding="utf-8",
        )
    if check and proc.returncode != 0:
        msg = [
            f"[FAIL] Comando fallido ({proc.returncode}): {' '.join(cmd)}",
            proc.stdout,
            proc.stderr,
        ]
        raise RuntimeError("\n".join(msg))
    return proc


def sudo_cmd(args: Sequence[str], check: bool = True, output_path: Optional[Path] = None) -> subprocess.CompletedProcess:
    return run_cmd(["sudo", "-n", *args], check=check, output_path=output_path)


def ovs_ofctl(bridge: str, args: Sequence[str], check: bool = True, output_path: Optional[Path] = None) -> subprocess.CompletedProcess:
    return sudo_cmd(["ovs-ofctl", "-O", "OpenFlow13", *args, bridge], check=check, output_path=output_path)


def ovs_ofctl_add_flow(bridge: str, flow: str) -> None:
    sudo_cmd(["ovs-ofctl", "-O", "OpenFlow13", "add-flow", bridge, flow])


def ovs_ofctl_add_group(bridge: str, group: str) -> None:
    sudo_cmd(["ovs-ofctl", "-O", "OpenFlow13", "add-group", bridge, group])


def ovs_list_bridges() -> List[str]:
    proc = sudo_cmd(["ovs-vsctl", "list-br"])
    return sorted([line.strip() for line in proc.stdout.splitlines() if line.strip()])


def require_sudo_cached() -> None:
    proc = subprocess.run(["sudo", "-n", "true"], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        raise SystemExit(
            "[FAIL] sudo no está cacheado. Ejecuta primero en esta terminal:\n\n"
            "    sudo -v\n\n"
            "y después repite el comando."
        )


# ---------------------------------------------------------------------------
# Captura de evidencias OVS
# ---------------------------------------------------------------------------


def capture_ovs_state(out_dir: Path, bridges: Optional[List[str]] = None) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    bridges = bridges or ovs_list_bridges()

    sudo_cmd(["ovs-vsctl", "show"], check=False, output_path=out_dir / "ovs_vsctl_show.txt")
    sudo_cmd(["ovs-vsctl", "list-br"], check=False, output_path=out_dir / "ovs_bridges.txt")

    for br in bridges:
        sudo_cmd(["ovs-ofctl", "-O", "OpenFlow13", "dump-flows", br], check=False, output_path=out_dir / f"{br}_flows.txt")
        sudo_cmd(["ovs-ofctl", "-O", "OpenFlow13", "dump-groups", br], check=False, output_path=out_dir / f"{br}_groups.txt")
        sudo_cmd(["ovs-ofctl", "-O", "OpenFlow13", "dump-group-stats", br], check=False, output_path=out_dir / f"{br}_group_stats.txt")


def reset_all_ovs_bridges(paths: CampaignPaths, stage_name: str) -> List[str]:
    bridges = ovs_list_bridges()
    reset_dir = paths.root / stage_name / "ovs_reset"
    reset_dir.mkdir(parents=True, exist_ok=True)

    for br in bridges:
        sudo_cmd(["ovs-vsctl", "set", "bridge", br, "protocols=OpenFlow13"], check=False)
        sudo_cmd(["ovs-vsctl", "set-fail-mode", br, "secure"], check=False)
        sudo_cmd(["ovs-ofctl", "-O", "OpenFlow13", "del-flows", br], check=False)
        sudo_cmd(["ovs-ofctl", "-O", "OpenFlow13", "del-groups", br], check=False)
        ovs_ofctl_add_flow(br, "priority=300,arp,actions=drop")
        ovs_ofctl_add_flow(br, "priority=0,actions=drop")

    capture_ovs_state(reset_dir / "after_reset", bridges)
    return bridges


def add_unicast_flow(bridge: str, dst_mac: str, out_port: int, priority: int = 250) -> None:
    ovs_ofctl_add_flow(bridge, f"priority={priority},ip,dl_dst={dst_mac},actions=output:{out_port}")


def add_group_flow(bridge: str, dst_mac: str, group_id: int, priority: int = 260) -> None:
    ovs_ofctl_add_flow(bridge, f"priority={priority},ip,dl_dst={dst_mac},actions=group:{group_id}")


# ---------------------------------------------------------------------------
# Acciones de instalación
# ---------------------------------------------------------------------------


def action_preflight(paths: CampaignPaths) -> None:
    paths.ensure()
    bridges = ovs_list_bridges()
    capture_ovs_state(paths.preflight / "ovs_state", bridges)

    required_ok = REQUIRED_SWITCHES.issubset(set(bridges))
    missing = sorted(REQUIRED_SWITCHES - set(bridges))

    summary = {
        "campaign_id": paths.campaign_id,
        "status": "PASS" if not missing and len(bridges) >= 20 else "CHECK_REQUIRED",
        "n_bridges": len(bridges),
        "bridges": bridges,
        "required_switches": sorted(REQUIRED_SWITCHES),
        "missing_required_switches": missing,
        "methodological_scope": "C.1 datapath-level demo using ovs-ofctl, not ODL RESTCONF.",
    }
    write_json(paths.preflight / "c0_preflight_summary.json", summary)

    print("C0 preflight ECMP limitado")
    print("=" * 48)
    print(f"campaign_id: {paths.campaign_id}")
    print(f"n_bridges: {len(bridges)}")
    print(f"missing_required_switches: {missing}")
    print(f"status: {summary['status']}")


def action_install_single(paths: CampaignPaths) -> None:
    paths.ensure()
    require_sudo_cached()
    bridges = reset_all_ovs_bridges(paths, "c1_single_path")

    install_dir = paths.single / "install"
    install_dir.mkdir(parents=True, exist_ok=True)

    installed: List[Dict[str, Any]] = []
    for bridge, rules in SINGLE_PATH_FLOWS.items():
        for dst_mac, out_port in rules:
            add_unicast_flow(bridge, dst_mac, out_port, priority=250)
            installed.append({"bridge": bridge, "dst_mac": dst_mac, "action": f"output:{out_port}", "priority": 250})

    capture_ovs_state(paths.single / "flows_after_install", bridges)

    summary = {
        "campaign_id": paths.campaign_id,
        "mode": "single_path",
        "status": "PASS",
        "n_installed_flows": len(installed),
        "installed_flows": installed,
        "path_forward": "h1-e1-a1-c1-a7-e8-h16",
        "path_reverse": "h16-e8-a7-c1-a1-e1-h1",
        "method": "ovs-ofctl direct datapath programming",
    }
    write_json(paths.single / "install_single_summary.json", summary)
    write_text(paths.single / "install_single_report.txt", json.dumps(summary, indent=2, ensure_ascii=False))

    print("C1 install-single")
    print("=" * 48)
    print(f"campaign_id: {paths.campaign_id}")
    print(f"n_installed_flows: {len(installed)}")
    print("status: PASS")


def action_install_select(paths: CampaignPaths) -> None:
    paths.ensure()
    require_sudo_cached()
    bridges = reset_all_ovs_bridges(paths, "c2_select_group")

    installed: List[Dict[str, Any]] = []
    groups: List[Dict[str, Any]] = []

    # Grupo select en e1 hacia a1/a2.
    ovs_ofctl_add_group("e1", f"group_id={GROUP_ID},type=select,bucket=output:3,bucket=output:4")
    groups.append({"bridge": "e1", "group_id": GROUP_ID, "type": "select", "buckets": ["output:3", "output:4"]})
    add_group_flow("e1", H16_MAC, GROUP_ID, priority=260)
    installed.append({"bridge": "e1", "dst_mac": H16_MAC, "action": f"group:{GROUP_ID}", "priority": 260})

    # Grupo select en e8 hacia a7/a8 para retorno.
    ovs_ofctl_add_group("e8", f"group_id={GROUP_ID},type=select,bucket=output:3,bucket=output:4")
    groups.append({"bridge": "e8", "group_id": GROUP_ID, "type": "select", "buckets": ["output:3", "output:4"]})
    add_group_flow("e8", H1_MAC, GROUP_ID, priority=260)
    installed.append({"bridge": "e8", "dst_mac": H1_MAC, "action": f"group:{GROUP_ID}", "priority": 260})

    for bridge, rules in SELECT_DOWNSTREAM_FLOWS.items():
        for dst_mac, out_port in rules:
            add_unicast_flow(bridge, dst_mac, out_port, priority=250)
            installed.append({"bridge": bridge, "dst_mac": dst_mac, "action": f"output:{out_port}", "priority": 250})

    capture_ovs_state(paths.select / "flows_groups_after_install", bridges)

    summary = {
        "campaign_id": paths.campaign_id,
        "mode": "select_group",
        "status": "PASS",
        "n_installed_flows": len(installed),
        "n_groups": len(groups),
        "groups": groups,
        "installed_flows": installed,
        "path_forward_A": "h1-e1-a1-c1-a7-e8-h16",
        "path_forward_B": "h1-e1-a2-c3-a8-e8-h16",
        "path_reverse_A": "h16-e8-a7-c1-a1-e1-h1",
        "path_reverse_B": "h16-e8-a8-c3-a2-e1-h1",
        "method": "ovs-ofctl direct datapath programming with OpenFlow select groups",
    }
    write_json(paths.select / "install_select_summary.json", summary)
    write_text(paths.select / "install_select_report.txt", json.dumps(summary, indent=2, ensure_ascii=False))

    print("C2 install-select")
    print("=" * 48)
    print(f"campaign_id: {paths.campaign_id}")
    print(f"n_groups: {len(groups)}")
    print(f"n_installed_flows: {len(installed)}")
    print("status: PASS")


# ---------------------------------------------------------------------------
# Preparación de comandos Mininet
# ---------------------------------------------------------------------------


def mininet_common_commands(stage_dir: Path, mode: str) -> List[str]:
    raw = stage_dir / "raw"
    arp = stage_dir / "arp_checks"
    connectivity = stage_dir / "connectivity_checks"
    flows_after = stage_dir / "flows_after_traffic"
    groups_after = stage_dir / "groups_after_traffic"

    commands: List[str] = []
    commands.append(f"sh mkdir -p {raw} {arp} {connectivity} {flows_after} {groups_after}")
    commands.append("h1 ip neigh flush all")
    commands.append("h16 ip neigh flush all")
    commands.append(f"h1 ip neigh replace {H16_IP} lladdr {H16_MAC} nud permanent dev h1-eth1")
    commands.append(f"h16 ip neigh replace {H1_IP} lladdr {H1_MAC} nud permanent dev h16-eth1")
    commands.append(f"h1 ip neigh show | tee {arp}/h1_static_arp.txt")
    commands.append(f"h16 ip neigh show | tee {arp}/h16_static_arp.txt")
    commands.append(f"h1 ping -c 10 h16 | tee {connectivity}/h1_h16_ping.txt")
    commands.append(f"h16 pkill -f 'iperf3 -s -p {IPERF_PORT}'")
    commands.append(f"h16 iperf3 -s -p {IPERF_PORT} -D")
    commands.append("sh sleep 1")
    commands.append(f"h16 ss -lnt | grep :{IPERF_PORT} | tee {stage_dir}/iperf_server_ready.txt")

    for rep in range(1, N_REPETITIONS + 1):
        commands.append(
            f"h1 iperf3 -c {H16_IP} -p {IPERF_PORT} -P {IPERF_PARALLEL} -t {IPERF_SECONDS} -J > "
            f"{raw}/iperf_{mode}_rep{rep:02d}.json"
        )
        commands.append("sh sleep 1")

    for sw in RELEVANT_SWITCHES:
        commands.append(f"sh ovs-ofctl -O OpenFlow13 dump-flows {sw} > {flows_after}/{sw}_flows.txt")
        commands.append(f"sh ovs-ofctl -O OpenFlow13 dump-groups {sw} > {groups_after}/{sw}_groups.txt")
        commands.append(f"sh ovs-ofctl -O OpenFlow13 dump-group-stats {sw} > {groups_after}/{sw}_group_stats.txt")

    return commands


def action_prepare_single(paths: CampaignPaths) -> None:
    paths.ensure()
    cli_path = paths.single / "mininet_single_path.cli"
    commands = mininet_common_commands(paths.single, "single")
    write_text(cli_path, "\n".join(commands) + "\n")

    print("C1 prepare-single: OK")
    print(f"campaign_id: {paths.campaign_id}")
    print("Ejecuta dentro de Mininet:")
    print(f"source {cli_path}")


def action_prepare_select(paths: CampaignPaths) -> None:
    paths.ensure()
    cli_path = paths.select / "mininet_select_group.cli"
    commands = mininet_common_commands(paths.select, "select")
    write_text(cli_path, "\n".join(commands) + "\n")

    print("C2 prepare-select: OK")
    print(f"campaign_id: {paths.campaign_id}")
    print("Ejecuta dentro de Mininet:")
    print(f"source {cli_path}")


# ---------------------------------------------------------------------------
# Parseo de resultados
# ---------------------------------------------------------------------------


def parse_ping(path: Path) -> Dict[str, Any]:
    text = read_text(path)
    loss_match = re.search(r"(\d+(?:\.\d+)?)% packet loss", text)
    rtt_match = re.search(r"rtt min/avg/max/(?:mdev|stddev) = ([0-9.]+)/([0-9.]+)/([0-9.]+)/([0-9.]+) ms", text)

    return {
        "ping_file": str(path),
        "packet_loss_pct": float(loss_match.group(1)) if loss_match else None,
        "rtt_min_ms": float(rtt_match.group(1)) if rtt_match else None,
        "rtt_avg_ms": float(rtt_match.group(2)) if rtt_match else None,
        "rtt_max_ms": float(rtt_match.group(3)) if rtt_match else None,
        "rtt_mdev_ms": float(rtt_match.group(4)) if rtt_match else None,
        "ping_ok": bool(loss_match and float(loss_match.group(1)) == 0.0),
    }


def parse_iperf_json(path: Path) -> Dict[str, Any]:
    data = read_json(path)
    if not data:
        return {"file": str(path), "ok": False, "receiver_gbps": None, "sender_gbps": None, "error": "invalid_json"}

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
        "bytes_received": as_float(sum_received.get("bytes")),
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
        rows.append(
            {
                "branch": branch_name,
                "switch": switch,
                "dst_mac": dst_mac,
                "description": info["description"],
                "packets": counters["packets"],
                "bytes": counters["bytes"],
                "evidence_file": str(flow_file),
            }
        )

    forward_total = sum(r["bytes"] for r in rows if r["branch"].startswith("forward_"))
    reverse_total = sum(r["bytes"] for r in rows if r["branch"].startswith("reverse_"))

    for row in rows:
        if row["branch"].startswith("forward_"):
            row["share_pct"] = (100.0 * row["bytes"] / forward_total) if forward_total > 0 else None
        else:
            row["share_pct"] = (100.0 * row["bytes"] / reverse_total) if reverse_total > 0 else None

    return rows


def group_present(stage_dir: Path, bridge: str) -> bool:
    candidates = [
        stage_dir / "flows_groups_after_install" / f"{bridge}_groups.txt",
        stage_dir / "groups_after_traffic" / f"{bridge}_groups.txt",
    ]
    for path in candidates:
        text = read_text(path)
        if f"group_id={GROUP_ID}" in text or f"group_id={GROUP_ID}," in text:
            return True
    return False


def summarize_stage(paths: CampaignPaths, mode: str) -> Dict[str, Any]:
    if mode == "single":
        stage_dir = paths.single
        iperf_prefix = "iperf_single"
    elif mode == "select":
        stage_dir = paths.select
        iperf_prefix = "iperf_select"
    else:
        raise ValueError(f"Modo no soportado: {mode}")

    ping = parse_ping(stage_dir / "connectivity_checks" / "h1_h16_ping.txt")
    iperf_rows = []
    for rep in range(1, N_REPETITIONS + 1):
        row = parse_iperf_json(stage_dir / "raw" / f"{iperf_prefix}_rep{rep:02d}.json")
        row["rep"] = rep
        row["mode"] = mode
        iperf_rows.append(row)

    branch_rows = parse_branch_usage(stage_dir)
    write_csv(stage_dir / f"{mode}_iperf_summary.csv", iperf_rows)
    write_csv(stage_dir / f"{mode}_branch_usage.csv", branch_rows)

    valid_iperf = [r for r in iperf_rows if r.get("ok")]
    throughput_mean = mean(r.get("receiver_gbps") for r in valid_iperf)
    throughput_min = min_num(r.get("receiver_gbps") for r in valid_iperf)
    throughput_max = max_num(r.get("receiver_gbps") for r in valid_iperf)

    forward_a = next((r for r in branch_rows if r["branch"] == "forward_A"), {})
    forward_b = next((r for r in branch_rows if r["branch"] == "forward_B"), {})
    reverse_a = next((r for r in branch_rows if r["branch"] == "reverse_A"), {})
    reverse_b = next((r for r in branch_rows if r["branch"] == "reverse_B"), {})

    if mode == "single":
        branch_evidence_ok = (forward_a.get("bytes", 0) > 0 and forward_b.get("bytes", 0) == 0)
        group_evidence_ok = True
    else:
        branch_evidence_ok = (forward_a.get("bytes", 0) > 0 and forward_b.get("bytes", 0) > 0)
        group_evidence_ok = group_present(stage_dir, "e1") and group_present(stage_dir, "e8")

    status = "PASS" if ping.get("ping_ok") and len(valid_iperf) == N_REPETITIONS and branch_evidence_ok and group_evidence_ok else "CHECK_REQUIRED"

    summary = {
        "campaign_id": paths.campaign_id,
        "mode": mode,
        "status": status,
        "ping_ok": ping.get("ping_ok"),
        "packet_loss_pct": ping.get("packet_loss_pct"),
        "rtt_avg_ms": ping.get("rtt_avg_ms"),
        "n_iperf_ok": len(valid_iperf),
        "n_iperf_total": N_REPETITIONS,
        "throughput_mean_gbps": throughput_mean,
        "throughput_min_gbps": throughput_min,
        "throughput_max_gbps": throughput_max,
        "branch_evidence_ok": branch_evidence_ok,
        "group_evidence_ok": group_evidence_ok,
        "forward_A_bytes": forward_a.get("bytes", 0),
        "forward_B_bytes": forward_b.get("bytes", 0),
        "reverse_A_bytes": reverse_a.get("bytes", 0),
        "reverse_B_bytes": reverse_b.get("bytes", 0),
        "forward_A_share_pct": forward_a.get("share_pct"),
        "forward_B_share_pct": forward_b.get("share_pct"),
        "reverse_A_share_pct": reverse_a.get("share_pct"),
        "reverse_B_share_pct": reverse_b.get("share_pct"),
        "methodological_scope": "C.1 datapath-level ovs-ofctl demonstration",
    }

    write_json(stage_dir / f"{mode}_summary.json", summary)
    write_text(stage_dir / f"{mode}_report.txt", json.dumps(summary, indent=2, ensure_ascii=False))

    return summary


def action_summarize_single(paths: CampaignPaths) -> None:
    summary = summarize_stage(paths, "single")
    print("C1 summarize-single")
    print("=" * 48)
    print(f"campaign_id: {paths.campaign_id}")
    print(f"ping_ok: {summary['ping_ok']}")
    print(f"n_iperf_ok: {summary['n_iperf_ok']}/{summary['n_iperf_total']}")
    print(f"throughput_mean_gbps: {fmt_num(summary['throughput_mean_gbps'], 2)}")
    print(f"branch_evidence_ok: {summary['branch_evidence_ok']}")
    print(f"status: {summary['status']}")
    print(f"report: {paths.single / 'single_report.txt'}")


def action_summarize_select(paths: CampaignPaths) -> None:
    summary = summarize_stage(paths, "select")
    print("C2 summarize-select")
    print("=" * 48)
    print(f"campaign_id: {paths.campaign_id}")
    print(f"ping_ok: {summary['ping_ok']}")
    print(f"n_iperf_ok: {summary['n_iperf_ok']}/{summary['n_iperf_total']}")
    print(f"throughput_mean_gbps: {fmt_num(summary['throughput_mean_gbps'], 2)}")
    print(f"branch_evidence_ok: {summary['branch_evidence_ok']}")
    print(f"group_evidence_ok: {summary['group_evidence_ok']}")
    print(f"forward_A_share_pct: {fmt_num(summary['forward_A_share_pct'], 1)}")
    print(f"forward_B_share_pct: {fmt_num(summary['forward_B_share_pct'], 1)}")
    print(f"status: {summary['status']}")
    print(f"report: {paths.select / 'select_report.txt'}")


# ---------------------------------------------------------------------------
# Procesado final y figuras opcionales
# ---------------------------------------------------------------------------


def import_matplotlib():
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        return plt
    except ImportError:
        return None


def save_fig(plt: Any, fig: Any, out_base: Path) -> List[str]:
    out_base.parent.mkdir(parents=True, exist_ok=True)
    files = []
    for suffix in [".png", ".svg", ".pdf"]:
        path = out_base.with_suffix(suffix)
        fig.savefig(path, dpi=180, bbox_inches="tight")
        files.append(str(path))
    plt.close(fig)
    return files


def style_ax(ax: Any) -> None:
    ax.grid(True, axis="y", alpha=0.28)
    ax.set_axisbelow(True)
    ax.tick_params(axis="both", labelsize=11)
    apply_spanish_yaxis(ax)


def bar_chart(plt: Any, labels: List[str], values: List[float], title: str, ylabel: str, out_base: Path, unit: str = "") -> Optional[List[str]]:
    if not labels or not values or len(labels) != len(values):
        return None
    fig = plt.figure(figsize=(9.5, 5.4))
    ax = fig.add_subplot(111)
    bars = ax.bar(labels, values)
    ax.set_title(title, fontsize=17, fontweight="bold", pad=16)
    ax.set_ylabel(ylabel, fontsize=13)
    style_ax(ax)
    max_v = max(values) if values else 1.0
    ax.set_ylim(0, max_v * 1.18 if max_v > 0 else 1.0)
    for bar in bars:
        h = bar.get_height()
        ax.annotate(
            f"{fmt_num(h, 2)} {unit}".strip(),
            xy=(bar.get_x() + bar.get_width() / 2.0, h),
            xytext=(0, 8),
            textcoords="offset points",
            ha="center",
            va="bottom",
            fontsize=10.5,
            fontweight="bold",
        )
    fig.tight_layout()
    return save_fig(plt, fig, out_base)


def grouped_branch_share_chart(
    plt: Any,
    labels: List[str],
    branch_a_values: List[float],
    branch_b_values: List[float],
    title: str,
    ylabel: str,
    out_base: Path,
) -> Optional[List[str]]:
    """Figura agrupada homogénea con el estilo usado en Leaf-Spine.

    Representa cada modo de encaminamiento en el eje X y, dentro de cada modo,
    dos barras: Rama A y Rama B. Es más comparable con las figuras previas de
    utilización de uplinks que una figura con cuatro categorías independientes.
    """
    if not labels or len(labels) != len(branch_a_values) or len(labels) != len(branch_b_values):
        return None

    fig = plt.figure(figsize=(10.5, 5.6))
    ax = fig.add_subplot(111)

    x = list(range(len(labels)))
    width = 0.32

    bars_a = ax.bar([i - width / 2 for i in x], branch_a_values, width, label="Rama 1")
    bars_b = ax.bar([i + width / 2 for i in x], branch_b_values, width, label="Rama 2")

    ax.set_title(title, fontsize=17, fontweight="bold", pad=16)
    ax.set_ylabel(ylabel, fontsize=13)
    ax.set_xlabel("Modo de encaminamiento", fontsize=13)
    ax.set_xticks(x)
    ax.set_xticklabels(labels)
    ax.legend(loc="upper center", ncol=2, frameon=True)
    style_ax(ax)

    max_value = max(branch_a_values + branch_b_values) if branch_a_values or branch_b_values else 100.0
    ax.set_ylim(0, max(110.0, max_value * 1.18))

    for bars in [bars_a, bars_b]:
        for bar in bars:
            h = bar.get_height()
            ax.annotate(
                f"{fmt_num(h, 2)} %",
                xy=(bar.get_x() + bar.get_width() / 2.0, h),
                xytext=(0, 8),
                textcoords="offset points",
                ha="center",
                va="bottom",
                fontsize=10.5,
                fontweight="bold",
            )

    fig.tight_layout()
    return save_fig(plt, fig, out_base)


def action_process(paths: CampaignPaths) -> None:
    paths.ensure()
    single = read_json(paths.single / "single_summary.json")
    select = read_json(paths.select / "select_summary.json")

    status_rows: List[Dict[str, Any]] = []
    throughput_rows: List[Dict[str, Any]] = []
    branch_rows: List[Dict[str, Any]] = []

    for mode, summary in [("single_path", single), ("select_group", select)]:
        if not summary:
            status_rows.append({"mode": mode, "status": "MISSING", "note": "summary not found"})
            continue
        status_rows.append(
            {
                "mode": mode,
                "status": summary.get("status"),
                "ping_ok": summary.get("ping_ok"),
                "iperf_ok": f"{summary.get('n_iperf_ok')}/{summary.get('n_iperf_total')}",
                "branch_evidence_ok": summary.get("branch_evidence_ok"),
                "group_evidence_ok": summary.get("group_evidence_ok"),
            }
        )
        throughput_rows.append(
            {
                "mode": mode,
                "throughput_mean_gbps": summary.get("throughput_mean_gbps"),
                "throughput_min_gbps": summary.get("throughput_min_gbps"),
                "throughput_max_gbps": summary.get("throughput_max_gbps"),
                "rtt_avg_ms": summary.get("rtt_avg_ms"),
            }
        )
        for direction in ["forward", "reverse"]:
            branch_rows.append(
                {
                    "mode": mode,
                    "direction": direction,
                    "branch_A_bytes": summary.get(f"{direction}_A_bytes"),
                    "branch_B_bytes": summary.get(f"{direction}_B_bytes"),
                    "branch_A_share_pct": summary.get(f"{direction}_A_share_pct"),
                    "branch_B_share_pct": summary.get(f"{direction}_B_share_pct"),
                }
            )

    write_csv(paths.processed / "ch08_ecmp_limited_status.csv", status_rows)
    write_csv(paths.processed / "ch08_ecmp_limited_throughput_summary.csv", throughput_rows)
    write_csv(paths.processed / "ch08_ecmp_limited_branch_usage.csv", branch_rows)

    figures: Dict[str, List[str]] = {}
    plt = import_matplotlib()
    if plt is not None and single and select:
        out = bar_chart(
            plt,
            ["Single-path", "ECMP limitado"],
            [as_float(single.get("throughput_mean_gbps")) or 0.0, as_float(select.get("throughput_mean_gbps")) or 0.0],
            "Throughput medio en single-path y ECMP limitado",
            "Throughput medio recibido (Gbps)",
            paths.figures / "ch08_ecmp_limited_throughput_comparison",
            unit="Gbps",
        )
        if out:
            figures["throughput_comparison"] = out

        out = grouped_branch_share_chart(
            plt,
            ["Single-path", "ECMP limitado"],
            [
                as_float(single.get("forward_A_share_pct")) or 0.0,
                as_float(select.get("forward_A_share_pct")) or 0.0,
            ],
            [
                as_float(single.get("forward_B_share_pct")) or 0.0,
                as_float(select.get("forward_B_share_pct")) or 0.0,
            ],
            "Reparto de tráfico entre ramas equivalentes",
            "Porcentaje de bytes en sentido h1→h16 (%)",
            paths.figures / "ch08_ecmp_limited_branch_share",
        )
        if out:
            figures["branch_share"] = out

    final_status = "PASS" if single and select and single.get("status") == "PASS" and select.get("status") == "PASS" else "CHECK_REQUIRED"
    manifest = {
        "campaign_id": paths.campaign_id,
        "status": final_status,
        "scope": "C.1 datapath-level ECMP limited demo using ovs-ofctl",
        "processed_dir": str(paths.processed),
        "figures_dir": str(paths.figures),
        "figures": figures,
        "status_csv": str(paths.processed / "ch08_ecmp_limited_status.csv"),
        "throughput_csv": str(paths.processed / "ch08_ecmp_limited_throughput_summary.csv"),
        "branch_usage_csv": str(paths.processed / "ch08_ecmp_limited_branch_usage.csv"),
    }
    write_json(paths.processed / "ch08_ecmp_limited_processing_manifest.json", manifest)

    print("C.1 process ECMP limitado")
    print("=" * 48)
    print(f"campaign_id: {paths.campaign_id}")
    print(f"status: {final_status}")
    print(f"processed_dir: {paths.processed}")
    print(f"figures_generated: {len(figures)}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fase C.1 — Demostrador ECMP limitado con ovs-ofctl.")
    parser.add_argument("--tfg-root", default=str(default_tfg_root()))
    parser.add_argument("--campaign-id", default=None)
    parser.add_argument(
        "--action",
        required=True,
        choices=[
            "preflight",
            "install-single",
            "prepare-single",
            "summarize-single",
            "install-select",
            "prepare-select",
            "summarize-select",
            "process",
        ],
    )
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    tfg_root = Path(args.tfg_root).expanduser().resolve()
    campaign_id = resolve_campaign_id(tfg_root, args.campaign_id, args.action)
    paths = CampaignPaths(tfg_root, campaign_id)
    paths.ensure()

    try:
        if args.action == "preflight":
            action_preflight(paths)
        elif args.action == "install-single":
            action_install_single(paths)
        elif args.action == "prepare-single":
            action_prepare_single(paths)
        elif args.action == "summarize-single":
            action_summarize_single(paths)
        elif args.action == "install-select":
            action_install_select(paths)
        elif args.action == "prepare-select":
            action_prepare_select(paths)
        elif args.action == "summarize-select":
            action_summarize_select(paths)
        elif args.action == "process":
            action_process(paths)
        else:
            raise ValueError(args.action)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    return 0


if __name__ == "__main__":
    raise SystemExit(main())




