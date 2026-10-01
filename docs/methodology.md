# Experimental methodology

The project was developed incrementally: controller/testbed validation first, then Leaf-Spine, then the k=4 Fat-Tree. Experiments retain explicit configuration, repeated traffic measurements and datapath/controller evidence where applicable.

## Evidence hierarchy

1. **Primary evidence — deterministic RESTCONF forwarding.** Installation and validation of destination-oriented OpenFlow rules, followed by point-to-point, concurrent and hotspot traffic scenarios.
2. **Bounded extension — limited ECMP.** OpenFlow `select` groups demonstrate traffic use across two branches in a controlled scope.
3. **Exploratory work — broader ECMP attempt.** Useful engineering evidence, but not sufficient to claim a complete Fat-Tree ECMP implementation.

Only compact processed summaries required to substantiate public claims are stored in `data/results/`. Raw run directories remain excluded because they are bulky and machine-specific.
