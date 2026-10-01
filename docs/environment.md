# Validated reference environment

The thesis experiments were executed on **native Ubuntu/Linux**, not WSL2. The following environment was captured by the project's reproducibility snapshot during the final experimental period.

| Component | Validated value |
|---|---|
| Operating system | Ubuntu 24.04.4 LTS |
| Kernel | 6.17.0-22-generic |
| Python | 3.12.3 |
| Open vSwitch | 3.3.4 |
| Mininet | 2.3.0 |
| Java | OpenJDK 21.0.10 |
| OpenDaylight / Karaf | Titanium, Karaf 0.22.0 |
| RESTCONF endpoint | `127.0.0.1:8181` |
| OpenFlow controller endpoint | `127.0.0.1:6653` |
| OpenFlow protocol | 1.3 |

The reference snapshot also recorded Matplotlib 3.6.3 and NumPy 1.26.4. These versions describe the validated thesis environment; they are not intended as universal hard pins for every future installation.

## System tools used by the experiment scripts

Depending on the experiment, the scripts invoke or expect:

- `mn` / Mininet;
- `ovs-vsctl`;
- `ovs-ofctl`;
- `iperf3`;
- `ping`;
- `ss`;
- `curl`;
- `sudo`;
- standard Linux networking/process utilities.

OpenDaylight is an external controller and is not installed as a Python dependency of this repository.

## Reproduction policy

The portable checks in this repository can run without Mininet or OpenDaylight. Controller-connected experiments require a compatible native Linux environment and are revalidated separately before public release.
