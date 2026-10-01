from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Tuple
from urllib.parse import urlparse

from tfg_sdn.doctor import write_doctor_snapshot
from tfg_sdn.odl.client import OdlAuth, OdlClient
from tfg_sdn.odl.flows import (
    flow_arp_normal,
    flow_table_miss_controller,
    flow_ipv4_dst_to_port,
)
from tfg_sdn.ovs.ovsctl import OvsCtl, run, run_sudo_n
from tfg_sdn.mininet.runner import (
    StartOptions,
    clean_mininet,
    start_basic,
    start_intermediate,
    start_leafspine,
)


@dataclass(frozen=True)
class ValidateConfig:
    odl_host: str = "127.0.0.1"
    odl_rest_port: int = 8181
    odl_of_port: int = 6653
    odl_user: str = "admin"
    odl_pass: str = ""
    strict: bool = False


LEAFSPINE_REMOTE_GID = 100

def _env_first(*names: str, default: str | None = None) -> str | None:
    for name in names:
        value = os.getenv(name)
        if value not in (None, ""):
            return value
    return default


def _default_odl_host() -> str:
    rest_url = os.getenv("ODL_REST", "").strip()
    if rest_url:
        try:
            parsed = urlparse(rest_url)
            if parsed.hostname:
                return parsed.hostname
        except Exception:
            pass
    return _env_first("ODL_HOST", default="127.0.0.1") or "127.0.0.1"


def _default_odl_rest_port() -> int:
    raw = _env_first("ODL_REST_PORT")
    if raw:
        return int(raw)

    rest_url = os.getenv("ODL_REST", "").strip()
    if rest_url:
        try:
            parsed = urlparse(rest_url)
            if parsed.port:
                return int(parsed.port)
        except Exception:
            pass

    return 8181


def _default_odl_of_port() -> int:
    raw = _env_first("ODL_OF_PORT", "ODL_PORT", default="6653") or "6653"
    return int(raw)


def _sudo_ticket_ok() -> bool:
    try:
        p = subprocess.run(
            ["sudo", "-n", "true"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        return p.returncode == 0
    except Exception:
        return False


def _require_sudo_ticket(context: str) -> bool:
    if _sudo_ticket_ok():
        return True

    _fail(f"{context}: no hay credencial sudo activa (este comando usa sudo -n internamente).")
    print("     Ejecuta: sudo -v")
    print("     y vuelve a lanzar el comando.")
    return False

def _ss_listening(port: int) -> bool:
    r = run(["ss", "-lnt"])
    if r.code != 0:
        return False
    return f":{port} " in r.out or f":{port}\n" in r.out or f":{port}\t" in r.out


def _print_section(title: str) -> None:
    print(f"\n==[{title}]==")

def _ok(msg: str) -> None:
    print(f"OK: {msg}")

def _fail(msg: str) -> None:
    print(f"FAIL: {msg}")

def _warn(msg: str) -> None:
    print(f"WARN: {msg}")


def _match_output(flow_line: str, iface: str, port_no: int) -> bool:
    if f'output:"{iface}"' in flow_line:
        return True
    if re.search(rf"\boutput:{port_no}\b", flow_line):
        return True
    return False


# ============================================================
# OVS OFCTL helpers (used for ECMP groups and datapath-only flows)
# ============================================================

def _ofctl_cmd(args: List[str], timeout_s: int = 20):
    return run_sudo_n(["ovs-ofctl", "-O", "OpenFlow13"] + args, timeout_s=timeout_s)


def _ofctl_add_flow(br: str, spec: str) -> None:
    r = _ofctl_cmd(["add-flow", br, spec])
    if r.code != 0:
        raise RuntimeError(f"add-flow failed br={br} spec={spec} err={r.err}")


def _ofctl_del_flows(br: str) -> None:
    r = _ofctl_cmd(["del-flows", br])
    if r.code != 0:
        raise RuntimeError(f"del-flows failed br={br} err={r.err}")


def _ofctl_add_group(br: str, spec: str) -> None:
    r = _ofctl_cmd(["add-group", br, spec])
    if r.code != 0:
        raise RuntimeError(f"add-group failed br={br} spec={spec} err={r.err}")


def _ofctl_del_groups(br: str) -> None:
    r = _ofctl_cmd(["del-groups", br])
    if r.code != 0:
        raise RuntimeError(f"del-groups failed br={br} err={r.err}")


def _ofctl_dump_groups(br: str) -> str:
    r = _ofctl_cmd(["dump-groups", br])
    if r.code != 0:
        return ""
    return r.out


def _ofctl_dump_group_stats(br: str) -> str:
    r = _ofctl_cmd(["dump-group-stats", br])
    if r.code != 0:
        return ""
    return r.out


# ============================================================
# Leaf-Spine helpers
# ============================================================

def _host_ip(host_idx: int) -> str:
    return f"10.0.0.{host_idx}"


def _leaf_for_host(host_idx: int, hosts_per_leaf: int) -> int:
    return ((host_idx - 1) // hosts_per_leaf) + 1


def _host_pos_in_leaf(host_idx: int, hosts_per_leaf: int) -> int:
    return ((host_idx - 1) % hosts_per_leaf) + 1


def _leaf_bridge_name(leaf_idx: int) -> str:
    return f"lf{leaf_idx}"


def _spine_bridge_name(spine_idx: int) -> str:
    return f"sp{spine_idx}"


def _leaf_node_name(spines: int, leaf_idx: int) -> str:
    return f"openflow:{spines + leaf_idx}"


def _spine_node_name(spine_idx: int) -> str:
    return f"openflow:{spine_idx}"


def _baseline_spine_for_destination_leaf(dest_leaf_idx: int, spines: int) -> int:
    return ((dest_leaf_idx - 1) % spines) + 1


def _leaf_remote_uplink_for_profile(dest_leaf_idx: int, spines: int, profile: str) -> int:
    """
    l3_static: reparto fijo por destino (baseline actual)
    single_path: todo destino remoto sale por el uplink 1
    """
    if profile == "single_path":
        return 1
    return _baseline_spine_for_destination_leaf(dest_leaf_idx, spines)


def _build_leafspine_expected(
    spines: int,
    leaves: int,
    hosts_per_leaf: int,
    profile: str = "l3_static",
) -> Tuple[List[str], List[str], Dict[str, Dict[str, Tuple[str, int]]]]:
    total_hosts = leaves * hosts_per_leaf

    expected_br: List[str] = []
    expected_nodes: List[str] = []
    expected_flow_map: Dict[str, Dict[str, Tuple[str, int]]] = {}

    # Spines: direct host routes to destination leaf port
    for si in range(1, spines + 1):
        br = _spine_bridge_name(si)
        expected_br.append(br)
        expected_nodes.append(_spine_node_name(si))
        expected_flow_map[br] = {}
        for hidx in range(1, total_hosts + 1):
            dst_leaf = _leaf_for_host(hidx, hosts_per_leaf)
            port = dst_leaf
            iface = f"{br}-eth{port}"
            expected_flow_map[br][_host_ip(hidx)] = (iface, port)

    # Leaves
    for li in range(1, leaves + 1):
        br = _leaf_bridge_name(li)
        expected_br.append(br)
        expected_nodes.append(_leaf_node_name(spines, li))
        expected_flow_map[br] = {}
        for hidx in range(1, total_hosts + 1):
            dst_leaf = _leaf_for_host(hidx, hosts_per_leaf)
            ip = _host_ip(hidx)

            if dst_leaf == li:
                local_pos = _host_pos_in_leaf(hidx, hosts_per_leaf)
                port = spines + local_pos
                iface = f"{br}-eth{port}"
            else:
                spine_port = _leaf_remote_uplink_for_profile(dst_leaf, spines, profile)
                port = spine_port
                iface = f"{br}-eth{port}"

            expected_flow_map[br][ip] = (iface, port)

    return expected_br, expected_nodes, expected_flow_map


# ---------------- VALIDATE (generic baseline) ----------------

def _validate_common(
    cfg: ValidateConfig,
    expected_br: List[str],
    expected_nodes: List[str],
    expected_flow_map: Dict[str, Dict[str, Tuple[str, int]]],
    require_arp_normal: bool = True,
) -> int:
    ovs = OvsCtl()
    odl = OdlClient(
        host=cfg.odl_host,
        port=cfg.odl_rest_port,
        auth=OdlAuth(cfg.odl_user, cfg.odl_pass),
        timeout_s=20,
    )

    _print_section("1] ODL listening (OF 6653, REST 8181)")
    ok_listen = True
    if _ss_listening(cfg.odl_rest_port):
        _ok(f"REST {cfg.odl_rest_port} listening")
    else:
        _fail(f"REST {cfg.odl_rest_port} not listening")
        ok_listen = False

    if _ss_listening(cfg.odl_of_port):
        _ok(f"OpenFlow {cfg.odl_of_port} listening")
    else:
        _fail(f"OpenFlow {cfg.odl_of_port} not listening")
        ok_listen = False

    _print_section("2] OVS bridge sanity (existencia + fail-mode + controller + is_connected + protocols)")
    if not _require_sudo_ticket("validate"):
        return 2
    
    bridges = ovs.list_bridges()
    if not bridges:
        _fail("No se han encontrado bridges OVS tras validar sudo. ¿Está Mininet levantado?")
        return 2

    missing = [b for b in expected_br if b not in bridges]
    if missing:
        _fail(f"Missing bridges: {missing} (found={bridges})")
        return 2
    _ok(f"Bridges presentes: {' '.join(expected_br)}")

    target = f"tcp:{cfg.odl_host}:{cfg.odl_of_port}"
    ok_ovs = True
    for br in expected_br:
        print(f"-- {br} --")
        fm = ovs.get_fail_mode(br)
        if fm == "secure":
            _ok(f"{br} fail-mode=secure")
        else:
            _fail(f"{br} fail-mode != secure (got={fm})")
            ok_ovs = False

        prot = ovs.get_protocols(br)
        if "OpenFlow13" in prot:
            _ok(f"{br} protocols incluye OpenFlow13")
        else:
            _fail(f"{br} protocols missing OpenFlow13 (got={prot})")
            ok_ovs = False

        ctrl = ovs.get_controller(br)
        if target in ctrl:
            _ok(f"{br} controller includes {target}")
        else:
            _fail(f"{br} controller mismatch (got={ctrl}, expected_contains={target})")
            ok_ovs = False

        is_conn = ovs.controller_is_connected(br, target)
        if is_conn is True:
            _ok(f"{br} controller {target} is_connected=true")
        elif is_conn is False:
            _fail(f"{br} controller {target} is_connected=false")
            ok_ovs = False
        else:
            _warn(f"{br} could not determine is_connected for {target}")
            if cfg.strict:
                ok_ovs = False

    _print_section("3] ODL inventory nodes (operational via content=nonconfig)")
    code, nodes = odl.inventory_nodes()
    ok_inv = False
    if code == 200:
        print(f"HTTP={code}")
        print(f"Nodes: {' '.join(nodes)}")
        if all(n in nodes for n in expected_nodes):
            _ok(f"Inventory contiene {' '.join(expected_nodes)}")
            ok_inv = True
        else:
            _fail(f"Inventory missing expected nodes (got={nodes})")
    else:
        _fail(f"Inventory HTTP={code} (auth/RESTCONF?)")

    _print_section("4] ODL topology (network-topology operational)")
    tcode, tbody = odl.topology_raw()
    ok_topo = False
    if tcode == 200:
        print(f"HTTP={tcode}")
        if "flow:1" in tbody:
            _ok("Topology incluye flow:1")
            ok_topo = True
        else:
            _warn("Topology OK pero no contiene literal 'flow:1' (puede seguir siendo válido)")
            ok_topo = not cfg.strict
    else:
        _fail(f"Topology HTTP={tcode}")

    _print_section("5] Datapath flows present (OVS)")
    ok_flows = True
    ok_counters = True

    for br in expected_br:
        lines = ovs.dump_flows(br).splitlines()
        if not lines:
            _fail(f"{br}: empty dump-flows")
            ok_flows = False
            continue

        has_arp = any(("priority=300" in ln and "arp" in ln and "actions=NORMAL" in ln) for ln in lines)
        has_miss = any(("priority=0" in ln and "actions=CONTROLLER" in ln) for ln in lines)

        if require_arp_normal:
            if has_arp:
                _ok(f"{br}: ARP NORMAL presente")
            else:
                _fail(f"{br}: missing ARP NORMAL")
                ok_flows = False
        else:
            if has_arp:
                _warn(f"{br}: ARP NORMAL presente pero no requerido")
            else:
                _ok(f"{br}: ARP NORMAL no requerido")

        if has_miss:
            _ok(f"{br}: table-miss CONTROLLER presente")
        else:
            _fail(f"{br}: missing table-miss CONTROLLER")
            ok_flows = False

        pkt_sum = 0
        for ip, (iface, pno) in expected_flow_map[br].items():
            found = False
            for ln in lines:
                if "priority=200" in ln and f"ip,nw_dst={ip}" in ln:
                    if _match_output(ln, iface, pno):
                        found = True
                        m = re.search(r"n_packets=(\d+)", ln)
                        if m:
                            pkt_sum += int(m.group(1))
                        break
            if not found:
                _fail(f"{br}: missing dst {ip} -> output({iface} | {pno})")
                ok_flows = False

        if cfg.strict:
            if pkt_sum > 0:
                _ok(f"{br}: counters > 0 (tráfico confirmado usando flows)")
            else:
                _fail(f"{br}: counters == 0 en flows esperados")
                ok_counters = False

    overall_ok = ok_listen and ok_ovs and ok_inv and ok_topo and ok_flows
    if cfg.strict:
        overall_ok = overall_ok and ok_counters

    print()
    if overall_ok and cfg.strict:
        print("PASS+: ODL listening + OVS connected + ODL sees nodes/topology + flows in datapath + counters > 0.")
        return 0
    if overall_ok:
        print("PASS: ODL listening + OVS connected + ODL sees nodes/topology + flows in datapath.")
        return 0

    print("FAIL: validate did not meet required checks.")
    return 1


# ---------------- VALIDATE baseline profiles ----------------

def validate_basic(cfg: ValidateConfig) -> int:
    expected_br = ["s1"]
    expected_nodes = ["openflow:1"]
    expected_flow_map = {
        "s1": {
            "10.0.0.1": ("s1-eth1", 1),
            "10.0.0.2": ("s1-eth2", 2),
        }
    }
    return _validate_common(cfg, expected_br, expected_nodes, expected_flow_map, require_arp_normal=True)


def validate_intermediate(cfg: ValidateConfig) -> int:
    expected_br = ["s1", "s2"]
    expected_nodes = ["openflow:1", "openflow:2"]
    expected_flow_map = {
        "s1": {
            "10.0.0.1": ("s1-eth1", 1),
            "10.0.0.3": ("s1-eth2", 2),
            "10.0.0.2": ("s1-eth3", 3),
            "10.0.0.4": ("s1-eth3", 3),
        },
        "s2": {
            "10.0.0.2": ("s2-eth1", 1),
            "10.0.0.4": ("s2-eth2", 2),
            "10.0.0.1": ("s2-eth3", 3),
            "10.0.0.3": ("s2-eth3", 3),
        },
    }
    return _validate_common(cfg, expected_br, expected_nodes, expected_flow_map, require_arp_normal=True)


def validate_leafspine_static(cfg: ValidateConfig, spines: int, leaves: int, hosts_per_leaf: int) -> int:
    expected_br, expected_nodes, expected_flow_map = _build_leafspine_expected(
        spines=spines,
        leaves=leaves,
        hosts_per_leaf=hosts_per_leaf,
        profile="l3_static",
    )
    return _validate_common(
        cfg,
        expected_br,
        expected_nodes,
        expected_flow_map,
        require_arp_normal=False,
    )


def validate_leafspine_single_path(cfg: ValidateConfig, spines: int, leaves: int, hosts_per_leaf: int) -> int:
    expected_br, expected_nodes, expected_flow_map = _build_leafspine_expected(
        spines=spines,
        leaves=leaves,
        hosts_per_leaf=hosts_per_leaf,
        profile="single_path",
    )

    ovs = OvsCtl()
    odl = OdlClient(
        host=cfg.odl_host,
        port=cfg.odl_rest_port,
        auth=OdlAuth(cfg.odl_user, cfg.odl_pass),
        timeout_s=20,
    )

    _print_section("1] ODL listening (OF 6653, REST 8181)")
    ok_listen = True
    if _ss_listening(cfg.odl_rest_port):
        _ok(f"REST {cfg.odl_rest_port} listening")
    else:
        _fail(f"REST {cfg.odl_rest_port} not listening")
        ok_listen = False

    if _ss_listening(cfg.odl_of_port):
        _ok(f"OpenFlow {cfg.odl_of_port} listening")
    else:
        _fail(f"OpenFlow {cfg.odl_of_port} not listening")
        ok_listen = False

    _print_section("2] OVS bridge sanity (existencia + fail-mode + controller + is_connected + protocols)")
    if not _require_sudo_ticket("validate"):
        return 2
    
    bridges = ovs.list_bridges()
    if not bridges:
        _fail("No se han encontrado bridges OVS tras validar sudo. ¿Está Mininet levantado?")
        return 2

    missing = [b for b in expected_br if b not in bridges]
    if missing:
        _fail(f"Missing bridges: {missing} (found={bridges})")
        return 2
    _ok(f"Bridges presentes: {' '.join(expected_br)}")

    target = f"tcp:{cfg.odl_host}:{cfg.odl_of_port}"
    ok_ovs = True
    for br in expected_br:
        print(f"-- {br} --")
        fm = ovs.get_fail_mode(br)
        if fm == "secure":
            _ok(f"{br} fail-mode=secure")
        else:
            _fail(f"{br} fail-mode != secure (got={fm})")
            ok_ovs = False

        prot = ovs.get_protocols(br)
        if "OpenFlow13" in prot:
            _ok(f"{br} protocols incluye OpenFlow13")
        else:
            _fail(f"{br} protocols missing OpenFlow13 (got={prot})")
            ok_ovs = False

        ctrl = ovs.get_controller(br)
        if target in ctrl:
            _ok(f"{br} controller includes {target}")
        else:
            _fail(f"{br} controller mismatch (got={ctrl}, expected_contains={target})")
            ok_ovs = False

        is_conn = ovs.controller_is_connected(br, target)
        if is_conn is True:
            _ok(f"{br} controller {target} is_connected=true")
        elif is_conn is False:
            _fail(f"{br} controller {target} is_connected=false")
            ok_ovs = False
        else:
            _warn(f"{br} could not determine is_connected for {target}")
            if cfg.strict:
                ok_ovs = False

    _print_section("3] ODL inventory nodes (operational via content=nonconfig)")
    code, nodes = odl.inventory_nodes()
    ok_inv = False
    if code == 200:
        print(f"HTTP={code}")
        print(f"Nodes: {' '.join(nodes)}")
        if all(n in nodes for n in expected_nodes):
            _ok(f"Inventory contiene {' '.join(expected_nodes)}")
            ok_inv = True
        else:
            _fail(f"Inventory missing expected nodes (got={nodes})")
    else:
        _fail(f"Inventory HTTP={code} (auth/RESTCONF?)")

    _print_section("4] ODL topology (network-topology operational)")
    tcode, tbody = odl.topology_raw()
    ok_topo = False
    if tcode == 200:
        print(f"HTTP={tcode}")
        if "flow:1" in tbody:
            _ok("Topology incluye flow:1")
            ok_topo = True
        else:
            _warn("Topology OK pero no contiene literal 'flow:1' (puede seguir siendo válido)")
            ok_topo = not cfg.strict
    else:
        _fail(f"Topology HTTP={tcode}")

    _print_section("5] Datapath flows present (OVS, single_path)")
    ok_flows = True
    ok_counters = True

    primary_spine = _spine_bridge_name(1)

    for br in expected_br:
        lines = ovs.dump_flows(br).splitlines()
        if not lines:
            _fail(f"{br}: empty dump-flows")
            ok_flows = False
            continue

        has_arp = any(("priority=300" in ln and "arp" in ln and "actions=NORMAL" in ln) for ln in lines)
        has_miss = any(("priority=0" in ln and "actions=CONTROLLER" in ln) for ln in lines)

        if has_arp:
            _warn(f"{br}: ARP NORMAL presente pero no requerido")
        else:
            _ok(f"{br}: ARP NORMAL no requerido")

        if has_miss:
            _ok(f"{br}: table-miss CONTROLLER presente")
        else:
            _fail(f"{br}: missing table-miss CONTROLLER")
            ok_flows = False

        pkt_sum = 0
        for ip, (iface, pno) in expected_flow_map[br].items():
            found = False
            for ln in lines:
                if "priority=200" in ln and f"ip,nw_dst={ip}" in ln:
                    if _match_output(ln, iface, pno):
                        found = True
                        m = re.search(r"n_packets=(\d+)", ln)
                        if m:
                            pkt_sum += int(m.group(1))
                        break
            if not found:
                _fail(f"{br}: missing dst {ip} -> output({iface} | {pno})")
                ok_flows = False

        if cfg.strict:
            # En single_path queremos tráfico en los leafs y en el spine primario.
            # Los spines alternativos deben poder quedar a 0 sin considerarse error.
            if br.startswith("lf") or br == primary_spine:
                if pkt_sum > 0:
                    _ok(f"{br}: counters > 0 (tráfico confirmado usando flows)")
                else:
                    _fail(f"{br}: counters == 0 en flows esperados")
                    ok_counters = False
            else:
                # Spine alternativo no usado: esto es correcto en single_path
                if pkt_sum == 0:
                    _ok(f"{br}: counters == 0 (esperado en single_path: spine alternativo sin uso)")
                else:
                    _warn(f"{br}: counters > 0 en spine alternativo ({pkt_sum} paquetes)")

    overall_ok = ok_listen and ok_ovs and ok_inv and ok_topo and ok_flows
    if cfg.strict:
        overall_ok = overall_ok and ok_counters

    print()
    if overall_ok and cfg.strict:
        print("PASS+: ODL listening + OVS connected + ODL sees nodes/topology + single_path flows in datapath + counters coherentes.")
        return 0
    if overall_ok:
        print("PASS: ODL listening + OVS connected + ODL sees nodes/topology + single_path flows in datapath.")
        return 0

    print("FAIL: validate did not meet required checks.")
    return 1


# ---------------- VALIDATE ECMP profile ----------------

def validate_leafspine_ecmp(cfg: ValidateConfig, spines: int, leaves: int, hosts_per_leaf: int) -> int:
    ovs = OvsCtl()
    odl = OdlClient(
        host=cfg.odl_host,
        port=cfg.odl_rest_port,
        auth=OdlAuth(cfg.odl_user, cfg.odl_pass),
        timeout_s=20,
    )

    expected_br = [_spine_bridge_name(i) for i in range(1, spines + 1)] + [_leaf_bridge_name(i) for i in range(1, leaves + 1)]
    expected_nodes = [_spine_node_name(i) for i in range(1, spines + 1)] + [_leaf_node_name(spines, i) for i in range(1, leaves + 1)]
    total_hosts = leaves * hosts_per_leaf

    _print_section("1] ODL listening (OF 6653, REST 8181)")
    ok_listen = True
    if _ss_listening(cfg.odl_rest_port):
        _ok(f"REST {cfg.odl_rest_port} listening")
    else:
        _fail(f"REST {cfg.odl_rest_port} not listening")
        ok_listen = False

    if _ss_listening(cfg.odl_of_port):
        _ok(f"OpenFlow {cfg.odl_of_port} listening")
    else:
        _fail(f"OpenFlow {cfg.odl_of_port} not listening")
        ok_listen = False

    _print_section("2] OVS bridge sanity (existencia + fail-mode + controller + is_connected + protocols)")
    if not _require_sudo_ticket("validate"):
        return 2
    
    bridges = ovs.list_bridges()
    if not bridges:
        _fail("No se han encontrado bridges OVS tras validar sudo. ¿Está Mininet levantado?")
        return 2

    missing = [b for b in expected_br if b not in bridges]
    if missing:
        _fail(f"Missing bridges: {missing} (found={bridges})")
        return 2
    _ok(f"Bridges presentes: {' '.join(expected_br)}")

    target = f"tcp:{cfg.odl_host}:{cfg.odl_of_port}"
    ok_ovs = True
    for br in expected_br:
        print(f"-- {br} --")
        fm = ovs.get_fail_mode(br)
        if fm == "secure":
            _ok(f"{br} fail-mode=secure")
        else:
            _fail(f"{br} fail-mode != secure (got={fm})")
            ok_ovs = False

        prot = ovs.get_protocols(br)
        if "OpenFlow13" in prot:
            _ok(f"{br} protocols incluye OpenFlow13")
        else:
            _fail(f"{br} protocols missing OpenFlow13 (got={prot})")
            ok_ovs = False

        ctrl = ovs.get_controller(br)
        if target in ctrl:
            _ok(f"{br} controller includes {target}")
        else:
            _fail(f"{br} controller mismatch (got={ctrl}, expected_contains={target})")
            ok_ovs = False

        is_conn = ovs.controller_is_connected(br, target)
        if is_conn is True:
            _ok(f"{br} controller {target} is_connected=true")
        elif is_conn is False:
            _fail(f"{br} controller {target} is_connected=false")
            ok_ovs = False
        else:
            _warn(f"{br} could not determine is_connected for {target}")
            if cfg.strict:
                ok_ovs = False

    _print_section("3] ODL inventory nodes (operational via content=nonconfig)")
    code, nodes = odl.inventory_nodes()
    ok_inv = False
    if code == 200:
        print(f"HTTP={code}")
        print(f"Nodes: {' '.join(nodes)}")
        if all(n in nodes for n in expected_nodes):
            _ok(f"Inventory contiene {' '.join(expected_nodes)}")
            ok_inv = True
        else:
            _fail(f"Inventory missing expected nodes (got={nodes})")
    else:
        _fail(f"Inventory HTTP={code} (auth/RESTCONF?)")

    _print_section("4] ODL topology (network-topology operational)")
    tcode, tbody = odl.topology_raw()
    ok_topo = False
    if tcode == 200:
        print(f"HTTP={tcode}")
        if "flow:1" in tbody:
            _ok("Topology incluye flow:1")
            ok_topo = True
        else:
            _warn("Topology OK pero no contiene literal 'flow:1' (puede seguir siendo válido)")
            ok_topo = not cfg.strict
    else:
        _fail(f"Topology HTTP={tcode}")

    _print_section("5] Datapath ECMP flows/groups present (OVS)")
    ok_flows = True
    ok_counters = True

    # Validate spines: static direct output flows
    for si in range(1, spines + 1):
        br = _spine_bridge_name(si)
        lines = ovs.dump_flows(br).splitlines()
        if not lines:
            _fail(f"{br}: empty dump-flows")
            ok_flows = False
            continue

        has_arp = any(("priority=300" in ln and "arp" in ln and "actions=NORMAL" in ln) for ln in lines)
        if has_arp:
            _warn(f"{br}: ARP NORMAL presente pero no requerido")
        else:
            _ok(f"{br}: ARP NORMAL no requerido")

        has_miss = any(("priority=0" in ln and "actions=CONTROLLER" in ln) for ln in lines)
        if has_miss:
            _ok(f"{br}: table-miss CONTROLLER presente")
        else:
            _fail(f"{br}: missing table-miss CONTROLLER")
            ok_flows = False

        pkt_sum = 0
        for hidx in range(1, total_hosts + 1):
            ip = _host_ip(hidx)
            dst_leaf = _leaf_for_host(hidx, hosts_per_leaf)
            iface = f"{br}-eth{dst_leaf}"
            port = dst_leaf

            found = False
            for ln in lines:
                if "priority=200" in ln and f"ip,nw_dst={ip}" in ln and _match_output(ln, iface, port):
                    found = True
                    m = re.search(r"n_packets=(\d+)", ln)
                    if m:
                        pkt_sum += int(m.group(1))
                    break
            if not found:
                _fail(f"{br}: missing dst {ip} -> output({iface} | {port})")
                ok_flows = False

        if cfg.strict:
            if pkt_sum > 0:
                _ok(f"{br}: counters > 0 (tráfico confirmado usando flows)")
            else:
                _fail(f"{br}: counters == 0 en flows esperados")
                ok_counters = False

    # Validate leaves: local outputs + remote group actions + group stats
    for li in range(1, leaves + 1):
        br = _leaf_bridge_name(li)
        lines = ovs.dump_flows(br).splitlines()
        groups = _ofctl_dump_groups(br)
        group_stats = _ofctl_dump_group_stats(br)

        if not lines:
            _fail(f"{br}: empty dump-flows")
            ok_flows = False
            continue

        has_arp = any(("priority=300" in ln and "arp" in ln and "actions=NORMAL" in ln) for ln in lines)
        if has_arp:
            _warn(f"{br}: ARP NORMAL presente pero no requerido")
        else:
            _ok(f"{br}: ARP NORMAL no requerido")

        has_miss = any(("priority=0" in ln and "actions=CONTROLLER" in ln) for ln in lines)
        if has_miss:
            _ok(f"{br}: table-miss CONTROLLER presente")
        else:
            _fail(f"{br}: missing table-miss CONTROLLER")
            ok_flows = False

        group_ok = False
        if f"group_id={LEAFSPINE_REMOTE_GID}" in groups and "type=select" in groups:
            buckets_ok = True
            for si in range(1, spines + 1):
                if f"output:{si}" not in groups and f'output:"{br}-eth{si}"' not in groups:
                    buckets_ok = False
                    break
            if buckets_ok:
                _ok(f"{br}: ECMP group {LEAFSPINE_REMOTE_GID} select presente")
                group_ok = True
            else:
                _fail(f"{br}: ECMP group {LEAFSPINE_REMOTE_GID} sin buckets esperados")
        else:
            _fail(f"{br}: missing ECMP group {LEAFSPINE_REMOTE_GID}")
        if not group_ok:
            ok_flows = False

        pkt_sum_local = 0
        group_pkt_sum = 0

        for hidx in range(1, total_hosts + 1):
            ip = _host_ip(hidx)
            dst_leaf = _leaf_for_host(hidx, hosts_per_leaf)

            if dst_leaf == li:
                local_pos = _host_pos_in_leaf(hidx, hosts_per_leaf)
                port = spines + local_pos
                iface = f"{br}-eth{port}"

                found = False
                for ln in lines:
                    if "priority=200" in ln and f"ip,nw_dst={ip}" in ln and _match_output(ln, iface, port):
                        found = True
                        m = re.search(r"n_packets=(\d+)", ln)
                        if m:
                            pkt_sum_local += int(m.group(1))
                        break
                if not found:
                    _fail(f"{br}: missing local dst {ip} -> output({iface} | {port})")
                    ok_flows = False
            else:
                found = False
                for ln in lines:
                    if "priority=200" in ln and f"ip,nw_dst={ip}" in ln and f"group:{LEAFSPINE_REMOTE_GID}" in ln:
                        found = True
                        m = re.search(r"n_packets=(\d+)", ln)
                        if m:
                            group_pkt_sum += int(m.group(1))
                        break
                if not found:
                    _fail(f"{br}: missing remote dst {ip} -> group:{LEAFSPINE_REMOTE_GID}")
                    ok_flows = False

        mgs = re.search(rf"group_id={LEAFSPINE_REMOTE_GID}.*packet_count=(\d+)", group_stats)
        group_stat_packets = int(mgs.group(1)) if mgs else 0

        if cfg.strict:
            if (pkt_sum_local + group_pkt_sum + group_stat_packets) > 0:
                _ok(f"{br}: counters > 0 (flows/groups en uso)")
            else:
                _fail(f"{br}: counters == 0 en flows/groups esperados")
                ok_counters = False

    overall_ok = ok_listen and ok_ovs and ok_inv and ok_topo and ok_flows
    if cfg.strict:
        overall_ok = overall_ok and ok_counters

    print()
    if overall_ok and cfg.strict:
        print("PASS+: ODL listening + OVS connected + ODL sees nodes/topology + ECMP flows/groups in datapath + counters > 0.")
        return 0
    if overall_ok:
        print("PASS: ODL listening + OVS connected + ODL sees nodes/topology + ECMP flows/groups in datapath.")
        return 0

    print("FAIL: validate did not meet required checks.")
    return 1


# ---------------- PUSH FLOWS ----------------

def push_flows_basic_l3_static(odl: OdlClient, ovs: OvsCtl) -> int:
    _print_section("push-flows] Esperando inventory openflow:1")
    if not odl.wait_for_nodes(["openflow:1"], timeout_s=25, interval_s=1.0):
        _fail("Inventory no contiene openflow:1 dentro de timeout")
        return 1
    _ok("Inventory contiene openflow:1")

    for fid in ["arp_normal", "table_miss", "to_h1_ip", "to_h2_ip"]:
        odl.delete_flow(node="openflow:1", table_id=0, flow_id=fid)

    flows = [
        flow_arp_normal(),
        flow_table_miss_controller(),
        flow_ipv4_dst_to_port("to_h1_ip", "10.0.0.1/32", 1),
        flow_ipv4_dst_to_port("to_h2_ip", "10.0.0.2/32", 2),
    ]

    _print_section("push-flows] Instalando flows BASIC (PUT RESTCONF config datastore)")
    for f in flows:
        code, body = odl.put_flow(node="openflow:1", table_id=0, flow=f)
        if 200 <= code < 300:
            _ok(f"openflow:1 ← {f['id']}")
        else:
            _fail(f"openflow:1 PUT {f['id']} HTTP={code} body={body[:220]}")
            return 1

    print("\nDONE: flows BASIC instalados en config (ODL).")
    return 0


def push_flows_intermediate_l3_static(odl: OdlClient, ovs: OvsCtl) -> int:
    _print_section("push-flows] Esperando inventory openflow:1/openflow:2")
    if not odl.wait_for_nodes(["openflow:1", "openflow:2"], timeout_s=25, interval_s=1.0):
        _fail("Inventory no contiene openflow:1/openflow:2 dentro de timeout")
        return 1
    _ok("Inventory contiene openflow:1 y openflow:2")

    for node in ["openflow:1", "openflow:2"]:
        for fid in ["arp_normal", "table_miss", "to_h1_ip", "to_h2_ip", "to_h3_ip", "to_h4_ip"]:
            odl.delete_flow(node=node, table_id=0, flow_id=fid)

    flows_node_1 = [
        flow_arp_normal(),
        flow_table_miss_controller(),
        flow_ipv4_dst_to_port("to_h1_ip", "10.0.0.1/32", 1),
        flow_ipv4_dst_to_port("to_h3_ip", "10.0.0.3/32", 2),
        flow_ipv4_dst_to_port("to_h2_ip", "10.0.0.2/32", 3),
        flow_ipv4_dst_to_port("to_h4_ip", "10.0.0.4/32", 3),
    ]
    flows_node_2 = [
        flow_arp_normal(),
        flow_table_miss_controller(),
        flow_ipv4_dst_to_port("to_h2_ip", "10.0.0.2/32", 1),
        flow_ipv4_dst_to_port("to_h4_ip", "10.0.0.4/32", 2),
        flow_ipv4_dst_to_port("to_h1_ip", "10.0.0.1/32", 3),
        flow_ipv4_dst_to_port("to_h3_ip", "10.0.0.3/32", 3),
    ]

    _print_section("push-flows] Instalando flows INTERMEDIATE (PUT RESTCONF config datastore)")
    for f in flows_node_1:
        code, body = odl.put_flow(node="openflow:1", table_id=0, flow=f)
        if 200 <= code < 300:
            _ok(f"openflow:1 ← {f['id']}")
        else:
            _fail(f"openflow:1 PUT {f['id']} HTTP={code} body={body[:220]}")
            return 1
    for f in flows_node_2:
        code, body = odl.put_flow(node="openflow:2", table_id=0, flow=f)
        if 200 <= code < 300:
            _ok(f"openflow:2 ← {f['id']}")
        else:
            _fail(f"openflow:2 PUT {f['id']} HTTP={code} body={body[:220]}")
            return 1

    print("\nDONE: flows INTERMEDIATE instalados en config (ODL).")
    return 0


def push_flows_leafspine_l3_static(
    odl: OdlClient,
    ovs: OvsCtl,
    spines: int,
    leaves: int,
    hosts_per_leaf: int,
) -> int:
    expected_br, expected_nodes, _ = _build_leafspine_expected(
        spines=spines,
        leaves=leaves,
        hosts_per_leaf=hosts_per_leaf,
        profile="l3_static",
    )

    _print_section(f"push-flows] Esperando inventory leafspine ({' '.join(expected_nodes)})")
    if not odl.wait_for_nodes(expected_nodes, timeout_s=35, interval_s=1.0):
        _fail(f"Inventory no contiene nodos esperados dentro de timeout: {expected_nodes}")
        return 1
    _ok("Inventory contiene nodos esperados")

    total_hosts = leaves * hosts_per_leaf
    host_flow_ids = [f"to_h{hidx:03d}" for hidx in range(1, total_hosts + 1)]

    for node in expected_nodes:
        for fid in ["arp_normal", "table_miss"] + host_flow_ids:
            odl.delete_flow(node=node, table_id=0, flow_id=fid)

    _print_section("push-flows] Instalando flows LEAFSPINE STATIC (PUT RESTCONF config datastore)")

    for si in range(1, spines + 1):
        node = _spine_node_name(si)
        flows = [flow_table_miss_controller()]
        for hidx in range(1, total_hosts + 1):
            dst_leaf = _leaf_for_host(hidx, hosts_per_leaf)
            out_port = dst_leaf
            flows.append(
                flow_ipv4_dst_to_port(
                    f"to_h{hidx:03d}",
                    f"{_host_ip(hidx)}/32",
                    out_port,
                )
            )
        for f in flows:
            code, body = odl.put_flow(node=node, table_id=0, flow=f)
            if 200 <= code < 300:
                _ok(f"{node} ← {f['id']}")
            else:
                _fail(f"{node} PUT {f['id']} HTTP={code} body={body[:220]}")
                return 1

    for li in range(1, leaves + 1):
        node = _leaf_node_name(spines, li)
        flows = [flow_table_miss_controller()]
        for hidx in range(1, total_hosts + 1):
            dst_leaf = _leaf_for_host(hidx, hosts_per_leaf)
            if dst_leaf == li:
                local_pos = _host_pos_in_leaf(hidx, hosts_per_leaf)
                out_port = spines + local_pos
            else:
                out_port = _baseline_spine_for_destination_leaf(dst_leaf, spines)

            flows.append(
                flow_ipv4_dst_to_port(
                    f"to_h{hidx:03d}",
                    f"{_host_ip(hidx)}/32",
                    out_port,
                )
            )
        for f in flows:
            code, body = odl.put_flow(node=node, table_id=0, flow=f)
            if 200 <= code < 300:
                _ok(f"{node} ← {f['id']}")
            else:
                _fail(f"{node} PUT {f['id']} HTTP={code} body={body[:220]}")
                return 1

    _print_section("push-flows] Verificación OVS (best-effort)")
    for br in expected_br:
        lines = ovs.dump_flows(br).splitlines()
        has_miss = any(("priority=0" in ln and "actions=CONTROLLER" in ln) for ln in lines)
        has_ip = any(("priority=200" in ln and "ip,nw_dst=" in ln) for ln in lines)
        if has_miss and has_ip:
            _ok(f"{br}: table-miss + flows IPv4 presentes")
        else:
            _warn(f"{br}: aún no refleja flows. Ejecuta pingall y valida.")

    print("\nDONE: flows LEAFSPINE STATIC instalados en config/datapath.")
    return 0


def push_flows_leafspine_single_path(
    odl: OdlClient,
    ovs: OvsCtl,
    spines: int,
    leaves: int,
    hosts_per_leaf: int,
) -> int:
    if spines < 1:
        _fail("single_path requiere al menos 1 spine")
        return 1

    expected_br, expected_nodes, _ = _build_leafspine_expected(
        spines=spines,
        leaves=leaves,
        hosts_per_leaf=hosts_per_leaf,
        profile="single_path",
    )

    _print_section(f"push-flows] Esperando inventory leafspine single_path ({' '.join(expected_nodes)})")
    if not odl.wait_for_nodes(expected_nodes, timeout_s=35, interval_s=1.0):
        _fail(f"Inventory no contiene nodos esperados dentro de timeout: {expected_nodes}")
        return 1
    _ok("Inventory contiene nodos esperados")

    total_hosts = leaves * hosts_per_leaf
    host_flow_ids = [f"to_h{hidx:03d}" for hidx in range(1, total_hosts + 1)]

    for node in expected_nodes:
        for fid in ["arp_normal", "table_miss"] + host_flow_ids:
            odl.delete_flow(node=node, table_id=0, flow_id=fid)

    _print_section("push-flows] Instalando flows LEAFSPINE SINGLE_PATH (PUT RESTCONF config datastore)")

    # Spines: igual que en el resto de perfiles, cada spine baja al leaf destino
    for si in range(1, spines + 1):
        node = _spine_node_name(si)
        flows = [flow_table_miss_controller()]
        for hidx in range(1, total_hosts + 1):
            dst_leaf = _leaf_for_host(hidx, hosts_per_leaf)
            out_port = dst_leaf
            flows.append(
                flow_ipv4_dst_to_port(
                    f"to_h{hidx:03d}",
                    f"{_host_ip(hidx)}/32",
                    out_port,
                )
            )
        for f in flows:
            code, body = odl.put_flow(node=node, table_id=0, flow=f)
            if 200 <= code < 300:
                _ok(f"{node} ← {f['id']}")
            else:
                _fail(f"{node} PUT {f['id']} HTTP={code} body={body[:220]}")
                return 1

    # Leaves: tráfico remoto siempre por el uplink 1
    for li in range(1, leaves + 1):
        node = _leaf_node_name(spines, li)
        flows = [flow_table_miss_controller()]
        for hidx in range(1, total_hosts + 1):
            dst_leaf = _leaf_for_host(hidx, hosts_per_leaf)
            if dst_leaf == li:
                local_pos = _host_pos_in_leaf(hidx, hosts_per_leaf)
                out_port = spines + local_pos
            else:
                out_port = 1

            flows.append(
                flow_ipv4_dst_to_port(
                    f"to_h{hidx:03d}",
                    f"{_host_ip(hidx)}/32",
                    out_port,
                )
            )
        for f in flows:
            code, body = odl.put_flow(node=node, table_id=0, flow=f)
            if 200 <= code < 300:
                _ok(f"{node} ← {f['id']}")
            else:
                _fail(f"{node} PUT {f['id']} HTTP={code} body={body[:220]}")
                return 1

    _print_section("push-flows] Verificación OVS (best-effort)")
    for br in expected_br:
        lines = ovs.dump_flows(br).splitlines()
        has_miss = any(("priority=0" in ln and "actions=CONTROLLER" in ln) for ln in lines)
        has_ip = any(("priority=200" in ln and "ip,nw_dst=" in ln) for ln in lines)
        if has_miss and has_ip:
            _ok(f"{br}: table-miss + flows IPv4 presentes")
        else:
            _warn(f"{br}: aún no refleja flows. Ejecuta pingall y valida.")

    print("\nDONE: flows LEAFSPINE SINGLE_PATH instalados en config/datapath.")
    return 0


def push_flows_leafspine_ecmp(
    odl: OdlClient,
    ovs: OvsCtl,
    spines: int,
    leaves: int,
    hosts_per_leaf: int,
) -> int:
    expected_br = [_spine_bridge_name(i) for i in range(1, spines + 1)] + [_leaf_bridge_name(i) for i in range(1, leaves + 1)]
    expected_nodes = [_spine_node_name(i) for i in range(1, spines + 1)] + [_leaf_node_name(spines, i) for i in range(1, leaves + 1)]

    _print_section(f"push-flows] Esperando inventory leafspine ECMP ({' '.join(expected_nodes)})")
    if not odl.wait_for_nodes(expected_nodes, timeout_s=35, interval_s=1.0):
        _fail(f"Inventory no contiene nodos esperados dentro de timeout: {expected_nodes}")
        return 1
    _ok("Inventory contiene nodos esperados")

    total_hosts = leaves * hosts_per_leaf
    host_flow_ids = [f"to_h{hidx:03d}" for hidx in range(1, total_hosts + 1)]

    for node in expected_nodes:
        for fid in ["arp_normal", "table_miss"] + host_flow_ids:
            odl.delete_flow(node=node, table_id=0, flow_id=fid)

    _print_section("push-flows] Limpieza datapath OVS (flows + groups)")
    for br in expected_br:
        _ofctl_del_groups(br)
        _ofctl_del_flows(br)
        _ok(f"{br}: datapath limpio")

    _print_section("push-flows] Instalando LEAFSPINE ECMP (OVS datapath via ovs-ofctl)")

    for si in range(1, spines + 1):
        br = _spine_bridge_name(si)

        _ofctl_add_flow(br, "table=0,priority=0 actions=CONTROLLER:65535")
        _ok(f"{br}: table_miss")

        for hidx in range(1, total_hosts + 1):
            ip = _host_ip(hidx)
            dst_leaf = _leaf_for_host(hidx, hosts_per_leaf)
            _ofctl_add_flow(
                br,
                f"table=0,priority=200,ip,nw_dst={ip} actions=output:{dst_leaf}",
            )
        _ok(f"{br}: host routes IPv4 instaladas")

    group_spec = f"group_id={LEAFSPINE_REMOTE_GID},type=select," + ",".join(
        [f"bucket=output:{si}" for si in range(1, spines + 1)]
    )

    for li in range(1, leaves + 1):
        br = _leaf_bridge_name(li)

        _ofctl_add_group(br, group_spec)
        _ok(f"{br}: group {LEAFSPINE_REMOTE_GID} select")

        _ofctl_add_flow(br, "table=0,priority=0 actions=CONTROLLER:65535")
        _ok(f"{br}: table_miss")

        for hidx in range(1, total_hosts + 1):
            ip = _host_ip(hidx)
            dst_leaf = _leaf_for_host(hidx, hosts_per_leaf)

            if dst_leaf == li:
                local_pos = _host_pos_in_leaf(hidx, hosts_per_leaf)
                out_port = spines + local_pos
                _ofctl_add_flow(
                    br,
                    f"table=0,priority=200,ip,nw_dst={ip} actions=output:{out_port}",
                )
            else:
                _ofctl_add_flow(
                    br,
                    f"table=0,priority=200,ip,nw_dst={ip} actions=group:{LEAFSPINE_REMOTE_GID}",
                )

        _ok(f"{br}: local routes + remote ECMP routes instaladas")

    _print_section("push-flows] Verificación OVS (best-effort)")
    for si in range(1, spines + 1):
        br = _spine_bridge_name(si)
        lines = ovs.dump_flows(br).splitlines()
        has_miss = any(("priority=0" in ln and "actions=CONTROLLER" in ln) for ln in lines)
        has_ip = any(("priority=200" in ln and "ip,nw_dst=" in ln) for ln in lines)
        if has_miss and has_ip:
            _ok(f"{br}: table-miss + flows IPv4 presentes")
        else:
            _warn(f"{br}: verificación incompleta")

    for li in range(1, leaves + 1):
        br = _leaf_bridge_name(li)
        lines = ovs.dump_flows(br).splitlines()
        groups = _ofctl_dump_groups(br)
        has_miss = any(("priority=0" in ln and "actions=CONTROLLER" in ln) for ln in lines)
        has_group_flows = any((f"group:{LEAFSPINE_REMOTE_GID}" in ln) for ln in lines)
        has_group = f"group_id={LEAFSPINE_REMOTE_GID}" in groups and "type=select" in groups
        if has_miss and has_group_flows and has_group:
            _ok(f"{br}: table-miss + flows ECMP + group select presentes")
        else:
            _warn(f"{br}: verificación incompleta")

    print("\nDONE: flows LEAFSPINE ECMP instalados en datapath.")
    return 0


# ---------------- MININET WIRING ----------------

def cmd_clean() -> int:
    _print_section("clean] Mininet cleanup (mn -c) + verificación bridges vacíos")
    ok, msg = clean_mininet()
    if ok:
        _ok("Cleanup OK (sin bridges)")
        return 0
    _fail(msg)
    return 1


def cmd_start(args: argparse.Namespace) -> int:
    auto_static_arp = (args.topo == "leafspine")

    opts = StartOptions(
        odl_host=args.odl_host,
        odl_of_port=args.odl_of_port,
        pingall=bool(args.pingall),
        with_cli=(not bool(args.no_cli)),
        clean_first=(not bool(args.no_clean)),
        set_fail_mode_secure=True,
        flush_neighbors=True,
        auto_static_arp=auto_static_arp,
        spines=int(getattr(args, "spines", 2)),
        leaves=int(getattr(args, "leaves", 4)),
        hosts_per_leaf=int(getattr(args, "hpl", 2)),
        leaf_spine_bw_mbps=int(getattr(args, "ls_bw_mbps", 0)) or None,
        leaf_spine_delay=(getattr(args, "ls_delay", "") or None),
        host_leaf_bw_mbps=int(getattr(args, "hl_bw_mbps", 0)) or None,
        host_leaf_delay=(getattr(args, "hl_delay", "") or None),
    )
    if args.topo == "basic":
        return start_basic(opts)
    if args.topo == "intermediate":
        return start_intermediate(opts)
    if args.topo == "leafspine":
        return start_leafspine(opts)
    _fail("Topología no soportada aún")
    return 2


# ---------------- RUN / SUITE ----------------

def cmd_run(args: argparse.Namespace) -> int:
    if args.exp == "expA":
        from tfg_sdn.experiments.expA import ExpAConfig, run_expA
        cfg = ExpAConfig(
            topo=args.topo,
            odl_host=args.odl_host,
            odl_of_port=args.odl_of_port,
            odl_rest_port=args.odl_rest_port,
            odl_user=args.odl_user,
            odl_pass=args.odl_pass,
            dur_s=args.dur,
            ping_count=args.ping_count,
            proto=args.proto,
            udp_rate_mbps=args.udp_rate_mbps,
            link_bw_mbps=args.link_bw_mbps,
        )
        manifest = run_expA(cfg)
        print(f"\nDONE: expA finished. manifest={manifest}")
        return 0
    _fail("Experiment not supported yet")
    return 2


def cmd_suite(args: argparse.Namespace) -> int:
    if args.exp != "expA":
        _fail("Only expA supported in suite for now")
        return 2

    from tfg_sdn.experiments.suite_expA import SuiteExpAConfig, run_suite_expA

    udp_rates = [int(x.strip()) for x in args.udp_rates.split(",") if x.strip()]
    scfg = SuiteExpAConfig(
        topo=args.topo,
        reps=args.rep,
        proto=args.proto,
        dur_s=args.dur,
        ping_count=args.ping_count,
        link_bw_mbps=args.link_bw_mbps,
        udp_rates_mbps=udp_rates,
        reset_csv=bool(args.reset_csv),
        odl_host=args.odl_host,
        odl_of_port=args.odl_of_port,
        odl_rest_port=args.odl_rest_port,
        odl_user=args.odl_user,
        odl_pass=args.odl_pass,
    )
    suite_manifest = run_suite_expA(scfg)
    print(f"\nDONE: suite expA finished. suite_manifest={os.path.join('runs', suite_manifest['suite_id'], 'suite_manifest.json')}")
    print(f"Summary CSV: {suite_manifest['outputs']['w2_summary_csv']}")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    root = Path(__file__).resolve().parents[1]
    runs = root / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    doctor_id = datetime_now_compact()
    out_dir = runs / f"{doctor_id}_doctor"
    jpath = write_doctor_snapshot(out_dir)
    print(f"DONE: doctor snapshot written to {jpath}")
    print(f" - {out_dir / 'doctor.txt'}")
    return 0


def datetime_now_compact() -> str:
    from datetime import datetime
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def main(argv: List[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]

    p = argparse.ArgumentParser(prog="tfgctl", description="TFG-SDN automation CLI (Python)")
    sub = p.add_subparsers(dest="cmd", required=True)

    # doctor
    sub.add_parser("doctor", help="Write reproducibility snapshot (versions, env, network)")

    # validate
    p_val = sub.add_parser("validate", help="Validate ODL+OVS+topology+flows")
    p_val.add_argument("--topo", default="intermediate", choices=["basic", "intermediate", "leafspine"])
    p_val.add_argument("--profile", default="l3_static", choices=["l3_static", "single_path", "ecmp"])
    p_val.add_argument("--strict", action="store_true")
    p_val.add_argument("--odl-host", default=_default_odl_host())
    p_val.add_argument("--odl-rest-port", type=int, default=_default_odl_rest_port())
    p_val.add_argument("--odl-of-port", type=int, default=_default_odl_of_port())
    p_val.add_argument("--odl-user", default=os.getenv("ODL_USER", "admin"))
    p_val.add_argument("--odl-pass", default=os.getenv("ODL_PASS", ""))
    p_val.add_argument("--spines", type=int, default=2)
    p_val.add_argument("--leaves", type=int, default=4)
    p_val.add_argument("--hpl", type=int, default=2)

    # push-flows
    p_push = sub.add_parser("push-flows", help="Push flows to switches via ODL/OVS")
    p_push.add_argument("--topo", default="intermediate", choices=["basic", "intermediate", "leafspine"])
    p_push.add_argument("--profile", default="l3_static", choices=["l3_static", "single_path", "ecmp"])
    p_push.add_argument("--odl-host", default=_default_odl_host())
    p_push.add_argument("--odl-rest-port", type=int, default=_default_odl_rest_port())
    p_push.add_argument("--odl-user", default=os.getenv("ODL_USER", "admin"))
    p_push.add_argument("--odl-pass", default=os.getenv("ODL_PASS", ""))
    p_push.add_argument("--spines", type=int, default=2)
    p_push.add_argument("--leaves", type=int, default=4)
    p_push.add_argument("--hpl", type=int, default=2)

    # clean
    sub.add_parser("clean", help="Cleanup Mininet/OVS state (mn -c)")

    # start
    p_start = sub.add_parser("start", help="Start Mininet topology via Python API")
    p_start.add_argument("--topo", default="intermediate", choices=["basic", "intermediate", "leafspine"])
    p_start.add_argument("--odl-host", default=_default_odl_host())
    p_start.add_argument("--odl-of-port", type=int, default=_default_odl_of_port())
    p_start.add_argument("--pingall", action="store_true")
    p_start.add_argument("--no-cli", action="store_true")
    p_start.add_argument("--no-clean", action="store_true")
    p_start.add_argument("--spines", type=int, default=2)
    p_start.add_argument("--leaves", type=int, default=4)
    p_start.add_argument("--hpl", type=int, default=2)
    p_start.add_argument("--ls-bw-mbps", type=int, default=0)
    p_start.add_argument("--ls-delay", default="")
    p_start.add_argument("--hl-bw-mbps", type=int, default=0)
    p_start.add_argument("--hl-delay", default="")

    # run
    p_run = sub.add_parser("run", help="Run experiments (expA first)")
    p_run.add_argument("exp", choices=["expA"])
    p_run.add_argument("--topo", default="basic", choices=["basic", "intermediate"])
    p_run.add_argument("--proto", default="both", choices=["tcp", "udp", "both"])
    p_run.add_argument("--dur", type=int, default=10)
    p_run.add_argument("--ping-count", type=int, default=20)
    p_run.add_argument("--udp-rate-mbps", type=int, default=200)
    p_run.add_argument("--link-bw-mbps", type=int, default=0)
    p_run.add_argument("--odl-host", default=_default_odl_host())
    p_run.add_argument("--odl-of-port", type=int, default=_default_odl_of_port())
    p_run.add_argument("--odl-rest-port", type=int, default=_default_odl_rest_port())
    p_run.add_argument("--odl-user", default=os.getenv("ODL_USER", "admin"))
    p_run.add_argument("--odl-pass", default=os.getenv("ODL_PASS", ""))

    # suite
    p_suite = sub.add_parser("suite", help="Run experiment suites (matrix + reps)")
    p_suite.add_argument("exp", choices=["expA"])
    p_suite.add_argument("--topo", default="basic", choices=["basic", "intermediate"])
    p_suite.add_argument("--rep", type=int, default=5)
    p_suite.add_argument("--proto", default="both", choices=["tcp", "udp", "both"])
    p_suite.add_argument("--dur", type=int, default=10)
    p_suite.add_argument("--ping-count", type=int, default=20)
    p_suite.add_argument("--udp-rates", default="50,100,200,500", help="Comma list, e.g. 50,100,200,500")
    p_suite.add_argument("--link-bw-mbps", type=int, default=1000)
    p_suite.add_argument("--reset-csv", action="store_true")
    p_suite.add_argument("--odl-host", default=_default_odl_host())
    p_suite.add_argument("--odl-of-port", type=int, default=_default_odl_of_port())
    p_suite.add_argument("--odl-rest-port", type=int, default=_default_odl_rest_port())
    p_suite.add_argument("--odl-user", default=os.getenv("ODL_USER", "admin"))
    p_suite.add_argument("--odl-pass", default=os.getenv("ODL_PASS", ""))

    args = p.parse_args(argv)

    if args.cmd == "doctor":
        return cmd_doctor(args)

    if args.cmd == "clean":
        return cmd_clean()

    if args.cmd == "start":
        return cmd_start(args)

    if args.cmd == "push-flows":
        odl = OdlClient(
            host=args.odl_host,
            port=args.odl_rest_port,
            auth=OdlAuth(args.odl_user, args.odl_pass),
            timeout_s=20,
        )
        ovs = OvsCtl()

        if args.topo in ("basic", "intermediate") and args.profile != "l3_static":
            _fail(f"profile={args.profile} no soportado para topo={args.topo}")
            return 2

        if args.topo == "basic":
            return push_flows_basic_l3_static(odl, ovs)

        if args.topo == "intermediate":
            return push_flows_intermediate_l3_static(odl, ovs)

        if args.topo == "leafspine":
            if args.profile == "l3_static":
                return push_flows_leafspine_l3_static(
                    odl=odl,
                    ovs=ovs,
                    spines=int(args.spines),
                    leaves=int(args.leaves),
                    hosts_per_leaf=int(args.hpl),
                )
            if args.profile == "single_path":
                return push_flows_leafspine_single_path(
                    odl=odl,
                    ovs=ovs,
                    spines=int(args.spines),
                    leaves=int(args.leaves),
                    hosts_per_leaf=int(args.hpl),
                )
            if args.profile == "ecmp":
                return push_flows_leafspine_ecmp(
                    odl=odl,
                    ovs=ovs,
                    spines=int(args.spines),
                    leaves=int(args.leaves),
                    hosts_per_leaf=int(args.hpl),
                )

        _fail("Topología no soportada aún")
        return 2

    if args.cmd == "validate":
        cfg = ValidateConfig(
            odl_host=args.odl_host,
            odl_rest_port=args.odl_rest_port,
            odl_of_port=args.odl_of_port,
            odl_user=args.odl_user,
            odl_pass=args.odl_pass,
            strict=bool(args.strict),
        )

        if args.topo in ("basic", "intermediate") and args.profile != "l3_static":
            _fail(f"profile={args.profile} no soportado para topo={args.topo}")
            return 2

        if args.topo == "basic":
            return validate_basic(cfg)

        if args.topo == "intermediate":
            return validate_intermediate(cfg)

        if args.topo == "leafspine":
            if args.profile == "l3_static":
                return validate_leafspine_static(
                    cfg=cfg,
                    spines=int(args.spines),
                    leaves=int(args.leaves),
                    hosts_per_leaf=int(args.hpl),
                )
            if args.profile == "single_path":
                return validate_leafspine_single_path(
                    cfg=cfg,
                    spines=int(args.spines),
                    leaves=int(args.leaves),
                    hosts_per_leaf=int(args.hpl),
                )
            if args.profile == "ecmp":
                return validate_leafspine_ecmp(
                    cfg=cfg,
                    spines=int(args.spines),
                    leaves=int(args.leaves),
                    hosts_per_leaf=int(args.hpl),
                )

        _fail("Topología no soportada aún")
        return 2

    if args.cmd == "run":
        return cmd_run(args)

    if args.cmd == "suite":
        return cmd_suite(args)

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
