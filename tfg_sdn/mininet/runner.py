from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional, Tuple

from tfg_sdn.ovs.ovsctl import OvsCtl, run_sudo_n


@dataclass(frozen=True)
class StartOptions:
    odl_host: str = "127.0.0.1"
    odl_of_port: int = 6653
    pingall: bool = False
    with_cli: bool = True
    clean_first: bool = True
    set_fail_mode_secure: bool = True
    flush_neighbors: bool = True
    auto_static_arp: bool = False

    # Leaf-Spine params (ignored by basic/intermediate)
    spines: int = 2
    leaves: int = 4
    hosts_per_leaf: int = 2

    # Optional link params for Leaf-Spine
    leaf_spine_bw_mbps: Optional[int] = None
    leaf_spine_delay: Optional[str] = None
    host_leaf_bw_mbps: Optional[int] = None
    host_leaf_delay: Optional[str] = None


def require_root_or_sudo() -> None:
    if os.geteuid() != 0:
        raise PermissionError(
            "Mininet start requires root. Run with: sudo -E ./tfgctl start --topo <...>"
        )


def clean_mininet() -> Tuple[bool, str]:
    r = run_sudo_n(["mn", "-c"], timeout_s=90)
    ovs = OvsCtl()
    br = ovs.list_bridges()
    ok = (r.code == 0) and (len(br) == 0)
    msg = r.out if r.out else r.err
    if not ok:
        msg = f"[mn -c rc={r.code}] bridges_after={br} :: {msg}"
    return ok, msg


def _datapath_has_useful_flows(sw) -> bool:
    """
    Heurística: si hay alguna regla priority=200 IPv4, ya hay forwarding.
    Para topologías con loops NO asumimos ARP NORMAL como señal sana.
    """
    out = sw.cmd(f"ovs-ofctl -O OpenFlow13 dump-flows {sw.name}")
    if "priority=200" in out and "ip" in out:
        return True
    return False


def _start_common(topo, opts: StartOptions) -> int:
    require_root_or_sudo()

    from mininet.cli import CLI
    from mininet.log import setLogLevel
    from mininet.net import Mininet
    from mininet.node import OVSKernelSwitch, RemoteController

    setLogLevel("info")

    if opts.clean_first:
        ok, msg = clean_mininet()
        if not ok:
            print("FAIL: clean_mininet() failed:", msg)
            return 2

    net = Mininet(
        topo=topo,
        controller=None,
        switch=OVSKernelSwitch,
        autoSetMacs=False,
        autoStaticArp=False,
        build=True,
    )
    net.addController("c0", controller=RemoteController, ip=opts.odl_host, port=int(opts.odl_of_port))

    try:
        net.start()

        if opts.set_fail_mode_secure:
            for sw in net.switches:
                sw.cmd(f"ovs-vsctl set-fail-mode {sw.name} secure")

        if opts.flush_neighbors:
            for h in net.hosts:
                h.cmd("ip neigh flush all")

        if opts.auto_static_arp:
            net.staticArp()
            print("*** NOTE: staticArp() applied automatically for this topology.")

        if opts.pingall:
            if all(_datapath_has_useful_flows(sw) for sw in net.switches):
                net.pingAll()
            else:
                print("*** NOTE: pingall skipped (fail-mode=secure and no IP flows yet).")
                print("***       Run: tfgctl push-flows ... then (in CLI) pingall, or validate --strict after traffic.")

        if opts.with_cli:
            CLI(net)

        return 0
    finally:
        try:
            net.stop()
        except Exception:
            pass


def start_basic(opts: StartOptions) -> int:
    from tfg_sdn.mininet.topos.basic import BasicTopo
    topo = BasicTopo()
    return _start_common(topo, opts)


def start_intermediate(opts: StartOptions) -> int:
    from tfg_sdn.mininet.topos.intermediate import IntermediateTopo
    topo = IntermediateTopo()
    return _start_common(topo, opts)


def start_leafspine(opts: StartOptions) -> int:
    from tfg_sdn.mininet.topos.leafspine import LeafSpineParams, LeafSpineTopo

    ls_bw = opts.leaf_spine_bw_mbps if opts.leaf_spine_bw_mbps and opts.leaf_spine_bw_mbps > 0 else None
    hl_bw = opts.host_leaf_bw_mbps if opts.host_leaf_bw_mbps and opts.host_leaf_bw_mbps > 0 else None
    ls_delay = opts.leaf_spine_delay if opts.leaf_spine_delay else None
    hl_delay = opts.host_leaf_delay if opts.host_leaf_delay else None

    params = LeafSpineParams(
        spines=int(opts.spines),
        leaves=int(opts.leaves),
        hosts_per_leaf=int(opts.hosts_per_leaf),
        leaf_spine_bw_mbps=ls_bw,
        leaf_spine_delay=ls_delay,
        host_leaf_bw_mbps=hl_bw,
        host_leaf_delay=hl_delay,
    )
    topo = LeafSpineTopo(params)
    return _start_common(topo, opts)
