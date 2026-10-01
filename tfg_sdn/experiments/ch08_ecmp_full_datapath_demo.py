#!/usr/bin/env python3
"""
Capítulo 8 — Fase D: intento controlado de ECMP completo en Fat-Tree k=4.

Ruta esperada:
    tfg_sdn/experiments/ch08_ecmp_full_datapath_demo.py

Objetivo:
    Reproducir de forma limpia y trazable las fases D.0, D.1 y D.2 sin pegar
    bloques Bash largos en el terminal.

Alcance metodológico:
    - D.0 captura inventario y documenta rutas candidatas.
    - D.1 instala un prototipo datapath con ovs-ofctl para h1 <-> h16.
    - D.2 estresa el prototipo añadiendo h2 <-> h15 y más flujos iperf3.
    - No instala todavía mediante OpenDaylight RESTCONF.
    - No debe presentarse automáticamente como ECMP completo Fat-Tree PASS.

Acciones disponibles:
    --action d0-capture
    --action d1-install
    --action d1-prepare-traffic
    --action d1-summarize
    --action d2-install-extra
    --action d2-prepare-stress
    --action d2-summarize
    --action status

Uso típico:
    cd /path/to/sdn-fattree-testbed
    export TFG_ROOT="$PWD"
    export PYTHONPATH="$TFG_ROOT:$PYTHONPATH"
    export ODL_HOST="127.0.0.1"
    export ODL_OF_PORT="6653"
    export ODL_REST_PORT="8181"
    export ODL_USER="admin"
    export ODL_PASS="<set-locally>"
    sudo -v

    export CAMPAIGN_ID="ch08_fattree_ecmp_full_$(date +%Y%m%d_%H%M)"

    python3 tfg_sdn/experiments/ch08_ecmp_full_datapath_demo.py \
      --campaign-id "$CAMPAIGN_ID" --action d0-capture

    python3 tfg_sdn/experiments/ch08_ecmp_full_datapath_demo.py \
      --campaign-id "$CAMPAIGN_ID" --action d1-install

    python3 tfg_sdn/experiments/ch08_ecmp_full_datapath_demo.py \
      --campaign-id "$CAMPAIGN_ID" --action d1-prepare-traffic

    # Dentro de Mininet:
    # source <ruta mostrada por d1-prepare-traffic>

    python3 tfg_sdn/experiments/ch08_ecmp_full_datapath_demo.py \
      --campaign-id "$CAMPAIGN_ID" --action d1-summarize

    python3 tfg_sdn/experiments/ch08_ecmp_full_datapath_demo.py \
      --campaign-id "$CAMPAIGN_ID" --action d2-install-extra

    python3 tfg_sdn/experiments/ch08_ecmp_full_datapath_demo.py \
      --campaign-id "$CAMPAIGN_ID" --action d2-prepare-stress

    # Dentro de Mininet:
    # source <ruta mostrada por d2-prepare-stress>

    python3 tfg_sdn/experiments/ch08_ecmp_full_datapath_demo.py \
      --campaign-id "$CAMPAIGN_ID" --action d2-summarize
"""

from __future__ import annotations

import argparse
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


CAMPAIGN_PREFIX = "ch08_fattree_ecmp_full_"
OPENFLOW13 = "OpenFlow13"
GROUP_ID = 200

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

ALL_FATTREE_SWITCHES = [
    "e1", "e2", "e3", "e4", "e5", "e6", "e7", "e8",
    "a1", "a2", "a3", "a4", "a5", "a6", "a7", "a8",
    "c1", "c2", "c3", "c4",
]

D1_SWITCHES = ["e1", "e8", "a1", "a2", "a7", "a8", "c1", "c2", "c3", "c4"]
GROUP_SWITCHES = ["e1", "a1", "a2", "e8", "a7", "a8"]
AGGREGATION_GROUP_SWITCHES = ["a1", "a2", "a7", "a8"]

# Switches esperados para el mapeo bridge -> openflow node con Fat-Tree k=4.
EXPECTED_NODE_IDS = {
    "c1": "openflow:1",
    "c2": "openflow:2",
    "c3": "openflow:3",
    "c4": "openflow:4",
    "a1": "openflow:5",
    "a2": "openflow:6",
    "a3": "openflow:7",
    "a4": "openflow:8",
    "a5": "openflow:9",
    "a6": "openflow:10",
    "a7": "openflow:11",
    "a8": "openflow:12",
    "e1": "openflow:13",
    "e2": "openflow:14",
    "e3": "openflow:15",
    "e4": "openflow:16",
    "e5": "openflow:17",
    "e6": "openflow:18",
    "e7": "openflow:19",
    "e8": "openflow:20",
}


# ---------------------------------------------------------------------------
# Rutas y utilidades generales
# ---------------------------------------------------------------------------


def default_tfg_root() -> Path:
    return Path(
        os.environ.get(
            "TFG_ROOT",
            str(Path(__file__).resolve().parents[2]),
        )
    ).expanduser()


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
    if latest and action not in {"d0-capture"}:
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
    def d0(self) -> Path:
        return self.root / "d0_design"

    @property
    def d1(self) -> Path:
        return self.root / "d1_datapath_ovs"

    @property
    def d2(self) -> Path:
        return self.root / "d2_stress_ovs"

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
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


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


def list_ovs_bridges() -> List[str]:
    proc = sudo_cmd(["ovs-vsctl", "list-br"], check=False)
    if proc.returncode != 0:
        return []
    return sorted([line.strip() for line in proc.stdout.splitlines() if line.strip()])


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


def add_flow(bridge: str, flow: str) -> None:
    sudo_cmd(["ovs-ofctl", "-O", OPENFLOW13, "add-flow", bridge, flow])


def add_group(bridge: str, group: str) -> None:
    sudo_cmd(["ovs-ofctl", "-O", OPENFLOW13, "add-group", bridge, group])


def del_flows_and_groups(bridge: str) -> None:
    sudo_cmd(["ovs-ofctl", "-O", OPENFLOW13, "del-flows", bridge], check=False)
    sudo_cmd(["ovs-ofctl", "-O", OPENFLOW13, "del-groups", bridge], check=False)


def group_line_present(text: str, group_id: int = GROUP_ID) -> bool:
    return f"group_id={group_id}" in text and "type=select" in text


def groups_present_for(bridges: Sequence[str]) -> Dict[str, bool]:
    result: Dict[str, bool] = {}
    for br in bridges:
        proc = sudo_cmd(["ovs-ofctl", "-O", OPENFLOW13, "dump-groups", br], check=False)
        result[br] = proc.returncode == 0 and group_line_present(proc.stdout)
    return result


# ---------------------------------------------------------------------------
# RESTCONF / ODL inventory capture only
# ---------------------------------------------------------------------------


def http_get(url: str, user: str, password: str, timeout: int = 20) -> Tuple[int, str]:
    import base64

    token = base64.b64encode(f"{user}:{password}".encode("utf-8")).decode("ascii")
    req = urllib.request.Request(url, headers={"Authorization": "Basic " + token, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as exc:
        return 0, str(exc)


def capture_odl_inventory(out_dir: Path) -> Dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    host = os.environ.get("ODL_HOST", "127.0.0.1")
    port = os.environ.get("ODL_REST_PORT", "8181")
    user = os.environ.get("ODL_USER", "admin")
    password = os.environ.get("ODL_PASS", "")
    url = f"http://{host}:{port}/rests/data/opendaylight-inventory:nodes?content=nonconfig"

    status, body = http_get(url, user=user, password=password)
    write_text(out_dir / "odl_inventory_nodes.json", body)
    nodes = sorted(set(re.findall(r"openflow:[0-9]+", body)), key=lambda x: int(x.split(":")[1]))
    write_text(out_dir / "odl_openflow_nodes.txt", "\n".join(nodes) + ("\n" if nodes else ""))

    try:
        parsed = json.loads(body)
        write_text(out_dir / "odl_inventory_nodes_pretty.json", json.dumps(parsed, indent=2, ensure_ascii=False))
    except json.JSONDecodeError:
        write_text(out_dir / "odl_inventory_nodes_pretty.json", body)

    meta = {"http_status": status, "url": url, "n_openflow_nodes": len(nodes), "openflow_nodes": nodes}
    write_json(out_dir / "odl_inventory_meta.json", meta)
    return meta


# ---------------------------------------------------------------------------
# Parsers de resultados
# ---------------------------------------------------------------------------


def parse_ping(path: Path) -> Dict[str, Any]:
    text = read_text(path)
    loss_match = re.search(r"(\d+(?:\.\d+)?)% packet loss", text)
    rtt_match = re.search(r"rtt min/avg/max/mdev = [0-9.]+/([0-9.]+)/", text)
    loss = float(loss_match.group(1)) if loss_match else None
    rtt_avg = float(rtt_match.group(1)) if rtt_match else None
    return {
        "path": str(path),
        "packet_loss_pct": loss,
        "rtt_avg_ms": rtt_avg,
        "ping_ok": loss == 0.0,
    }


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
        f"bucket{idx}": {
            "packets": int(packets),
            "bytes": int(bytes_),
            "active": int(packets) > 0,
        }
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


# ---------------------------------------------------------------------------
# Contenido documental D.0
# ---------------------------------------------------------------------------


def d0_candidate_paths_md(campaign_id: str) -> str:
    return f"""# Fase D.0 - Rutas candidatas para ECMP completo en Fat-Tree k=4

## Alcance

Esta fase define el modelo exploratorio para intentar ECMP completo en Fat-Tree k=4. No modifica todavía el datapath y no demuestra rendimiento. Su objetivo es identificar rutas equivalentes, switches implicados y criterios de paso hacia el prototipo datapath de la Fase D.1.

La campaña se identifica como:

`{campaign_id}`

## Par inicial prioritario

Par inicial: h1 <-> h16.

Este par se utiliza como primer caso porque ya fue empleado en la Fase C como demostrador ECMP limitado. En la Fase D se intenta extender ese comportamiento desde un reparto limitado entre dos ramas hacia un modelo con más caminos equivalentes dentro de la jerarquía Fat-Tree.

## Rutas candidatas h1 -> h16

1. h1 -> e1 -> a1 -> c1 -> a7 -> e8 -> h16
2. h1 -> e1 -> a1 -> c2 -> a7 -> e8 -> h16
3. h1 -> e1 -> a2 -> c3 -> a8 -> e8 -> h16
4. h1 -> e1 -> a2 -> c4 -> a8 -> e8 -> h16

## Rutas candidatas h16 -> h1

1. h16 -> e8 -> a7 -> c1 -> a1 -> e1 -> h1
2. h16 -> e8 -> a7 -> c2 -> a1 -> e1 -> h1
3. h16 -> e8 -> a8 -> c3 -> a2 -> e1 -> h1
4. h16 -> e8 -> a8 -> c4 -> a2 -> e1 -> h1

## Switches candidatos para grupos select

- e1: selección inicial hacia a1/a2 en sentido h1 -> h16.
- a1: selección hacia c1/c2.
- a2: selección hacia c3/c4.
- e8: selección inicial hacia a7/a8 en sentido h16 -> h1.
- a7: selección hacia c1/c2.
- a8: selección hacia c3/c4.

## Switches con forwarding determinista previsto

- c1, c2, c3 y c4: forwarding entre la capa core y los pods de destino.
- e1 y e8: entrega local hacia h1 y h16 cuando el tráfico llega al switch de borde destino.
- a1, a2, a7 y a8: combinación de selección ascendente mediante grupos select y forwarding descendente hacia el edge de destino.

## Criterio mínimo para pasar a D.1

Se considera que D.0 permite pasar a D.1 si se cumplen las siguientes condiciones:

- ODL escucha correctamente en los puertos 6653 y 8181.
- La topología Fat-Tree k=4 está activa en Mininet.
- Existen 20 bridges OVS.
- Los 20 bridges OVS están conectados a OpenDaylight.
- El inventario operacional de ODL muestra 20 nodos OpenFlow.
- Existe un mapeo claro entre bridges OVS, DPID y nodos OpenFlow.
- Las rutas h1 <-> h16 se pueden trazar sin ambigüedad a partir de los puertos de la topología.

## Decisión D.0

La evidencia capturada permite pasar a D.1 si el inventario confirma el estado esperado. La siguiente fase consistirá en construir un prototipo datapath mediante ovs-ofctl para comprobar si las rutas equivalentes candidatas pueden activarse de forma estable y con contadores interpretables.
"""


def d0_decision_gate_md() -> str:
    return """# Fase D.0 - Matriz de decisión

## Estado esperado para continuar

- ODL escucha en 6653 y 8181.
- Mininet Fat-Tree k=4 está activo.
- Los 20 bridges OVS están conectados a ODL.
- El inventario operacional de ODL muestra 20 nodos openflow.
- Existe mapeo claro bridge -> DPID -> openflow node.
- Los puertos OVS permiten trazar las rutas candidatas h1 <-> h16.

## Decisión

- PASS: se puede pasar a D.1 y construir un prototipo datapath con ovs-ofctl.
- PARTIAL: se puede intentar una versión reducida con menos grupos select.
- STOP: se detiene la Fase D si el modelo resulta ambiguo o inmanejable.

## Criterios de parada recordados

- Si el diseño se vuelve inmanejable o ambiguo, se detiene y se documenta.
- Si D.1 no consigue conectividad estable en un tiempo razonable, se detiene.
- Si hay conectividad pero no contadores interpretables, no se presenta como ECMP completo.
- Si funciona con ovs-ofctl pero no mediante RESTCONF, se documenta como prototipo datapath no integrado completamente vía controlador.
"""


# ---------------------------------------------------------------------------
# Acciones D.0
# ---------------------------------------------------------------------------


def action_d0_capture(paths: CampaignPaths) -> None:
    require_sudo_cached()
    d0 = paths.d0
    inventory_dir = d0 / "inventory"
    odl_dir = d0 / "odl"
    design_dir = d0 / "design"
    for path in [d0, inventory_dir, odl_dir, design_dir]:
        path.mkdir(parents=True, exist_ok=True)

    ss_proc = run_cmd(["ss", "-lntp"], check=False)
    ovs_show = sudo_cmd(["ovs-vsctl", "show"], check=False)
    bridges = list_ovs_bridges()

    snapshot = [
        "Fase D.0 - Snapshot inicial ECMP completo Fat-Tree k=4",
        "=======================================================",
        datetime.now().isoformat(timespec="seconds"),
        "",
        f"campaign_id={paths.campaign_id}",
        f"TFG_ROOT={paths.tfg_root}",
        "",
        "[Puertos ODL]",
        "\n".join(line for line in ss_proc.stdout.splitlines() if ":6653" in line or ":8181" in line),
        "",
        "[Número de bridges OVS]",
        str(len(bridges)),
        "",
        "[Listado de bridges OVS]",
        "\n".join(bridges),
        "",
        "[Controladores OVS]",
        "\n".join(line for line in ovs_show.stdout.splitlines() if any(token in line for token in ["Bridge", "Controller", "is_connected"])),
        "",
    ]
    write_text(d0 / "d0_environment_snapshot.txt", "\n".join(snapshot))

    mapping_rows = ["bridge,dpid,node_id,expected_node_id,match_expected"]
    mapping: Dict[str, Any] = {}
    for br in bridges:
        dpid = get_bridge_dpid(br)
        node_id = bridge_to_node_id(br)
        expected = EXPECTED_NODE_IDS.get(br, "")
        match = (node_id == expected) if expected else ""
        mapping_rows.append(f"{br},{dpid or ''},{node_id or ''},{expected},{match}")
        mapping[br] = {"dpid": dpid, "node_id": node_id, "expected_node_id": expected, "match_expected": match}
    write_text(d0 / "d0_bridge_node_mapping.csv", "\n".join(mapping_rows) + "\n")
    write_json(d0 / "d0_bridge_node_mapping.json", mapping)

    dump_ovs_switch_state(inventory_dir, ALL_FATTREE_SWITCHES)
    odl_meta = capture_odl_inventory(odl_dir)

    write_text(design_dir / "d0_ecmp_candidate_paths.md", d0_candidate_paths_md(paths.campaign_id))
    write_text(design_dir / "d0_decision_gate.md", d0_decision_gate_md())

    connected_count = ovs_show.stdout.count("is_connected: true")
    expected_switches_present = all(br in bridges for br in ALL_FATTREE_SWITCHES)
    mapping_ok = all(mapping.get(br, {}).get("match_expected") is True for br in EXPECTED_NODE_IDS)
    odl_ok = odl_meta.get("n_openflow_nodes") == 20
    ovs_ok = len(bridges) == 20 and connected_count >= 20 and expected_switches_present

    status = "PASS" if ovs_ok and odl_ok and mapping_ok else "CHECK_REQUIRED"
    summary = {
        "campaign_id": paths.campaign_id,
        "scope": "D.0 design and inventory capture for ECMP full exploratory attempt",
        "status": status,
        "n_bridges": len(bridges),
        "n_is_connected_true": connected_count,
        "expected_switches_present": expected_switches_present,
        "mapping_ok": mapping_ok,
        "odl_inventory": odl_meta,
        "decision": "advance_to_d1" if status == "PASS" else "review_before_d1",
    }
    write_json(d0 / "d0_summary.json", summary)

    print("D.0 capture")
    print("=" * 60)
    print(f"campaign_id: {paths.campaign_id}")
    print(f"d0_root: {d0}")
    print(f"n_bridges: {len(bridges)}")
    print(f"n_is_connected_true: {connected_count}")
    print(f"n_odl_openflow_nodes: {odl_meta.get('n_openflow_nodes')}")
    print(f"mapping_ok: {mapping_ok}")
    print(f"status: {status}")
    print(f"summary: {d0 / 'd0_summary.json'}")


# ---------------------------------------------------------------------------
# Acciones D.1
# ---------------------------------------------------------------------------


def d1_add_base_rules() -> None:
    for br in D1_SWITCHES:
        if not ovs_bridge_exists(br):
            raise RuntimeError(f"Bridge requerido no encontrado: {br}")
        del_flows_and_groups(br)
        add_flow(br, "cookie=0xd100,priority=300,arp,actions=drop")
        add_flow(br, "cookie=0xd100,priority=0,actions=drop")


def d1_add_groups() -> None:
    # e1/e8 seleccionan entre aggregation switches. a1/a2/a7/a8 seleccionan entre core switches.
    group = f"group_id={GROUP_ID},type=select,bucket=actions=output:3,bucket=actions=output:4"
    for br in GROUP_SWITCHES:
        add_group(br, group)


def d1_add_h1_h16_flows() -> None:
    h1 = HOST_MAC["h1"]
    h16 = HOST_MAC["h16"]

    # Edge origen/destino.
    add_flow("e1", f"cookie=0xd101,priority=250,dl_dst={h16},actions=group:{GROUP_ID}")
    add_flow("e1", f"cookie=0xd102,priority=250,dl_dst={h1},actions=output:1")
    add_flow("e8", f"cookie=0xd103,priority=250,dl_dst={h1},actions=group:{GROUP_ID}")
    add_flow("e8", f"cookie=0xd104,priority=250,dl_dst={h16},actions=output:2")

    # Aggregation lado h1.
    add_flow("a1", f"cookie=0xd105,priority=250,dl_dst={h16},actions=group:{GROUP_ID}")
    add_flow("a1", f"cookie=0xd106,priority=250,dl_dst={h1},actions=output:1")
    add_flow("a2", f"cookie=0xd107,priority=250,dl_dst={h16},actions=group:{GROUP_ID}")
    add_flow("a2", f"cookie=0xd108,priority=250,dl_dst={h1},actions=output:1")

    # Aggregation lado h16.
    add_flow("a7", f"cookie=0xd109,priority=250,dl_dst={h16},actions=output:2")
    add_flow("a7", f"cookie=0xd10a,priority=250,dl_dst={h1},actions=group:{GROUP_ID}")
    add_flow("a8", f"cookie=0xd10b,priority=250,dl_dst={h16},actions=output:2")
    add_flow("a8", f"cookie=0xd10c,priority=250,dl_dst={h1},actions=group:{GROUP_ID}")

    # Core. Puertos según net de la topología Fat-Tree k=4.
    for core, cookie_h16, cookie_h1 in [
        ("c1", "0xd10d", "0xd10e"),
        ("c2", "0xd10f", "0xd110"),
        ("c3", "0xd111", "0xd112"),
        ("c4", "0xd113", "0xd114"),
    ]:
        add_flow(core, f"cookie={cookie_h16},priority=250,dl_dst={h16},actions=output:4")
        add_flow(core, f"cookie={cookie_h1},priority=250,dl_dst={h1},actions=output:1")


def action_d1_install(paths: CampaignPaths) -> None:
    require_sudo_cached()
    d1 = paths.d1
    for sub in ["install", "before_traffic", "after_traffic", "connectivity", "iperf", "summary"]:
        (d1 / sub).mkdir(parents=True, exist_ok=True)

    log_lines = [
        "Fase D.1 - Instalación datapath ovs-ofctl",
        "==========================================",
        datetime.now().isoformat(timespec="seconds"),
        f"campaign_id={paths.campaign_id}",
        "",
        "[1] Limpieza de flows y grupos previos",
    ]

    d1_add_base_rules()
    log_lines.append("OK base cleanup + ARP/table-miss drops")

    log_lines.append("[2] Instalación de grupos select")
    d1_add_groups()
    present = groups_present_for(GROUP_SWITCHES)
    log_lines.append(json.dumps(present, indent=2, ensure_ascii=False))

    log_lines.append("[3] Instalación de flows h1 <-> h16")
    d1_add_h1_h16_flows()
    log_lines.append("OK flows h1 <-> h16")

    dump_ovs_switch_state(d1 / "before_traffic", D1_SWITCHES)
    write_text(d1 / "install" / "d1_install_ovs_output.txt", "\n".join(log_lines) + "\n")

    status = "PASS" if all(present.values()) else "CHECK_REQUIRED"
    summary = {
        "campaign_id": paths.campaign_id,
        "scope": "D.1 install datapath ECMP exploratory prototype using ovs-ofctl",
        "status": status,
        "group_id": GROUP_ID,
        "group_switches": GROUP_SWITCHES,
        "groups_present": present,
        "relevant_switches": D1_SWITCHES,
    }
    write_json(d1 / "install" / "d1_install_summary.json", summary)

    print("D.1 install")
    print("=" * 60)
    print(f"campaign_id: {paths.campaign_id}")
    print(f"status: {status}")
    print(f"groups_present: {present}")
    print(f"summary: {d1 / 'install' / 'd1_install_summary.json'}")


def make_mininet_line(host: str, command: str) -> str:
    return f"{host} {command}"


def action_d1_prepare_traffic(paths: CampaignPaths) -> None:
    d1 = paths.d1
    for sub in ["connectivity", "iperf", "after_traffic"]:
        (d1 / sub).mkdir(parents=True, exist_ok=True)

    lines: List[str] = []
    lines += [
        make_mininet_line("h1", "ip neigh flush all"),
        make_mininet_line("h16", "ip neigh flush all"),
        make_mininet_line("h1", f"ip neigh replace {HOST_IP['h16']} lladdr {HOST_MAC['h16']} nud permanent dev h1-eth1"),
        make_mininet_line("h16", f"ip neigh replace {HOST_IP['h1']} lladdr {HOST_MAC['h1']} nud permanent dev h16-eth1"),
        make_mininet_line("h1", f"ip neigh show | tee {d1 / 'connectivity' / 'h1_static_arp.txt'}"),
        make_mininet_line("h16", f"ip neigh show | tee {d1 / 'connectivity' / 'h16_static_arp.txt'}"),
        make_mininet_line("h1", f"ping -c 10 h16 | tee {d1 / 'connectivity' / 'h1_h16_ping.txt'}"),
        make_mininet_line("h16", "pkill -f iperf3"),
    ]

    for port in range(5801, 5805):
        lines.append(make_mininet_line("h16", f"iperf3 -s -p {port} -D"))
    lines.append("sh sleep 1")
    lines.append(make_mininet_line("h16", f"ss -lnt | grep -E ':5801|:5802|:5803|:5804' | tee {d1 / 'iperf' / 'h16_servers.txt'}"))
    for port in range(5801, 5805):
        out = d1 / "iperf" / f"h1_h16_p{port}.json"
        lines.append(make_mininet_line("h1", f"bash -lc 'iperf3 -c {HOST_IP['h16']} -p {port} -t 8 -J > {out}'"))

    for br in GROUP_SWITCHES:
        lines.append(f"sh sudo ovs-ofctl -O {OPENFLOW13} dump-group-stats {br} | tee {d1 / 'after_traffic' / f'{br}_group_stats.txt'}")
    for br in ["e1", "e8"]:
        lines.append(f"sh sudo ovs-ofctl -O {OPENFLOW13} dump-flows {br} | tee {d1 / 'after_traffic' / f'{br}_flows.txt'}")

    cli_path = d1 / "mininet_d1_h1_h16.cli"
    write_text(cli_path, "\n".join(lines) + "\n")

    print("D.1 prepare-traffic")
    print("=" * 60)
    print(f"campaign_id: {paths.campaign_id}")
    print("Ejecuta dentro de Mininet:")
    print(f"source {cli_path}")


def action_d1_summarize(paths: CampaignPaths) -> None:
    d1 = paths.d1
    ping = parse_ping(d1 / "connectivity" / "h1_h16_ping.txt")
    iperf_rows, iperf_ok_rows, throughput_sum = summarize_iperf_dir(d1 / "iperf", "h1_h16_p*.json")
    groups = summarize_groups(d1 / "after_traffic")

    used_group_switches = [br for br, row in groups.items() if row["group_packets"] > 0]
    multi_bucket_switches = [br for br, row in groups.items() if row["active_buckets"] >= 2]
    edges_multibucket = groups["e1"]["active_buckets"] >= 2 and groups["e8"]["active_buckets"] >= 2

    if ping["ping_ok"] and len(iperf_rows) > 0 and len(iperf_ok_rows) == len(iperf_rows) and edges_multibucket:
        status = "PASS_DATAPATH_EXPLORATORY"
    elif ping["ping_ok"] and len(iperf_ok_rows) > 0:
        status = "PARTIAL_DATAPATH"
    else:
        status = "CHECK_REQUIRED"

    summary = {
        "campaign_id": paths.campaign_id,
        "scope": "D.1 datapath ECMP full exploratory prototype using ovs-ofctl",
        "status": status,
        "ping": ping,
        "iperf_total": len(iperf_rows),
        "iperf_ok": len(iperf_ok_rows),
        "throughput_sum_gbps": throughput_sum,
        "group_evidence": groups,
        "used_group_switches": used_group_switches,
        "multi_bucket_switches": multi_bucket_switches,
        "interpretation": (
            "PASS_DATAPATH_EXPLORATORY means that D.1 has datapath-level evidence of multipath usage. "
            "It is still not RESTCONF and not automatically a complete production ECMP implementation."
        ),
    }
    write_json(d1 / "summary" / "d1_datapath_summary.json", summary)

    print("D.1 summarize")
    print("=" * 60)
    print(f"campaign_id: {paths.campaign_id}")
    print(f"status: {status}")
    print(f"ping_ok: {ping['ping_ok']} packet_loss_pct={ping['packet_loss_pct']}")
    print(f"iperf_ok: {len(iperf_ok_rows)}/{len(iperf_rows)}")
    print(f"throughput_sum_gbps: {throughput_sum:.2f}")
    print(f"used_group_switches: {used_group_switches}")
    print(f"multi_bucket_switches: {multi_bucket_switches}")
    print(f"summary: {d1 / 'summary' / 'd1_datapath_summary.json'}")


# ---------------------------------------------------------------------------
# Acciones D.2
# ---------------------------------------------------------------------------


def d2_add_h2_h15_flows() -> None:
    h2 = HOST_MAC["h2"]
    h15 = HOST_MAC["h15"]

    # h2 -> h15.
    add_flow("e1", f"cookie=0xd201,priority=250,dl_dst={h15},actions=group:{GROUP_ID}")
    add_flow("a1", f"cookie=0xd202,priority=250,dl_dst={h15},actions=group:{GROUP_ID}")
    add_flow("a2", f"cookie=0xd203,priority=250,dl_dst={h15},actions=group:{GROUP_ID}")
    add_flow("c1", f"cookie=0xd204,priority=250,dl_dst={h15},actions=output:4")
    add_flow("c2", f"cookie=0xd205,priority=250,dl_dst={h15},actions=output:4")
    add_flow("c3", f"cookie=0xd206,priority=250,dl_dst={h15},actions=output:4")
    add_flow("c4", f"cookie=0xd207,priority=250,dl_dst={h15},actions=output:4")
    add_flow("a7", f"cookie=0xd208,priority=250,dl_dst={h15},actions=output:2")
    add_flow("a8", f"cookie=0xd209,priority=250,dl_dst={h15},actions=output:2")
    add_flow("e8", f"cookie=0xd20a,priority=250,dl_dst={h15},actions=output:1")

    # h15 -> h2.
    add_flow("e8", f"cookie=0xd20b,priority=250,dl_dst={h2},actions=group:{GROUP_ID}")
    add_flow("a7", f"cookie=0xd20c,priority=250,dl_dst={h2},actions=group:{GROUP_ID}")
    add_flow("a8", f"cookie=0xd20d,priority=250,dl_dst={h2},actions=group:{GROUP_ID}")
    add_flow("c1", f"cookie=0xd20e,priority=250,dl_dst={h2},actions=output:1")
    add_flow("c2", f"cookie=0xd20f,priority=250,dl_dst={h2},actions=output:1")
    add_flow("c3", f"cookie=0xd210,priority=250,dl_dst={h2},actions=output:1")
    add_flow("c4", f"cookie=0xd211,priority=250,dl_dst={h2},actions=output:1")
    add_flow("a1", f"cookie=0xd212,priority=250,dl_dst={h2},actions=output:1")
    add_flow("a2", f"cookie=0xd213,priority=250,dl_dst={h2},actions=output:1")
    add_flow("e1", f"cookie=0xd214,priority=250,dl_dst={h2},actions=output:2")


def action_d2_install_extra(paths: CampaignPaths) -> None:
    require_sudo_cached()
    d2 = paths.d2
    for sub in ["install_extra", "before_stress", "connectivity", "iperf", "after_stress", "summary"]:
        (d2 / sub).mkdir(parents=True, exist_ok=True)

    dump_ovs_switch_state(d2 / "before_stress", D1_SWITCHES)
    d2_add_h2_h15_flows()
    dump_ovs_switch_state(d2 / "install_extra", D1_SWITCHES)

    # Comprobamos presencia de reglas de h2/h15.
    expected_macs = [HOST_MAC["h2"], HOST_MAC["h15"]]
    flow_hits: Dict[str, Dict[str, bool]] = {}
    for br in D1_SWITCHES:
        text = read_text(d2 / "install_extra" / f"{br}_flows.txt")
        flow_hits[br] = {mac: mac in text for mac in expected_macs}

    status = "PASS" if any(any(values.values()) for values in flow_hits.values()) else "CHECK_REQUIRED"
    summary = {
        "campaign_id": paths.campaign_id,
        "scope": "D.2 install additional h2-h15 flows over D.1 datapath prototype",
        "status": status,
        "flow_hits": flow_hits,
    }
    write_json(d2 / "install_extra" / "d2_install_extra_summary.json", summary)

    print("D.2 install-extra")
    print("=" * 60)
    print(f"campaign_id: {paths.campaign_id}")
    print(f"status: {status}")
    print(f"summary: {d2 / 'install_extra' / 'd2_install_extra_summary.json'}")


def action_d2_prepare_stress(paths: CampaignPaths) -> None:
    d2 = paths.d2
    for sub in ["connectivity", "iperf", "after_stress"]:
        (d2 / sub).mkdir(parents=True, exist_ok=True)

    lines: List[str] = []
    lines += [
        make_mininet_line("h1", "ip neigh flush all"),
        make_mininet_line("h16", "ip neigh flush all"),
        make_mininet_line("h2", "ip neigh flush all"),
        make_mininet_line("h15", "ip neigh flush all"),
        make_mininet_line("h1", f"ip neigh replace {HOST_IP['h16']} lladdr {HOST_MAC['h16']} nud permanent dev h1-eth1"),
        make_mininet_line("h16", f"ip neigh replace {HOST_IP['h1']} lladdr {HOST_MAC['h1']} nud permanent dev h16-eth1"),
        make_mininet_line("h2", f"ip neigh replace {HOST_IP['h15']} lladdr {HOST_MAC['h15']} nud permanent dev h2-eth1"),
        make_mininet_line("h15", f"ip neigh replace {HOST_IP['h2']} lladdr {HOST_MAC['h2']} nud permanent dev h15-eth1"),
        make_mininet_line("h1", f"ip neigh show | tee {d2 / 'connectivity' / 'h1_static_arp.txt'}"),
        make_mininet_line("h16", f"ip neigh show | tee {d2 / 'connectivity' / 'h16_static_arp.txt'}"),
        make_mininet_line("h2", f"ip neigh show | tee {d2 / 'connectivity' / 'h2_static_arp.txt'}"),
        make_mininet_line("h15", f"ip neigh show | tee {d2 / 'connectivity' / 'h15_static_arp.txt'}"),
        make_mininet_line("h1", f"ping -c 10 h16 | tee {d2 / 'connectivity' / 'h1_h16_ping.txt'}"),
        make_mininet_line("h2", f"ping -c 10 h15 | tee {d2 / 'connectivity' / 'h2_h15_ping.txt'}"),
        make_mininet_line("h16", "pkill -f iperf3"),
        make_mininet_line("h15", "pkill -f iperf3"),
    ]

    for port in range(5810, 5818):
        lines.append(make_mininet_line("h16", f"iperf3 -s -p {port} -D"))
    for port in range(5910, 5918):
        lines.append(make_mininet_line("h15", f"iperf3 -s -p {port} -D"))
    lines.append("sh sleep 1")
    lines.append(make_mininet_line("h16", f"ss -lnt | grep -E ':5810|:5811|:5812|:5813|:5814|:5815|:5816|:5817' | tee {d2 / 'iperf' / 'h16_servers.txt'}"))
    lines.append(make_mininet_line("h15", f"ss -lnt | grep -E ':5910|:5911|:5912|:5913|:5914|:5915|:5916|:5917' | tee {d2 / 'iperf' / 'h15_servers.txt'}"))

    for port in range(5810, 5818):
        out = d2 / "iperf" / f"h1_h16_p{port}.json"
        lines.append(make_mininet_line("h1", f"bash -lc 'iperf3 -c {HOST_IP['h16']} -p {port} -t 6 -J > {out}'"))
    for port in range(5910, 5918):
        out = d2 / "iperf" / f"h2_h15_p{port}.json"
        lines.append(make_mininet_line("h2", f"bash -lc 'iperf3 -c {HOST_IP['h15']} -p {port} -t 6 -J > {out}'"))

    for br in GROUP_SWITCHES:
        lines.append(f"sh sudo ovs-ofctl -O {OPENFLOW13} dump-group-stats {br} | tee {d2 / 'after_stress' / f'{br}_group_stats.txt'}")
    for br in ["e1", "e8", "a1", "a2", "a7", "a8"]:
        lines.append(f"sh sudo ovs-ofctl -O {OPENFLOW13} dump-flows {br} | tee {d2 / 'after_stress' / f'{br}_flows.txt'}")

    cli_path = d2 / "mininet_d2_stress.cli"
    write_text(cli_path, "\n".join(lines) + "\n")

    print("D.2 prepare-stress")
    print("=" * 60)
    print(f"campaign_id: {paths.campaign_id}")
    print("Ejecuta dentro de Mininet:")
    print(f"source {cli_path}")


def action_d2_summarize(paths: CampaignPaths) -> None:
    d2 = paths.d2
    pings = {
        "h1_h16": parse_ping(d2 / "connectivity" / "h1_h16_ping.txt"),
        "h2_h15": parse_ping(d2 / "connectivity" / "h2_h15_ping.txt"),
    }
    iperf_rows, iperf_ok_rows, throughput_sum = summarize_iperf_dir(d2 / "iperf")
    groups = summarize_groups(d2 / "after_stress")

    used_group_switches = [br for br, row in groups.items() if row["group_packets"] > 0]
    multi_bucket_switches = [br for br, row in groups.items() if row["active_buckets"] >= 2]
    aggregation_multi_bucket = [br for br in AGGREGATION_GROUP_SWITCHES if groups[br]["active_buckets"] >= 2]

    all_pings_ok = all(row["ping_ok"] for row in pings.values())
    all_iperf_ok = len(iperf_rows) > 0 and len(iperf_ok_rows) == len(iperf_rows)
    edges_multibucket = groups["e1"]["active_buckets"] >= 2 and groups["e8"]["active_buckets"] >= 2

    if all_pings_ok and all_iperf_ok and edges_multibucket and len(aggregation_multi_bucket) >= 2:
        status = "PASS_DATAPATH_STRESS_STRONG"
    elif all_pings_ok and len(iperf_ok_rows) > 0 and edges_multibucket:
        status = "PARTIAL_DATAPATH_STRESS"
    else:
        status = "CHECK_REQUIRED"

    summary = {
        "campaign_id": paths.campaign_id,
        "scope": "D.2 stress validation for ECMP full exploratory datapath prototype using ovs-ofctl",
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
            "PASS_DATAPATH_STRESS_STRONG requires successful connectivity, valid iperf results, "
            "both edge groups using both buckets, and at least two aggregation groups also using both buckets. "
            "PARTIAL_DATAPATH_STRESS means the prototype remains valid but does not prove full four-path ECMP activation."
        ),
    }
    write_json(d2 / "summary" / "d2_stress_summary.json", summary)

    print("D.2 summarize")
    print("=" * 60)
    print(f"campaign_id: {paths.campaign_id}")
    print(f"status: {status}")
    print(f"pings_ok: {all_pings_ok}")
    print(f"iperf_ok: {len(iperf_ok_rows)}/{len(iperf_rows)}")
    print(f"throughput_sum_gbps: {throughput_sum:.2f}")
    print(f"used_group_switches: {used_group_switches}")
    print(f"multi_bucket_switches: {multi_bucket_switches}")
    print(f"aggregation_multi_bucket_switches: {aggregation_multi_bucket}")
    print(f"summary: {d2 / 'summary' / 'd2_stress_summary.json'}")


# ---------------------------------------------------------------------------
# Status global
# ---------------------------------------------------------------------------


def action_status(paths: CampaignPaths) -> None:
    files = {
        "D.0": paths.d0 / "d0_summary.json",
        "D.1": paths.d1 / "summary" / "d1_datapath_summary.json",
        "D.2": paths.d2 / "summary" / "d2_stress_summary.json",
    }
    rows: Dict[str, Any] = {}
    for key, path in files.items():
        if path.exists():
            try:
                rows[key] = json.loads(read_text(path))
            except json.JSONDecodeError:
                rows[key] = {"status": "UNREADABLE", "path": str(path)}
        else:
            rows[key] = {"status": "MISSING", "path": str(path)}

    print("Fase D status")
    print("=" * 60)
    print(f"campaign_id: {paths.campaign_id}")
    for key, row in rows.items():
        print(f"{key}: {row.get('status')}  ({files[key]})")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Capítulo 8 — Fase D ECMP full exploratory datapath demo.")
    parser.add_argument("--tfg-root", default=str(default_tfg_root()))
    parser.add_argument("--campaign-id", default=None)
    parser.add_argument(
        "--action",
        required=True,
        choices=[
            "d0-capture",
            "d1-install",
            "d1-prepare-traffic",
            "d1-summarize",
            "d2-install-extra",
            "d2-prepare-stress",
            "d2-summarize",
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
        if args.action == "d0-capture":
            action_d0_capture(paths)
        elif args.action == "d1-install":
            action_d1_install(paths)
        elif args.action == "d1-prepare-traffic":
            action_d1_prepare_traffic(paths)
        elif args.action == "d1-summarize":
            action_d1_summarize(paths)
        elif args.action == "d2-install-extra":
            action_d2_install_extra(paths)
        elif args.action == "d2-prepare-stress":
            action_d2_prepare_stress(paths)
        elif args.action == "d2-summarize":
            action_d2_summarize(paths)
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

