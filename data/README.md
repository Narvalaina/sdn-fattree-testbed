# Public evidence dataset

This directory contains only the compact topology metadata and processed experiment summaries selected to support the public claims in this repository. It is **not** a dump of the complete thesis run archive.

## `topology/fattree_k4/`

Deterministic metadata generated for the validated Fat-Tree `k=4` instance:

- manifest;
- nodes;
- links;
- host mapping;
- port mapping.

The Phase 0B reconstruction regenerated these five artefacts from the curated topology code and verified them byte-for-byte against the recovered thesis artefacts.

## `results/leafspine/`

Compact summaries for the controlled Leaf-Spine forwarding comparisons.

## `results/restconf/`

Compact status and traffic summaries for the **primary deterministic RESTCONF Fat-Tree campaign**.

## `results/ecmp_limited/`

Compact evidence for the bounded OpenFlow `select`-group ECMP demonstrator.

## `results/exploratory/`

Selected processed evidence from the broader ECMP attempt. These files are retained only as **exploratory/non-conclusive engineering evidence** and do not support a claim of complete Fat-Tree ECMP.

## Integrity

`SHA256SUMS` records hashes for every committed evidence file under `data/`, excluding the checksum file itself. Historical CSV line endings are intentionally preserved; `.gitattributes` disables Git end-of-line conversion for these CSV files.

Raw run directories, host-specific logs, virtual environments and thesis-production artefacts remain private and are intentionally excluded.
