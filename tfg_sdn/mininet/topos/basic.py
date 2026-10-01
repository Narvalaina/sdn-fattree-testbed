from __future__ import annotations

from mininet.topo import Topo


class BasicTopo(Topo):
    """
    Básica: 1 switch (s1) y 2 hosts (h1,h2).
    Puertos fijos:
      s1-eth1 = h1
      s1-eth2 = h2
    DPID fijo: openflow:1
    """

    def build(self) -> None:
        s1 = self.addSwitch(
            "s1",
            protocols="OpenFlow13",
            failMode="secure",
            dpid="0000000000000001",
        )

        h1 = self.addHost(
            "h1",
            ip="10.0.0.1/24",
            mac="00:00:00:00:00:01",
        )
        h2 = self.addHost(
            "h2",
            ip="10.0.0.2/24",
            mac="00:00:00:00:00:02",
        )

        # Puertos explícitos para mantener numeración estable
        self.addLink(h1, s1, port2=1)
        self.addLink(h2, s1, port2=2)


# Registro para Mininet CLI: --custom basic.py --topo basic
topos = {
    "basic": (lambda: BasicTopo()),
}


