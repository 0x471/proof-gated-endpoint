"""The FlashProve backend end to end: the Rust endpoint, the real cp_verify and FlashProve's stored proof
(docs/FLASHPROVE_BINDING.md).

Needs Linux, PGE_RUST_BIN (the dynamic build), PGE_CP_VERIFY (cp_verify built from FlashProve 9e2bdd8,
matching device/flashprove_small.pins) and PGE_FP_FIXTURE (its examples/accepted-proof); skips without them.
PGE_FP_DEADLINE sets the release deadline in seconds (default 3; one check takes about 0.7 s on a laptop VM
and 5 s on the Pi 4). PGE_FP_CONFINE picks the verifier's confinement (default landlock; seccomp-only is for
kernels without Landlock, aarch64 only). The confinement probe needs only Linux, PGE_RUST_BIN and a C compiler.
"""
import asyncio
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import time
from pathlib import Path

import pytest

from conftest import ROOT
from test_endpoint_rust import free_port, rust_bin  # noqa: F401
from test_endpoint_rust_robust import log_records
from prover.server import Replay, Server

sys.path.insert(0, str(ROOT / "observer"))
from cap_model import START, step  # noqa: E402
from client import decode_failure_position, trace, trace_batches  # noqa: E402

PINS = ROOT / "device" / "flashprove_small.pins"
DEADLINE = float(os.environ.get("PGE_FP_DEADLINE", "3"))
CONFINE = os.environ.get("PGE_FP_CONFINE", "landlock")
LOG_KEYS = {"ms", "result", "seq", "x_sha256", "reason", "failures", "confine", "trace"}
TRACE_KEYS = {"cid", "admit", "answer", "check_start", "check_end", "target", "sent"}

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="the FlashProve backend needs Linux")


@pytest.fixture(scope="module")
def flashprove():
    verifier, fixture = os.environ.get("PGE_CP_VERIFY"), os.environ.get("PGE_FP_FIXTURE")
    if not verifier or not fixture:
        pytest.skip("set PGE_CP_VERIFY and PGE_FP_FIXTURE")
    verifier, fixture = Path(verifier), Path(fixture)
    pinned = next(l.split()[1] for l in PINS.read_text().splitlines() if l.startswith("verifier "))
    assert hashlib.sha256(verifier.read_bytes()).hexdigest() == pinned, "PGE_CP_VERIFY isn't the pinned build"
    assert (fixture / "example.proof").is_file(), "PGE_FP_FIXTURE has no example.proof"
    return verifier, fixture


class Patterned(Replay):
    """The stored answer, with the reply's first token changed on the calls marked in `fail`."""

    def __init__(self, fixture, fail):
        super().__init__(fixture, PINS)
        self.fail, self.calls = list(fail), 0

    def prove(self, x):
        self.calls += 1
        reply, proof = super().prove(x)
        return (self.tamper(reply) if self.fail[self.calls - 1] else reply), proof


def endpoint_args(binary, verifier, fixture, port, sport, jobs, log, deadline, extra):
    return [str(binary), "--listen", f"127.0.0.1:{port}", "--server", f"127.0.0.1:{sport}",
            "--bundle", str(fixture), "--pins", str(PINS), "--backend", "flashprove", "--verifier", str(verifier),
            "--jobs", str(jobs), "--confine", CONFINE, "--release", "fixed", "--deadline", str(deadline),
            "--spin-ms", "5", "--log", str(log), *extra]


async def run_endpoint(binary, flashprove, work, prover=None, mode="honest", n=2, prompts=None,
                       deadline=DEADLINE, extra=(), during=None, client_batch=None, **server_args):
    verifier, fixture = flashprove
    work.mkdir()
    jobs = work / "jobs"
    jobs.mkdir()
    replay = prover or Replay(fixture, PINS)
    server = Server(replay, mode=mode, **server_args)
    connected = asyncio.Event()

    async def handle(reader, writer):
        connected.set()
        await server.handle(reader, writer)

    upstream = await asyncio.start_server(handle, "127.0.0.1", 0)
    port, log = free_port(), work / "endpoint.log"
    args = endpoint_args(binary, verifier, fixture, port, upstream.sockets[0].getsockname()[1], jobs, log,
                         deadline, extra)
    proc = await asyncio.create_subprocess_exec(*args, stderr=asyncio.subprocess.PIPE)
    try:
        await asyncio.wait_for(connected.wait(), 10)
        await asyncio.sleep(0.5)
        xs = prompts or [replay.prompt] * n
        if client_batch:
            task = asyncio.create_task(trace_batches("127.0.0.1", port, xs, client_batch, tokens=True))
        else:
            task = asyncio.create_task(trace("127.0.0.1", port, xs, tokens=True))
        if during:
            await during(proc.pid, jobs)
        rows = await asyncio.wait_for(task, 60 + 2 * len(xs) * deadline)
        records = await log_records(log, lambda r: sum("seq" in x for x in r) >= len(xs))
        # A reply can leave at its deadline while its check is still being stopped and cleaned up.
        end = time.monotonic() + deadline + 5
        while any(p.name.startswith("job-") for root in jobs.iterdir() for p in root.iterdir()):
            assert time.monotonic() < end, "a job directory was never removed"
            await asyncio.sleep(0.05)
        assert proc.returncode is None
    finally:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()
        upstream.close()
        await upstream.wait_closed()
        server.pool.shutdown(wait=True)
    return rows, records, jobs, replay


def released(records):
    return [r for r in records if "seq" in r and r["result"] not in ("duplicate_answer",)]


def test_the_stored_proof_passes_and_is_released_at_the_deadline(rust_bin, flashprove, tmp_path):  # noqa: F811
    rows, records, jobs, replay = asyncio.run(run_endpoint(rust_bin, flashprove, tmp_path / "w", n=3))
    assert records[0] == {"result": "start", "confine": CONFINE}
    assert [r["answer"] for r in rows] == [{"id": i, "ok": True, "y": replay.reply} for i in range(3)]
    assert all(r["elapsed_ms"] >= DEADLINE * 1000 for r in rows)
    assert [r["result"] for r in released(records)] == ["accept"] * 3
    # The private trace: one request per client id, events in order, released at admission + deadline.
    traces = [r["trace"] for r in released(records)]
    assert [t["cid"] for t in traces] == [0, 1, 2]
    for t in traces:
        assert set(t) == TRACE_KEYS, t
        assert t["admit"] <= t["answer"] <= t["check_start"] <= t["check_end"] <= t["target"] <= t["sent"], t
        assert t["target"] - t["admit"] == round(DEADLINE * 1e9), t
    root = next(jobs.iterdir())
    assert sorted(p.name for p in root.iterdir()) == ["config", "cp_verify", "ivk"]


def test_a_changed_prompt_is_refused(rust_bin, flashprove, tmp_path):  # noqa: F811
    replay = Replay(flashprove[1], PINS)
    other = [(replay.prompt[0] + 1) % 512] + replay.prompt[1:]
    rows, records, _, _ = asyncio.run(run_endpoint(rust_bin, flashprove, tmp_path / "w", prompts=[other, replay.prompt]))
    assert [r["answer"]["ok"] for r in rows] == [False, True]
    assert rows[0]["answer"]["error"] == "unavailable"
    assert [(r["result"], r.get("reason")) for r in released(records)] == [("reject", "proof"), ("accept", None)]


@pytest.mark.parametrize("mode", ["tamper_output", "tamper_last", "flip_proof"])
def test_a_changed_reply_or_proof_is_refused(rust_bin, flashprove, tmp_path, mode):  # noqa: F811
    rows, records, _, _ = asyncio.run(run_endpoint(rust_bin, flashprove, tmp_path / "w", mode=mode, n=1))
    assert rows[0]["answer"] == {"id": 0, "ok": False, "error": "unavailable"}
    assert [(r["result"], r.get("reason")) for r in released(records)] == [("reject", "proof")]


@pytest.mark.parametrize("mode", ["garbage", "oversize"])
def test_malformed_answers_end_the_server_link(rust_bin, flashprove, tmp_path, mode):  # noqa: F811
    rows, records, _, _ = asyncio.run(run_endpoint(rust_bin, flashprove, tmp_path / "w", mode=mode, n=1))
    assert rows[0]["answer"]["ok"] is False
    assert "server_violation" in [r["result"] for r in records]


def test_stale_and_duplicate_answers_never_add_a_release(rust_bin, flashprove, tmp_path):  # noqa: F811
    rows, records, _, _ = asyncio.run(run_endpoint(rust_bin, flashprove, tmp_path / "w", mode="duplicate", n=2))
    assert [r["answer"]["ok"] for r in rows] == [True, True]
    assert [r["result"] for r in records].count("duplicate_answer") == 2
    rows, records, _, _ = asyncio.run(run_endpoint(rust_bin, flashprove, tmp_path / "s", mode="wrong_seq", n=1))
    assert rows[0]["answer"]["ok"] is False and "stale_answer" in [r["result"] for r in records]


def verifier_processes(root):
    """Processes running a copy of cp_verify from this test's private directory."""
    found = []
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit():
            continue
        try:
            cmd = (proc / "cmdline").read_bytes().split(b"\0")
            status = (proc / "status").read_text()
        except OSError:
            continue
        if cmd and cmd[0].startswith(str(root).encode()) and cmd[0].endswith(b"cp_verify"):
            found.append((int(proc.name), status))
    return found


def test_a_check_past_the_deadline_is_killed_with_its_children(rust_bin, flashprove, tmp_path):  # noqa: F811
    rows, records, jobs, _ = asyncio.run(run_endpoint(rust_bin, flashprove, tmp_path / "w", n=2, deadline=0.3))
    assert [r["answer"]["ok"] for r in rows] == [False, False]
    # The core decides "deadline" at the release time; the killed check's own error comes later.
    assert {r["result"] for r in released(records)} <= {"deadline", "error"}
    time.sleep(0.5)
    assert verifier_processes(jobs) == []
    root = next(jobs.iterdir())
    assert sorted(p.name for p in root.iterdir()) == ["config", "cp_verify", "ivk"]


def test_a_dead_endpoint_takes_its_check_with_it(rust_bin, flashprove, tmp_path):  # noqa: F811
    verifier, fixture = flashprove
    work, jobs = tmp_path / "w", tmp_path / "w" / "jobs"
    jobs.mkdir(parents=True)
    replay = Replay(fixture, PINS)

    async def run():
        server = Server(replay)
        upstream = await asyncio.start_server(server.handle, "127.0.0.1", 0)
        port = free_port()
        args = endpoint_args(rust_bin, verifier, fixture, port, upstream.sockets[0].getsockname()[1], jobs,
                             work / "endpoint.log", 30, ())
        proc = await asyncio.create_subprocess_exec(*args, stderr=asyncio.subprocess.PIPE)
        try:
            await asyncio.sleep(1.0)
            task = asyncio.create_task(trace("127.0.0.1", port, [replay.prompt], tokens=True))
            end = time.monotonic() + 20
            while not verifier_processes(jobs):
                assert time.monotonic() < end, "never saw cp_verify running"
                await asyncio.sleep(0.005)
            proc.kill()
            await proc.wait()
            task.cancel()
            end = time.monotonic() + 10
            while verifier_processes(jobs):
                assert time.monotonic() < end, verifier_processes(jobs)
                await asyncio.sleep(0.05)
        finally:
            if proc.returncode is None:
                proc.kill()
                await proc.wait()
            upstream.close()
            server.pool.shutdown(wait=True)

    asyncio.run(run())


def test_the_verifier_runs_limited_confined_and_off_the_release_cpu(rust_bin, flashprove, tmp_path):  # noqa: F811
    if len(os.sched_getaffinity(0)) < 2:
        pytest.skip("needs two CPUs")
    seen = []

    async def watch(pid, jobs):
        end = time.monotonic() + 3 * DEADLINE
        while time.monotonic() < end and len(seen) < 3:
            for vpid, status in verifier_processes(jobs):
                if vpid not in [s[0] for s in seen]:
                    try:
                        limits = (Path("/proc") / str(vpid) / "limits").read_text()
                    except OSError:
                        continue
                    seen.append((vpid, status, limits))
            await asyncio.sleep(0.01)

    asyncio.run(run_endpoint(rust_bin, flashprove, tmp_path / "w", n=2, extra=("--release-cpu", "0"), during=watch))
    assert seen, "never saw cp_verify running"
    for _, status, limits in seen:
        fields = dict(line.split(":\t", 1) for line in status.splitlines() if ":\t" in line)
        cpus = fields["Cpus_allowed_list"].strip()
        assert "0" not in [c for part in cpus.split(",") for c in part.split("-")], cpus
        assert fields["NoNewPrivs"].strip() == "1"
        assert fields["Seccomp"].strip() == "2"
        assert re.search(r"^Max address space\s+268435456\s+268435456 ", limits, re.M), limits
        assert re.search(r"^Max file size\s+0\s+0 ", limits, re.M), limits
        assert re.search(r"^Max open files\s+64\s+64 ", limits, re.M), limits


def test_logs_and_replies_carry_no_proof_material(rust_bin, flashprove, tmp_path):  # noqa: F811
    proof = (flashprove[1] / "example.proof").read_bytes()
    full = hashlib.sha256(proof).hexdigest()
    digests = {full, full[:16]}
    for i, mode in enumerate(["honest", "flip_proof"]):
        _, records, _, _ = asyncio.run(run_endpoint(rust_bin, flashprove, tmp_path / f"w{i}", mode=mode, n=1))
        for r in records:
            assert set(r) <= LOG_KEYS, r
            assert not any(d in json.dumps(r) for d in digests)


def expected(fail, window, failures, cooldown):
    state, ok, outcomes = START, [], []
    for failed in fail:
        state, outcome = step(state, window, failures, cooldown, failed)
        ok.append(outcome == "pass" and not failed)
        outcomes.append(outcome)
    return ok, outcomes


def test_the_failure_cap_matches_the_reference_model(rust_bin, flashprove, tmp_path):  # noqa: F811
    fail = [False, True, False, False, True, True, False, False, False, True, False, False]
    window, failures, cooldown = 4, 1, 3
    cap = ("--cap-window", str(window), "--cap-failures", str(failures), "--cap-cooldown", str(cooldown))
    rows, records, _, _ = asyncio.run(run_endpoint(rust_bin, flashprove, tmp_path / "w",
                                                   prover=Patterned(flashprove[1], fail), n=len(fail), extra=cap))
    want, outcomes = expected(fail, window, failures, cooldown)
    assert [r["answer"]["ok"] for r in rows] == want
    results = [r["result"] for r in records]
    assert results.count("cap_tripped") == outcomes.count("trip") > 0
    assert results.count("capped") == outcomes.count("capped") > 0


def test_the_failure_code_reads_back_without_a_cap(rust_bin, flashprove, tmp_path):  # noqa: F811
    rows, _, _, _ = asyncio.run(run_endpoint(rust_bin, flashprove, tmp_path / "w", mode="fail_position", n=8,
                                             bits="1011", batch=2))
    assert decode_failure_position(rows, 2) == "1011"


def test_timing_bits_move_the_answer_not_the_release(rust_bin, flashprove, tmp_path):  # noqa: F811
    slot, bits = 0.5, "0101"
    rows, records, _, _ = asyncio.run(run_endpoint(rust_bin, flashprove, tmp_path / "w", mode="timing_bits", n=4,
                                                   bits=bits, slot=slot))
    assert all(r["answer"]["ok"] for r in rows)
    traces = [r["trace"] for r in released(records)]
    delays = [(t["answer"] - t["admit"]) / 1e9 for t in traces]
    for bit, d in zip(bits, delays):
        assert (d >= 0.9 * slot) if bit == "1" else (d < slot / 2), (bits, delays)
    # Fixed release: every reply still leaves at its admission + deadline, whatever the answer time.
    assert all(t["target"] <= t["sent"] for t in traces)


def test_order_bits_cannot_reorder_the_replies(rust_bin, flashprove, tmp_path):  # noqa: F811
    # Batch 3 carries 2 bits; "11" picks a permutation other than the admission order.
    rows, records, _, _ = asyncio.run(run_endpoint(rust_bin, flashprove, tmp_path / "w", mode="order_bits", n=3,
                                                   client_batch=3, bits="11", batch=3, deadline=2 * DEADLINE))
    assert [r["id"] for r in rows] == [0, 1, 2] and all(r["answer"]["ok"] for r in rows)
    traces = sorted((r["trace"] for r in released(records)), key=lambda t: t["cid"])
    answered = [t["cid"] for t in sorted(traces, key=lambda t: t["answer"])]
    assert answered != [0, 1, 2], answered
    # Checks run in answer-arrival order, releases in admission order.
    assert [t["cid"] for t in sorted(traces, key=lambda t: t["check_start"])] == answered
    assert [t["cid"] for t in sorted(traces, key=lambda t: t["sent"])] == [0, 1, 2]


@pytest.mark.parametrize("mode", ["drop", "delay"])
def test_a_withheld_or_late_answer_fails_at_the_deadline(rust_bin, flashprove, tmp_path, mode):  # noqa: F811
    rows, records, _, _ = asyncio.run(run_endpoint(rust_bin, flashprove, tmp_path / "w", mode=mode, n=1,
                                                   delay=DEADLINE + 0.5))
    assert rows[0]["answer"]["ok"] is False and rows[0]["elapsed_ms"] >= DEADLINE * 1000
    (rec,) = released(records)
    assert rec["result"] == "deadline" and rec["trace"]["answer"] is None and rec["trace"]["check_start"] is None


def test_a_replayed_answer_passes_for_the_same_statement(rust_bin, flashprove, tmp_path):  # noqa: F811
    # Every request here is the one stored statement, so the previous answer is valid again: the binding
    # claims the statement, not fresh computation (docs/FLASHPROVE_BINDING.md, "Not claimed").
    rows, records, _, _ = asyncio.run(run_endpoint(rust_bin, flashprove, tmp_path / "w", mode="replay", n=2))
    assert all(r["answer"]["ok"] for r in rows)
    assert [r["result"] for r in released(records)] == ["accept", "accept"]


PROBE = r"""
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <sched.h>
#include <stdio.h>
#include <string.h>
#include <sys/ptrace.h>
#include <sys/resource.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <sys/wait.h>
#include <sys/xattr.h>
#include <unistd.h>
extern char **environ;
static void say(const char *name, int ok) { printf("%s %s\n", name, ok ? "ok" : "FAIL"); }
static int refused(long r) { return r < 0 && (errno == EPERM || errno == EACCES); }
int main(int argc, char **argv) {
    struct rlimit as, fsize, nofile;
    getrlimit(RLIMIT_AS, &as); getrlimit(RLIMIT_FSIZE, &fsize); getrlimit(RLIMIT_NOFILE, &nofile);
    say("limits", as.rlim_cur == 256UL << 20 && fsize.rlim_cur == 0 && nofile.rlim_cur == 64);
    say("empty_env", environ[0] == NULL);
    say("args", argc == 9 && strcmp(argv[6], "-id") == 0 && strcmp(argv[8], "-q") == 0);
    say("reads_job", open(argv[3], O_RDONLY) >= 0);
    say("reads_bundle", open(argv[1], O_RDONLY | O_DIRECTORY) >= 0);
    int fd = open("/etc/hostname", O_RDONLY); say("no_other_files", fd < 0 && errno == EACCES);
    char other[4096];
    say("no_writes", refused(open(argv[3], O_WRONLY)));
    snprintf(other, sizeof other, "%s.new", argv[3]);
    say("no_create", refused(open(other, O_WRONLY | O_CREAT, 0600)));
    say("no_truncate", refused(open(argv[3], O_RDONLY | O_TRUNC)));
    say("no_chmod", refused(fchmodat(AT_FDCWD, argv[3], 0600, 0)));
    snprintf(other, sizeof other, "%s.d", argv[3]);
    say("no_mkdir", refused(mkdirat(AT_FDCWD, other, 0700)));
    say("no_rename", refused(renameat2(AT_FDCWD, argv[3], AT_FDCWD, argv[4], 0)));
    say("no_rename2", refused(syscall(SYS_renameat2, AT_FDCWD, argv[3], AT_FDCWD, argv[4], 0)));
    say("no_chmod2", refused(syscall(452, AT_FDCWD, argv[3], 0600, 0)));
    snprintf(other, sizeof other, "%s.l", argv[3]);
    say("no_link", refused(linkat(AT_FDCWD, argv[3], AT_FDCWD, other, 0)));
    say("no_symlink", refused(symlinkat(argv[3], AT_FDCWD, other)));
    say("no_xattr", refused(setxattr(argv[3], "user.pge", "x", 1, 0)));
    say("no_unlink", refused(unlinkat(AT_FDCWD, argv[3], 0)));
    say("no_inet", socket(AF_INET, SOCK_STREAM, 0) < 0 && errno == EPERM);
    say("no_unix", socket(AF_UNIX, SOCK_STREAM, 0) < 0 && errno == EPERM);
    say("no_kill", kill(getppid(), 0) < 0 && errno == EPERM);
    say("no_setpgid", setpgid(0, 0) < 0 && errno == EPERM);
    say("no_setsid", setsid() < 0 && errno == EPERM);
    say("no_userns", unshare(CLONE_NEWUSER) < 0 && errno == EPERM);
    say("no_ptrace", ptrace(PTRACE_TRACEME, 0, 0, 0) < 0 && errno == EPERM);
    pid_t pid = fork();
    if (pid == 0) _exit(execl("/bin/true", "true", (char *)NULL) < 0 && errno == EACCES ? 7 : 1);
    int st = 0; waitpid(pid, &st, 0);
    say("forks_but_cannot_exec", WIFEXITED(st) && WEXITSTATUS(st) == 7);
    fflush(stdout);
    return 0;
}
"""


# Without Landlock the verifier can read its user's other files and run other programs; nothing else changes.
WITHOUT_LANDLOCK = {"no_other_files", "forks_but_cannot_exec"}


@pytest.mark.parametrize("confine", ["landlock", "seccomp-only"])
def test_the_confinement_refuses_what_a_verifier_never_needs(rust_bin, tmp_path, confine):  # noqa: F811
    cc = shutil.which("cc") or shutil.which("gcc")
    if not cc:
        pytest.skip("needs a C compiler")
    if confine == "seccomp-only" and os.uname().machine != "aarch64":
        pytest.skip("seccomp-only is for aarch64")
    root = tmp_path / "root"
    (root / "config").mkdir(parents=True)
    (root / "ivk").mkdir()
    (tmp_path / "probe.c").write_text(PROBE)
    probe = root / "cp_verify"
    subprocess.run([cc, "-O1", "-o", str(probe), str(tmp_path / "probe.c")], check=True)
    job = root / "job-1"
    job.mkdir()
    for name in ("request", "next", "proof"):
        (job / name).write_bytes(name.encode())
    before = {p.name: (p.read_bytes(), p.stat().st_mode) for p in job.iterdir()}
    elf = probe.read_bytes()
    interp = elf[elf.index(b"/lib"):].split(b"\0")[0].decode()
    args = [str(rust_bin), "--flashprove-child", confine, "5", str(root), str(probe), str(Path(interp).resolve()),
            str(root / "config"), str(root / "ivk"), str(job / "request"), str(job / "next"), str(job / "proof"), "a" * 64]
    done = subprocess.run(args, capture_output=True, timeout=30, env={"HOME": "/nonexistent", "PATH": "/usr/bin"})
    lines = done.stdout.decode().splitlines()
    assert done.returncode == 0, (done.returncode, lines, done.stderr)
    assert len(lines) == 26, lines
    failed = {l.split()[0] for l in lines if not l.endswith(" ok")}
    assert failed == (set() if confine == "landlock" else WITHOUT_LANDLOCK), lines
    assert {p.name: (p.read_bytes(), p.stat().st_mode) for p in job.iterdir()} == before


def test_flashprove_flags_are_checked(rust_bin, tmp_path):  # noqa: F811
    base = [str(rust_bin), "--listen", "127.0.0.1:1", "--server", "127.0.0.1:2", "--bundle", str(tmp_path),
            "--pins", str(PINS)]
    for extra in (["--backend", "flashprove"],
                  ["--backend", "flashprove", "--verifier", "/bin/true", "--proof-bytes", "10"],
                  ["--backend", "flashprove", "--verifier", "/bin/true", "--domain", str(PINS)],
                  ["--verifier", "/bin/true", "--proof-bytes", "10"],
                  ["--confine", "seccomp-only", "--proof-bytes", "10"],
                  ["--backend", "flashprove", "--verifier", "/bin/true", "--confine", "none"],
                  ["--backend", "other", "--proof-bytes", "10"]):
        done = subprocess.run(base + extra, capture_output=True, timeout=10)
        assert done.returncode == 2, extra
    done = subprocess.run(base + ["--backend", "flashprove", "--verifier", "/bin/true", "--jobs", str(tmp_path)],
                          capture_output=True, timeout=10)
    assert done.returncode == 2 and b"pin" in done.stderr
