from __future__ import annotations

from mininet.topo import Topo

class IntermediateTopo(Topo):
    """
    Intermedia: 2 switches (s1--s2) y 4 hosts (h1,h3 en s1; h2,h4 en s2).
    Puertos FIJOS (forzados explícitamente):
      s1-eth1=h1, s1-eth2=h3, s1-eth3=s2
      s2-eth1=h2, s2-eth2=h4, s2-eth3=s1
    """
    def build(self):
        # Switches OF13 + DPIDs fijos (ODL: openflow:1/openflow:2)
        s1 = self.addSwitch('s1', protocols='OpenFlow13',
                            failMode='secure', dpid='0000000000000001')
        s2 = self.addSwitch('s2', protocols='OpenFlow13',
                            failMode='secure', dpid='0000000000000002')

        # Hosts con IP/MAC fijas
        h1 = self.addHost('h1', ip='10.0.0.1/24', mac='00:00:00:00:00:01')
        h2 = self.addHost('h2', ip='10.0.0.2/24', mac='00:00:00:00:00:02')
        h3 = self.addHost('h3', ip='10.0.0.3/24', mac='00:00:00:00:00:03')
        h4 = self.addHost('h4', ip='10.0.0.4/24', mac='00:00:00:00:00:04')  # FIX

        # Enlaces: fijamos puertos del SWITCH explícitamente (port2= switch-side)
        self.addLink(h1, s1, port2=1)  # s1-eth1
        self.addLink(h3, s1, port2=2)  # s1-eth2
        self.addLink(h2, s2, port2=1)  # s2-eth1
        self.addLink(h4, s2, port2=2)  # s2-eth2

        # Troncal fijada explícitamente en ambos switches
        self.addLink(s1, s2, port1=3, port2=3)  # s1-eth3 <-> s2-eth3

topos = {
    'intermediate': (lambda: IntermediateTopo())
}


