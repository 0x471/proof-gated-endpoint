# FlashProve binding (experimental)

This is the contract for the experimental FlashProve backend on the `flashprove` branch. The EZKL
binding in `BINDING_SPEC.md` is unchanged and remains the binding of the hardened image. Everything
here refers to FlashProve at commit 9e2bdd8ff5bf9c62f18263d7dac189b0400b260c: the request format, and
the closing lines in `verifier/docs/output.md`. A later FlashProve revision needs this page re-checked.

Status: the statement builder and the answer reader exist in `endpoint/flashprove` with tests. The
wire mode, pins file, job files, confined launcher and service glue are planned.

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

## Running the verifier

- One whole call (key, leaf and request stages) on the pinned files, the endpoint-built request and
  next-token files and the server's proof, with `-id` set to the pinned ID. No `-policy`, no
  `-next-token`, no `-show-stages`. Never assemble a verdict from part-calls or earlier calls.
- The three per-check files go into a private directory on tmpfs, created exclusively, owner-only, and
  removed after the check.
- The verifier runs in its own process group with an empty environment, under a deadline equal to the
  time left before the request's release, with standard output capped. At the deadline the whole
  group is killed and every child reaped.
- Confinement for the experiment: limits on CPU, open files and file growth; Landlock read access to
  the pinned bundle and the job directory, execute access to the pinned verifier only; seccomp
  refusing network sockets and ptrace while allowing the forks the verifier needs. The hardened
  image's parent profile allows only the endpoint's own binary to run, so these experiments use a
  separate, recorded stock-Linux profile, and the protections it lacks are listed with its results.
- Memory limits are per process, and the verifier forks one process per stage, so the whole tree's
  memory is measured and capped as a whole where the system allows.

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
