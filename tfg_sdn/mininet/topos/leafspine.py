#!/usr/bin/env python3
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional

from mininet.topo import Topo


def _dpid_hex(n: int) -> str:
    # OVS expects 16 hex chars (64-bit) for dpid
    if n <= 0:
        raise ValueError("dpid index must be >= 1")
    return f"{n:016x}"


def _mac_from_int(i: int) -> str:
    # deterministic locally-administered MAC: 02:00:00:xx:yy:zz
    if i < 1:
        i = 1
    b1 = (i >> 16) & 0xFF
    b2 = (i >> 8) & 0xFF
    b3 = i & 0xFF
    return f"02:00:00:{b1:02x}:{b2:02x}:{b3:02x}"


@dataclass(frozen=True)
class LeafSpineParams:
    spines: int = 2
    leaves: int = 4
    hosts_per_leaf: int = 2

    # Optional link parameters (only effective if the Mininet net uses TCLink)
    leaf_spine_bw_mbps: Optional[int] = None
    leaf_spine_delay: Optional[str] = None  # e.g., "0.5ms"
    host_leaf_bw_mbps: Optional[int] = None
    host_leaf_delay: Optional[str] = None   # e.g., "5us"

    # Host addressing
    host_cidr: str = "10.0.0.0/24"  # we use 10.0.0.X/24 for hosts


class LeafSpineTopo(Topo):
    """
    Leaf-Spine topology (parametrizable y determinista).

    - spines: número de switches spine
    - leaves: número de switches leaf
    - hosts_per_leaf: hosts por leaf

    Nombres:
      spines: sp1..spS
      leaves: lf1..lfL
      hosts:  h1..hN (N = leaves * hosts_per_leaf)

    DPIDs deterministas:
      spines: openflow:1..openflow:S
      leaves: openflow:(S+1)..openflow:(S+L)

    Puertos deterministas:
      - En cada spine spX:
          downlinks a leaves en puertos 1..L (lf1->port1, lf2->port2, ...)
      - En cada leaf lfY:
          uplinks a spines en puertos 1..S (sp1->port1, sp2->port2, ...)
          hosts en puertos (S+1)..(S+hosts_per_leaf)

    Host IP/MAC deterministas:
      h1 = 10.0.0.1/24, h2 = 10.0.0.2/24, ...
      MAC: 02:00:00:xx:yy:zz (derivada del índice)
    """

    def __init__(self, params: LeafSpineParams | None = None):
        self.params = params or LeafSpineParams()
        super().__init__()

    def build(self) -> None:
        p = self.params

        if p.spines < 1:
            raise ValueError("spines must be >= 1")
        if p.leaves < 1:
            raise ValueError("leaves must be >= 1")
        if p.hosts_per_leaf < 1:
            raise ValueError("hosts_per_leaf must be >= 1")

        # link options (only used when TCLink is the link class)
        ls_opts: Dict[str, object] = {}
        if p.leaf_spine_bw_mbps and p.leaf_spine_bw_mbps > 0:
            ls_opts["bw"] = int(p.leaf_spine_bw_mbps)
            ls_opts["use_htb"] = True
        if p.leaf_spine_delay:
            ls_opts["delay"] = str(p.leaf_spine_delay)

        hl_opts: Dict[str, object] = {}
        if p.host_leaf_bw_mbps and p.host_leaf_bw_mbps > 0:
            hl_opts["bw"] = int(p.host_leaf_bw_mbps)
            hl_opts["use_htb"] = True
        if p.host_leaf_delay:
            hl_opts["delay"] = str(p.host_leaf_delay)

        # Create spines with deterministic dpids
        spine_names = []
        for si in range(1, p.spines + 1):
            name = f"sp{si}"
            dpid = _dpid_hex(si)  # 1..S
            self.addSwitch(
                name,
                protocols="OpenFlow13",
                failMode="secure",
                dpid=dpid,
            )
            spine_names.append(name)

        # Create leaves with deterministic dpids (S+1..S+L)
        leaf_names = []
        for li in range(1, p.leaves + 1):
            name = f"lf{li}"
            dpid = _dpid_hex(p.spines + li)
            self.addSwitch(
                name,
                protocols="OpenFlow13",
                failMode="secure",
                dpid=dpid,
            )
            leaf_names.append(name)

        # Interconnect leaf <-> spine with deterministic porting:
        # spine port = leaf index; leaf port = spine index
        for li, lf in enumerate(leaf_names, start=1):
            for si, sp in enumerate(spine_names, start=1):
                self.addLink(
                    sp,
                    lf,
                    port1=li,   # on spine: 1..L
                    port2=si,   # on leaf:  1..S
                    **ls_opts,
                )

        # Add hosts and connect to leaves
        host_idx = 0
        for li, lf in enumerate(leaf_names, start=1):
            for hi in range(1, p.hosts_per_leaf + 1):
                host_idx += 1
                hname = f"h{host_idx}"
                ip = f"10.0.0.{host_idx}/24"
                mac = _mac_from_int(host_idx)
                self.addHost(hname, ip=ip, mac=mac)

                # host ports on leaf start at (S+1)
                leaf_port = p.spines + hi  # S+1 .. S+hosts_per_leaf
                self.addLink(
                    hname,
                    lf,
                    port2=leaf_port,
                    **hl_opts,
                )


# Mininet CLI integration (optional)
# Allows: sudo mn --custom <file> --topo leafspine
topos = {
    "leafspine": (lambda: LeafSpineTopo()),
}
