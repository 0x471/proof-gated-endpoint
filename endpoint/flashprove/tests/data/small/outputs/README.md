Standard output of real `cp_verify` calls on the stored small proof, captured unchanged on 19 Nov 2026
(Ubuntu 24.04 aarch64 VM, glibc 2.39). The verifier was built from FlashProve 9e2bdd8 with
`verifier/build.sh` (binary sha256 25c4a614308b0db37a3a606c5e17d1ca62d6b291947284c3d5b90a96fbc2a864; it
prints `build=93afdaf5...`). Every call was a whole call with `-id` and `-q`; standard error was empty in
all of them.

| File | Call | Exit code |
|---|---|---|
| `accept.out` | the stored files unchanged | 0 |
| `no_prompt_changed.out` | request word 0 (a prompt token) plus one | 1 |
| `no_y_changed.out` | y = 200 instead of 199 | 1 |
| `no_range.out` | e = 63 with y still 199 | 1 |
| `no_other_id.out` | `-id` with its last digit changed | 1 |
| `no_proof_head.out` | first proof byte flipped | 1 |
| `no_proof_flipped.out` | middle proof byte flipped | 1 |
| `no_proof_empty.out` | empty proof file | 1 |
| `no_key_changed.out` | one byte of `ivk/key.bin` flipped | 1 |
| `no_id_exit14.out` | no `-id` | 14 |

The progress lines carry stage timings, so `accept.out` differs from run to run above its closing lines.
