# Repository map

Start with the directory table. Expand a section to find a specific implementation or experiment.

Two proof systems share one endpoint. The EZKL track ran on stock Pi OS and on the stripped image; its device scripts and image are frozen so its results stay reproducible. The FlashProve track runs on stock Pi OS and is where the side-channel experiments continue.

| Directory | Purpose | Status |
|---|---|---|
| `endpoint/` | Rust software running on the device: shared event loop, release rules and cap, with an EZKL and a FlashProve backend | Active |
| `pge/` | Python reference endpoint for EZKL, compared reply for reply; `tokens.py` is FlashProve's message format for the laptop side | Reference |
| `prover/`, `observer/` | Simulated server, outside client, controls and leakage analysis | Active |
| `device/` | Pi experiments: the `flashprove_*` scripts, `pi_temp_log.sh` and `pi_cpu_sample.sh` are current; the others ran the EZKL experiments | Active; EZKL scripts frozen |
| `image/` | The stripped Buildroot system image (EZKL) | Frozen |
| `tests/` | Automated checks and pinned models | Active |
| `docs/`, `book/` | Specifications, registered protocols, evidence and this explanation | Active |
| `.github/workflows/` | CI | Active |

Side experiments that are no longer used (a Rust EZKL checker and an S-two checker) were removed; they stay in the git history.

<details><summary>Device software: Rust</summary>

| Path | Purpose |
|---|---|
| `endpoint/wire` | Strict parse/re-encode comparison for EZKL and token messages; no unsafe code. Two bounded Kani proofs cover frame-length checking and any 12-byte input for the smallest shape. Larger inputs did not finish. |
| `endpoint/core` | Deterministic release, halt and cap state machine; caller supplies time and events. |
| `endpoint/verify` | EZKL-compatible input encoding; pinned bundle re-hashed per check. |
| `endpoint/flashprove` | The statement the endpoint builds for FlashProve's `cp_verify`, and a strict reading of its answer. |
| `endpoint/update` | Signed key lists, manifests and forward-only state. `pge-sign` signs off-device. |
| `endpoint/endpoint` | One poll loop owns sockets, timers and logs; fresh limited, syscall-filtered child per proof. `fp.rs` runs FlashProve's verifier on pinned private copies. |
| `endpoint/fuzz` | cargo-fuzz targets for messages, checker input, update files and forward-only state. `seeds.py` supplies seeds. |
| `endpoint/sources.toml`, `endpoint/prepare.sh` | Commit-pinned upstream sources, checked out before each build. |

</details>

<details><summary>Python reference</summary>

| Path in `pge/` | Purpose |
|---|---|
| `service.py` | One model, at most one reply per question; arrival default, immediate v0 or fixed release; proven output/fixed errors and fixed-word logs. |
| `protocol.py` | Strict fixed-size binary messages for the model. |
| `tokens.py` | FlashProve token messages, as `endpoint/wire`, for the server, observer and relay. |
| `binding.py` | Encode inputs as EZKL does, validate output values, decode proven outputs. |
| `bundle.py` | Load model files only when hashes match. |
| `domain.py` | Refuse questions outside the model's input range. |
| `verdict.py` | ACCEPT, REJECT or ERROR classification. |
| `cli.py` | Limited proof checks through EZKL's official CLI. |
| `proc.py` | Program deadline, output cap and resource limits. |
| `worker.py` | One proof check in a separate process with a deadline. |

</details>

<details><summary>Specifications and records</summary>

| Path in `docs/` | Purpose |
|---|---|
| `THREAT_MODEL.md` | Assets, attackers, boundaries, assumptions, attack surface, channel status and MITRE ATLAS mapping. |
| `ATTACKS.md` | Every catalogued scenario, result and evidence. |
| `SERVICE_SPEC.md` | Exact service behaviour. |
| `BINDING_SPEC.md` | EZKL request binding, pitfalls and test vectors. |
| `FLASHPROVE_BINDING.md` | FlashProve statement binding, messages, confinement and experiment modes. |
| `UPDATE_SPEC.md` | Signers, formats, rollback and replay rules. |
| `ATTACK_SURFACE.md` | Access by version, including build machines. |
| `EXPERIMENT_PROTOCOL.md` | Protocol 1: spin repeat, written before data. |
| `EXPERIMENT_PROTOCOL_2.md` | Pre-registered protocols 2-5: timing cause, cap/halt, load, clients and packet captures. |
| `EXPERIMENT_PROTOCOL_3.md`, `_4.md`, `_5.md` | Protocols 6 (several clients), 7 (release loop on its own CPU) and 8 (the frozen image). |
| `EXPERIMENT_PROTOCOL_6.md` | Protocols 9-11 with FlashProve on the Pi: timing, order and order to failure, load near a release. |
| `EXPERIMENT_PROTOCOL_7.md` | Protocol 12: the load test again with an idle laptop, with and without a release CPU. |
| `results/` | Complete registered-experiment analysis. |

</details>

<details><summary>Tests, server and observer</summary>

| Path | Purpose |
|---|---|
| `tests/` | Unit, EZKL encoding, end-to-end, parser-fuzz, service and leakage tests. `test_docs.py` checks copied facts for drift. |
| `tests/fixtures/` | Pinned `tiny`: 4 inputs, 3 outputs, range in `domain.json`; `multi`: two inputs and two outputs. |
| `prover/server.py` | Honest or misbehaving server, including timing, order, failure, load and straddle encoders; `Replay` serves FlashProve's stored proof. |
| `prover/e2e_tiny.py`, `prover/build_multi.py`, `prover/checks.py` | Rebuild the tiny and multi EZKL models, prove, and check tamper rejection. |
| `observer/client.py` | Sequential, batched, multi-connection or paced questions; reply/time records and decoding. |
| `observer/relay.py` | Positive control for ordering: passes answers through unchecked, in the server's order. Never an endpoint. |
| `observer/explore_attacks.py` | Exploratory FlashProve attacks on one Linux machine, each with its control. Not device evidence. |
| `observer/join_blocks.py`, `observer/protocol10.py` | Join interleaved blocks for `jitter_test.py`; protocol 10's registered analysis. |
| `observer/trace_lateness.py` | Release lateness by bit from the endpoint's private trace: protocols 9 and 11's secondary analysis. |
| `observer/figures.py`, `book/src/figures/` | The book's data figures, drawn as plain SVG from the saved protocol 9-11 runs. |
| `observer/protocol12.py` | Protocol 12's registered analysis: both decoders, the pinned-minus-unpinned lateness interval, the admission path. |
| `observer/pilot_summary.py` | Summarises a pilot: check and release times from the trace, client-side decodes, simulated sensitivity. |
| `observer/by_admission.py` | Order multi-client traces by admission. |
| `observer/pcap_trace.py` | Extract outside packet details and reply times. |
| `observer/cap_model.py`, `observer/cap_bound.py`, `observer/cap_check.py` | Cap reference, failure-pattern bound and reply-by-reply comparison. |
| `observer/cap_curves.py`, `observer/budget_bound.py` | Models of what a cap setting or a failure budget costs and allows. |
| `observer/sleep_vs_fixed.py` | Compare random-sleep and fixed-release models. |
| `observer/analyze_matrix.py` | Summarise release-rule experiments. |
| `observer/jitter_test.py` | Test whether remaining timing follows planted bits. |
| `observer/shift_bound.py` | How much a mean-shift timing code could carry, given a run. |
| `observer/estimate.py` | Mutual information in bits per answer, null and interval; synthetic `--selftest`. |
| `observer/order_experiment.py` | Local ordering experiment. |

</details>

<details><summary>Pi scripts, images and supporting files</summary>

| Path | Purpose |
|---|---|
| `device/flashprove_contract.sh` | What `cp_verify` does with the stored proof and changed copies on this machine. |
| `device/flashprove_scenario.sh` | One FlashProve scenario: endpoint on the Pi, replay server and observer on the laptop; manifest with hashes, CPU settings and temperature. |
| `device/flashprove_gate.sh` | The FlashProve integration gate on the Pi. Not a measurement. |
| `device/flashprove_protocols.sh` | Runs protocols 9-12 in their registered order, with bits from registered seeds. |
| `device/flashprove_pilot.sh` | The labelled pilot on the Pi that sets deadline, slot, pacing and sample size. Not a result. |
| `device/flashprove_small.pins` | Pins for FlashProve's stored small proof. |
| `device/pi_temp_log.sh` | Logs the Pi's temperature at a fixed interval during a scenario. |
| `device/pi_cpu_sample.sh` | With `CPU_SAMPLE=1`, logs once a minute which CPUs `cp_verify` and the endpoint's threads may use. |
| `device/verify_on_pi.sh`, `device/check_verifier.sh` | Copy pinned model; time real-tool acceptance and rejection checks. |
| `device/pi_scenario.sh` | Stock-system EZKL experiment: Python or Rust on Pi; server and observer on laptop. |
| `device/image_scenario.sh` | Run against the already-started, login-free image endpoint; record manifest. |
| `device/tcp_scan.py` | Scan every TCP port: open, refused or dropped. |
| `device/halfopen_check.py` | Does the endpoint notice a silently dead server connection and redial? |
| `device/rust_verify_cost.py`, `device/endpoint_latency.py` | Rust checker cost and endpoint overhead. |
| `device/pi_matrix.sh` | Every release rule against each signalling method. |
| `device/verify_cost.py` | Verification cost and memory-cap reliability. |
| `device/measure.sh` | Packages, services, processes, open ports and file hashes. |
| `image/` | Buildroot `build.sh`, fragments, firewall/start-up overlay; signed `make_update.sh`; file inventory via `manifest.sh`. |
| `.github/workflows/` | Python, native/static-aarch64 Rust, book and on-demand independent image build. |
| `book/` | mdBook, including reference files from `docs/`. |
| `book/diagrams/`, `book/render-diagrams.sh` | Mermaid sources rendered to committed SVGs in `book/src/diagrams/`; mdBook has no Mermaid support. |

</details>
