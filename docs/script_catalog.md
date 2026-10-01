# Experiment script catalog

The repository preserves thesis-era script names to retain traceability. Not every script has the same execution status or dependency profile.

| Script | Role | Requirements / status |
|---|---|---|
| `expA.py` | Basic/intermediate validation experiment | Native Linux + Mininet/OVS + ODL for full execution |
| `suite_expA.py` | Repeated ExpA campaign driver | Same environment as `expA.py` |
| `leafspine_compare.py` | Controlled Leaf-Spine comparison | Native Linux + Mininet/OVS + ODL |
| `ch06_ft68_functional_forwarding.py` | Representative Fat-Tree functional forwarding validation | Native Linux + Mininet/OVS + ODL |
| `ch08_preflight_collect.py` | Fat-Tree preflight/controller/datapath evidence collection | Native Linux + OVS + running ODL/Mininet instance |
| `ch08_install_restconf_forwarding.py` | Primary deterministic RESTCONF forwarding installation | Native Linux + OVS + ODL |
| `ch08_restconf_preflight_validate.py` | Prepare/summarize deterministic campaign preflight | Requires the associated campaign/run structure |
| `ch08_restconf_p2p_tests.py` | Point-to-point campaign stage | Requires active testbed and campaign/run structure |
| `ch08_restconf_concurrent_tests.py` | Concurrent distributed campaign stage | Requires active testbed and campaign/run structure |
| `ch08_restconf_hotspot_tests.py` | Hotspot campaign stage | Requires active testbed and campaign/run structure |
| `ch08_ecmp_limited_demo.py` | Bounded datapath-level `select`-group demonstrator | Native Linux + OVS; direct `ovs-ofctl` programming |
| `ch08_restconf_group_select_demo.py` | Bounded RESTCONF `select`-group demonstrator | Native Linux + OVS + ODL |
| `ch08_process_restconf_results.py` | Thesis result processor | Requires historical/raw campaign artefacts not committed here |
| `ch08_process_ecmp_phase_c.py` | Limited-ECMP result processor | Requires historical/raw campaign artefacts not committed here |
| `ch08_process_ecmp_c2_restconf.py` | RESTCONF limited-ECMP result processor | Requires historical/raw campaign artefacts not committed here |
| `ch08_ecmp_full_datapath_demo.py` | Broader ECMP datapath prototype | **Exploratory**; native Linux + OVS |
| `ch08_ecmp_full_restconf_attempt.py` | RESTCONF migration of broader ECMP prototype | **Exploratory/non-conclusive**; native Linux + ODL/OVS |
| `ch08_process_ecmp_full_phase_d.py` | Processor for Phase D evidence | **Exploratory**; requires historical/raw campaign artefacts not committed here |

The public repository deliberately keeps processors that require private/raw inputs because they document how the published compact summaries were produced. Their presence does **not** imply that every historical raw run is redistributed.

## Release validation utility

- `tools/native_linux_release_check.sh` — native-Linux preflight for public-safety audit, compile, CLI checks, portable tests, environment capture and deterministic Fat-Tree metadata regression. It does not replace the manual Mininet/OpenDaylight functional gates.
