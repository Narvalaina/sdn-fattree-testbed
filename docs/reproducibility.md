# Reproducibility

## Target environment

The original project was executed on **native Linux/Ubuntu**, not WSL2. The validated laboratory stack used:

- Mininet
- Open vSwitch with OpenFlow 1.3
- OpenDaylight / Karaf
- RESTCONF on port 8181
- OpenFlow controller connection on port 6653
- Python 3
- `iperf3`, `ping` and standard Linux networking tools

## Local configuration

Do not commit credentials. Export the OpenDaylight settings locally, for example:

```bash
export ODL_HOST=127.0.0.1
export ODL_REST_PORT=8181
export ODL_OF_PORT=6653
export ODL_USER=admin
export ODL_PASS='your-local-lab-password'
```

The user name `admin` is shown only as a common laboratory default; no password is embedded in the public code.

## Python package

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[plots,test]'
```

Mininet and Open vSwitch are system-level dependencies and should be installed using the packages/documentation appropriate to the Linux distribution.

## Initial checks

```bash
tfgctl doctor
python -m tfg_sdn.mininet.topos.fattree --k 4 --export --outdir data/topology/fattree_k4_generated
```

Starting Mininet and executing controller-connected experiments requires the relevant Linux privileges and a running OpenDaylight instance.

## Validated thesis-era stack

The exact captured versions used during the final experimental period are recorded in [`environment.md`](environment.md). This distinction matters: the values there describe the **validated reference environment**, whereas the Python metadata in `pyproject.toml` intentionally allows a broader Python range for portable, non-Mininet utilities.

## Pre-release native-Linux regression

Before public release, follow [`linux_validation.md`](linux_validation.md). The release gate is deliberately separated from Windows-side curation because Mininet, Open vSwitch and OpenDaylight execution must be checked on native Linux.
