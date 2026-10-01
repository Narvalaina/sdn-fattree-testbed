# Native Linux release-validation plan

This checklist is the gate for **Phase 0C-B**. It is intentionally executed on the native Ubuntu/Linux partition used for the thesis, not on Windows or WSL2.

Do **not** modify the recovered historical thesis directory. Validate only a fresh copy of the curated release candidate.

## 1. Integrity and portable/native preflight

From the release-candidate root:

```bash
chmod +x tools/native_linux_release_check.sh
./tools/native_linux_release_check.sh
```

The script performs public-safety audit, compile, CLI, portable tests (when pytest is installed), deterministic Fat-Tree metadata regression, environment capture and `tfgctl doctor`. Machine-specific doctor output is copied outside the repository and removed from the candidate tree.

A successful run must finish with:

```text
PRECHECK=PASS
FAIL=0
```

## 2. Reference system stack

Record and compare, without upgrading the historical environment merely for this release check:

```bash
python3 --version
ovs-vsctl --version
ovs-ofctl --version
mn --version
java -version
iperf3 --version
```

The thesis reference stack is documented in [`environment.md`](environment.md).

## 3. OpenDaylight readiness

Start the existing thesis-era OpenDaylight/Karaf installation in a separate terminal. Do not reinstall or upgrade it during the validation session unless a failure demonstrates that this is necessary.

Export the local laboratory configuration without committing credentials:

```bash
export ODL_HOST=127.0.0.1
export ODL_REST_PORT=8181
export ODL_OF_PORT=6653
export ODL_USER=admin
export ODL_PASS='YOUR_LOCAL_LAB_PASSWORD'
```

Confirm the listening ports:

```bash
ss -lnt | grep -E ':(8181|6653)\b'
```

## 4. Basic topology gate

Use two terminals from the repository root. First obtain a sudo ticket in both terminals with `sudo -v`.

Terminal A:

```bash
sudo -E python3 tfgctl start --topo basic
```

Keep the Mininet CLI open. Terminal B:

```bash
python3 tfgctl push-flows --topo basic --odl-host "$ODL_HOST" --odl-rest-port "$ODL_REST_PORT" --odl-user "$ODL_USER" --odl-pass "$ODL_PASS"
python3 tfgctl validate --topo basic --strict --odl-host "$ODL_HOST" --odl-rest-port "$ODL_REST_PORT" --odl-of-port "$ODL_OF_PORT" --odl-user "$ODL_USER" --odl-pass "$ODL_PASS"
```

Exit the Mininet CLI and run `python3 tfgctl clean`.

## 5. Intermediate topology gate

Repeat the previous pattern with `--topo intermediate`. The strict validation must pass before proceeding.

## 6. Leaf-Spine representative gate

Terminal A:

```bash
sudo -E python3 tfgctl start --topo leafspine
```

Terminal B:

```bash
python3 tfgctl push-flows --topo leafspine --profile l3_static --odl-host "$ODL_HOST" --odl-rest-port "$ODL_REST_PORT" --odl-user "$ODL_USER" --odl-pass "$ODL_PASS"
python3 tfgctl validate --topo leafspine --profile l3_static --strict --odl-host "$ODL_HOST" --odl-rest-port "$ODL_REST_PORT" --odl-of-port "$ODL_OF_PORT" --odl-user "$ODL_USER" --odl-pass "$ODL_PASS"
```

Exit the Mininet CLI and clean the topology. This gate checks the public reconstruction; it is not a rerun of every historical performance repetition.

## 7. Fat-Tree k=4 functional gate

With OpenDaylight running and `ODL_PASS` exported:

```bash
sudo -E python3 tfg_sdn/experiments/ch06_ft68_functional_forwarding.py
```

Expected release-level evidence:

- Fat-Tree k=4 instantiates successfully;
- 20 OVS/OpenFlow switches are visible to the controller;
- representative same-edge, same-pod and inter-pod pings pass under the script's deterministic forwarding;
- OpenFlow 1.3 state can be dumped;
- RESTCONF inventory/topology capture succeeds.

The script writes machine-specific evidence below `docs/ch06_fat_tree/`; retain that output **privately for the release audit**, then remove it from the public candidate before the final publication audit.

## 8. Deterministic RESTCONF smoke gate

After the Fat-Tree topology itself is proven, validate the public RESTCONF installer in a controlled smoke run. Prefer the dry-run first:

```bash
python3 tfg_sdn/experiments/ch08_install_restconf_forwarding.py --profile p2p --dry-run --odl-pass "$ODL_PASS"
```

Only after the dry-run is coherent should the live installation be exercised against a running k=4 Fat-Tree. The live sequence must preserve the thesis methodology: deterministic RESTCONF forwarding is the primary evidence; static ARP is intentional; no ARP `NORMAL` shortcut should be introduced in the redundant topology.

## 9. Limited ECMP smoke gate

If all previous gates pass, perform only a bounded smoke validation of the `select`-group path. Do not turn this release check into a new full-ECMP campaign. The public claim remains limited ECMP / bounded `select`-group evidence.

## 10. Evidence and decision

For each manual gate retain privately:

- date/time;
- exact command;
- concise terminal output/log;
- controller/OVS evidence where relevant;
- PASS/FAIL;
- any code change needed.

Before release, remove machine-specific runtime output and Python caches, then run again:

```bash
find . -type d -name '__pycache__' -prune -exec rm -rf {} +
rm -rf .pytest_cache runs docs/ch06_fat_tree
python3 tools/release_audit.py
```

The candidate is eligible for Git initialization only after the required native-Linux gates are PASS and the public tree is clean.
