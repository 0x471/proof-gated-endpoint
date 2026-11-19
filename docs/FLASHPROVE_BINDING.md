# FlashProve binding (experimental)

This is the contract for the experimental FlashProve backend on the `flashprove` branch. The EZKL
binding in `BINDING_SPEC.md` is unchanged and remains the binding of the hardened image. Everything
here refers to FlashProve at commit 9e2bdd8ff5bf9c62f18263d7dac189b0400b260c: the request format, and
the closing lines in `verifier/docs/output.md`. A later FlashProve revision needs this page re-checked.

Status: built and tested on a Linux VM, not yet run on the Pi. `endpoint/flashprove` builds the
statement, reads the answer and parses the pins file; `endpoint/endpoint/src/fp.rs` holds the bundle copy,
the job files and the launcher; the service runs it with `--backend flashprove`. The tests are listed
under [Checks](#checks).

## The claim

For this pinned integer-model configuration and this endpoint-built request, the whole fixed-length
continuation satisfies the exact-match policy, conditional on the proof system's and the verifier's
stated assumptions.

Not claimed: a complete chat or a correct stopping point; fresh computation (a stored proof for the
same statement verifies again); anything about the original floating-point model or its quality; that
the weights are hidden; that the approved model's own correct answers carry no sensitive content. The
C++ verifier is not proved; the Lean soundness theorem is about the Lean verifier at full size and
exact match only.

## What the endpoint pins

None of these comes from the server or from the verifier's output.

- The commitment ID (64 lower-case hex digits), obtained before any prompt is sent from the party
  that vouches for the model.
- The configuration directory (`policy.txt`, `KL.bin`, `layout.txt`) and the public key directory
  (`key.bin` and the permitted leaf files), each with its sha256.
- The `cp_verify` binary's sha256, checked before every start, and its source digest (`build=`). The
  printed digest is a consistency check, not attestation.
- The window T, the vocabulary size V, the prompt length NP, the continuation length NC and the filler
  id. FlashProve's `policy.txt` uses L for the number of layers, so the continuation length is NC here.
- `sh`, the amount of proof of work accepted (decided before any run), the commitment's
  `commitment-assumed-rounds`, and the largest proof the wire accepts.

All of these are in one pins file, `device/flashprove_small.pins` for the stored proof: one `key value`
line per setting and one `file <path> <sha256>` line per bundle file, each exactly once. Its `work 32`
was decided on 19 Nov before any run: the stored proof reaches 128 bits in its weakest round only with
32 bits of proof of work counted, so a setup accepting less would refuse it.

## Building the statement

From the admitted prompt ids and the server's continuation ids:

1. Exactly NP prompt ids and NC continuation ids; every id below V; NP >= 1, NC >= 1.
2. NP + NC <= T + 1 and NP <= T - 1, so at least one checked token lies inside the request and the
   claim is exact-match, never next-token only.
3. r = NP - 1 and e = NP + NC - 1. Position t checks token t + 1, so [r, e) covers every continuation
   token.
4. If NP + NC <= T: fill to T tokens with the filler id; y = 0 and y is not checked. If NP + NC = T + 1:
   the last continuation token is y, and y must be checked.
5. The request file is T ids followed by r and e, each an unsigned 32-bit little-endian word; the
   next-token file is y as one such word.

The endpoint never uses a request file, ID or configuration supplied by the server.

## Messages

The token messages have their own kind bytes, so they never parse as EZKL messages
(`endpoint/wire/src/tokens.rs`, `pge/tokens.py`). Each is framed like every other message, with a 4-byte
big-endian length, and starts with its kind and a big-endian 64-bit id below 2^53; token ids are
big-endian 32-bit words.

| Kind | Message | After the id |
|---|---|---|
| 5 | request, client to endpoint | NP prompt ids |
| 6 | forward, endpoint to server | the same NP ids |
| 7 | answer, server to endpoint | NC reply ids, then the proof (1 byte up to the pinned limit) |
| 8 | reply, endpoint to client | status 0 and NC ids, or status 1 (bad request) or 2 (unavailable) and NC zero words |

Every message has one canonical encoding; anything else is refused.

## Running the verifier

- At start-up the endpoint hashes every pinned bundle file and the verifier, refuses to start on any
  mismatch, and copies them into a new private directory `<jobs>/pge-flashprove-<pid>` (`--jobs`,
  default `/dev/shm`): directories 0700, files 0400, the verifier 0500. Before every check it hashes the
  copies again; a changed copy makes the check an error.
- One whole call (key, leaf and request stages) on the copies, the endpoint-built request and
  next-token files and the server's proof: `cp_verify CONFIG IVK request next proof -id <pinned ID> -q`.
  No `-policy`, no `-next-token`, no `-show-stages`, no `-j` (one thread for the request stage). Never
  assemble a verdict from part-calls or earlier calls.
- The three per-check files go into `job-<seq>` in the private directory, created exclusively,
  owner-only and read-only, and removed after the check whatever its outcome.
- The endpoint starts its own binary in `--flashprove-child` mode, in a new process group with an empty
  environment. That process asks to be killed if the checking thread dies, lowers its limits (CPU
  seconds = the whole seconds left before the release, at least 1; 256 MiB of address space; no file
  growth; 64 open files; no core dumps), confines itself, then becomes `cp_verify`, whose stage
  processes inherit all of it. At the deadline the whole group is killed. Standard output and error
  together are capped at 64 KiB; any standard error makes the check an error.
- Landlock: read access beneath the private directory, the system library directories (`/lib`,
  `/lib64`, `/usr/lib`, `/usr/lib64`, whichever exist) and `/etc/ld.so.cache`; execute access to the
  verifier copy and its program interpreter (read from the ELF file at start-up) only. From Landlock
  ABI 4 no TCP bind or connect; from ABI 6 no signals or abstract sockets outside the verifier's own
  processes. A kernel without Landlock makes every check an error.
- Seccomp, allowing everything else, refuses: sockets, socket pairs, connect, bind, listen, accept;
  ptrace and reading or writing other processes' memory; `kill` and pidfd signals; new sessions and
  process groups; namespaces (`unshare`, `setns`, namespace flags on `clone`); mounts and `chroot`;
  BPF, perf events, userfaultfd, io_uring, keys, module loading, `kexec_load`; `memfd_create`,
  `execveat` and `open_by_handle_at`. None of these is in a traced whole call.
- With `--release-cpu N` the checking thread, and so the verifier and its stages, runs on the other
  CPUs.

## What this setup lacks

The FlashProve experiments run on a stock Linux card, not on the frozen image, and lack some of its
protections:

- The endpoint process itself is not confined: only the static build confines itself, and that build
  can execute nothing but its own binary.
- The verifier runs as the endpoint's user. `kill` is refused, but before Landlock ABI 6 it could still
  signal that user's processes with `tgkill`.
- Memory is limited per process; the endpoint does not limit the whole tree. Run it in a cgroup with a
  memory limit for experiments.
- Between the hash check and the verifier reading the copies, another process of the same user could
  change them.
- If the endpoint itself dies during a check, the job directory stays behind, and the verifier's stage
  processes are bounded only by their CPU limit (the main verifier process is killed).
- Every request is the one stored statement, so every honest check does the same work.

## Checks

- `cargo test -p pge-flashprove`: the statement builder (the stored request byte for byte), the answer
  reader on outputs captured from real calls (`endpoint/flashprove/tests/data/small/outputs`) and on
  every changed field, and the pins parser.
- `cargo test -p pge-endpoint`: the ELF interpreter reader and, on Linux, the bundle copy and its
  refusals.
- `pytest tests/test_tokens.py`: the Python token format against byte vectors shared with the Rust tests.
- On Linux, with `PGE_RUST_BIN` (the dynamic build), `PGE_CP_VERIFY` and `PGE_FP_FIXTURE` set,
  `pytest tests/test_endpoint_rust_flashprove.py`: the stored proof passes at the deadline; a changed
  prompt, first or last reply token or proof bit is refused; malformed, stale and duplicate answers
  release nothing extra; a check past its deadline is killed with its children and leaves no job
  directory; a dead endpoint takes its verifier with it; the verifier runs with the limits above,
  no-new-privileges, a seccomp filter and off the release CPU; logs carry no proof material; the
  failure cap matches `observer/cap_model.py`. With only a C compiler instead of `cp_verify`, a probe
  checks the confinement itself.
- `device/flashprove_contract.sh`: the verifier alone on the stored proof and changed copies of it.
- `device/flashprove_gate.sh`: the same gate on the Pi, through `device/flashprove_scenario.sh`.

## Reading the answer

Accept only if all of these hold:

1. The process exited with code 0. Exit code 1 with a last line beginning `NO: ` is a rejection; any
   other ending (codes 2, 3, 4, 5, 14, 15, a signal, a timeout) is an error.
2. No line begins `TEST BUILD:`.
3. The last line is exactly `YES`.
4. Exactly one line begins `result-v1`, and it is the second-to-last line.
5. The fourth-to-last line begins `commitment: ` and ends with the clause that the table has a record
   and that the binding of the weights rests on the theorem on codes, taken as given.
6. The `result-v1` line has exactly the documented fields in the documented order, and: `status=0`,
   `claim=exact-match`, the pinned `id`, `bits=128`, `work` within the decided amount (and `work=0`
   exactly when `work-rounds=0`), the pinned `commitment-assumed-rounds`, `record=1`, `r`, `e`, `T`,
   `tokens` and `y` equal to the endpoint's statement, `policy=exact-match`, `C` and `theta` equal to
   2 to the power `sh`, `cap=0`, the pinned `sh`, `sT` equal to `sh` minus 4, `F=24`, `above-clip=0`,
   `stages=key,leaf,request`, `dw=8` and the pinned `build`. Whole numbers with a leading zero are
   refused.
7. The `proof of work:` line agrees with the `result-v1` line: `none counted` exactly when `work=0`,
   otherwise the same bits, rounds and both least-round figures.

Never search the output for a word. The words `NOT CHECKED` appear in every accepted call, in the line
on weight hiding.

| Verifier ending | Reading | Outward reply | Failure cap |
|---|---|---|---|
| every rule above holds | accept | the continuation ids at release time | not counted |
| exit code 1, `NO: ...` | reject | `unavailable` at release time | counted as reject |
| any other ending or any rule broken | error | `unavailable` | counted as error |
| no verdict by the release time | error | `unavailable` | counted |

There is no retry. An honest proof that is refused, or a call that ends without a verdict, is a
counted failure and part of the honest baseline.

## What leaves the endpoint

Only the fixed-size reply: the continuation ids or `unavailable`, at the fixed release time. The
proof, any hash of it, the verifier's output and exit code, and the reader's internal cause never
leave and are never logged. A compromised prover controls the randomness in a valid proof, so
forwarding proofs would hand it a channel.

## Out of scope for this binding

Text, tokenizer, chat template and end tokens (a fixed number of token ids instead); sampling (FlashProve
checks greedy choices only); the clipped-gap policy; weight hiding; the floating-point model; live
proving. Each needs its own contract before it is used.
