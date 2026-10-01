# Selected results

This document intentionally reports only results supported by the compact evidence committed under `data/results/`.

## Leaf-Spine controlled comparison

For the principal `single_path` versus ECMP comparison (five repetitions, six successful flows per repetition):

- `single_path`: **95.437 Mbit/s** mean aggregate throughput; lf1 uplink utilization approximately **96.402% / 0.0004%**.
- ECMP: **191.144 Mbit/s** mean aggregate throughput; lf1 uplink utilization approximately **96.224% / 96.194%**.

The separate L3 static-by-destination versus ECMP comparison produced similar aggregate throughput: **191.144 Mbit/s** and **191.136 Mbit/s**, respectively. This supports a comparison of forwarding mechanisms rather than a claim that ECMP always increases capacity.

## Fat-Tree deterministic RESTCONF campaign

`ch08_restconf_campaign_status.csv` records PASS for the complete deterministic campaign blocks:

- FT8.R — flow installation: 84/84 checks.
- FT8.2R — preflight: 6/6.
- FT8.3R — point-to-point: 9/9.
- FT8.4R — concurrent distributed: 12/12.
- FT8.5R — hotspot: 12/12.

The compact traffic summary records mean receiver aggregate throughput of approximately **30.343 Gbit/s** for the distributed concurrent scenario and **29.906 Gbit/s** for the hotspot scenario on the local emulated testbed. These are testbed measurements, not production-network capacity claims.

## Limited ECMP demonstrator

Both `single_path` and `select_group` modes passed connectivity/traffic checks. In `select_group` mode, forward traffic was split approximately **57.68% / 42.32%** across the two observed branches; reverse traffic was approximately **58.34% / 41.66%**.

## Exploratory broader-ECMP work

The Phase D data remains explicitly exploratory. The processed status labels include `PASS_DATAPATH_EXPLORATORY`, `PARTIAL_DATAPATH_STRESS`, `PARTIAL_RESTCONF_TRAFFIC` and `PARTIAL_RESTCONF_STRESS`. These results are retained to document engineering iteration and must not be presented as validation of a complete Fat-Tree ECMP solution.
