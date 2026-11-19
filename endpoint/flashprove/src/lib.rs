#![forbid(unsafe_code)]
//! The statement the endpoint asks FlashProve's `cp_verify` to check, built by the endpoint alone,
//! and a strict reading of the verifier's answer. Both follow FlashProve 9e2bdd8: the request file
//! and the closing lines of `verifier/docs/output.md`. See `docs/FLASHPROVE_BINDING.md`.

pub const RECORD_CLAUSE: &str = "; the table has a record: the binding of the weights rests on the theorem on codes (ePrint 2025/2055, Theorem 4.6), taken as given";

const KEYS: [&str; 28] = [
    "status", "claim", "id", "bits", "work", "work-rounds", "least-round", "least-round-without-work",
    "commitment-assumed-rounds", "record", "r", "e", "T", "tokens", "y", "policy", "C", "theta", "cap",
    "s", "sh", "sT", "F", "above-clip", "leaf", "stages", "dw", "build",
];

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Invalid;

/// Pinned in the endpoint's bundle; nothing here comes from the server.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Pins {
    pub id: String,
    pub build: String,
    pub window: u32,
    pub vocab: u32,
    pub filler: u32,
    pub prompt_len: usize,
    pub reply_len: usize,
    pub sh: u32,
    pub max_work: u32,
    pub assumed_rounds: u64,
}

fn hex64(s: &str) -> bool {
    s.len() == 64 && s.bytes().all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

impl Pins {
    /// At least one checked token lies inside the request (r <= T - 2), so the claim is exact-match.
    pub fn check(&self) -> Result<(), Invalid> {
        let t = self.window as usize;
        let ok = hex64(&self.id)
            && hex64(&self.build)
            && self.window >= 2
            && self.window.is_power_of_two()
            && self.vocab > 0
            && self.filler < self.vocab
            && self.prompt_len >= 1
            && self.reply_len >= 1
            && self.prompt_len < t
            && self.prompt_len + self.reply_len <= t + 1
            && (self.sh == 7 || self.sh == 8)
            && self.max_work <= 32;
        if ok { Ok(()) } else { Err(Invalid) }
    }
}

/// The largest proof a setup may allow: the token wire's own limit.
pub const PROOF_LIMIT: usize = 1 << 26;
const MAX_FILES: usize = 64;
const SETUP_KEYS: [&str; 12] = [
    "id", "build", "window", "vocab", "filler", "prompt", "reply", "sh", "work", "assumed-rounds",
    "proof-bytes", "verifier",
];
const REQUIRED_FILES: [&str; 4] = ["config/policy.txt", "config/KL.bin", "config/layout.txt", "ivk/key.bin"];

/// Everything the endpoint pins for one model: the statement's pins, the largest proof, the
/// verifier binary's sha256 and the sha256 of every bundle file a whole call reads.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Setup {
    pub pins: Pins,
    pub proof_max: usize,
    pub verifier: String,
    pub files: Vec<(String, String)>,
}

fn bundle_file(path: &str) -> bool {
    REQUIRED_FILES.contains(&path)
        || path
            .strip_prefix("ivk/leaf/")
            .and_then(|n| n.strip_suffix(".bin"))
            .is_some_and(|n| whole(n).is_some())
}

/// One `key value` line per setting, each exactly once, and one `file <path> <sha256>` line per
/// bundle file, all separated by single spaces.
pub fn parse_setup(text: &str) -> Result<Setup, Invalid> {
    let body = text.strip_suffix('\n').unwrap_or(text);
    if body.is_empty() {
        return Err(Invalid);
    }
    let mut values: Vec<Option<&str>> = vec![None; SETUP_KEYS.len()];
    let mut files: Vec<(String, String)> = Vec::new();
    for line in body.split('\n') {
        match line.split(' ').collect::<Vec<_>>().as_slice() {
            ["file", path, digest] => {
                if !bundle_file(path) || !hex64(digest) || files.iter().any(|(p, _)| p == path) || files.len() == MAX_FILES {
                    return Err(Invalid);
                }
                files.push((path.to_string(), digest.to_string()));
            }
            [key, value] => {
                let i = SETUP_KEYS.iter().position(|k| k == key).ok_or(Invalid)?;
                if value.is_empty() || values[i].replace(value).is_some() {
                    return Err(Invalid);
                }
            }
            _ => return Err(Invalid),
        }
    }
    let v: Vec<&str> = values.into_iter().collect::<Option<_>>().ok_or(Invalid)?;
    let num = |s: &str| whole(s).ok_or(Invalid);
    let small = |s: &str| num(s).and_then(|n| u32::try_from(n).map_err(|_| Invalid));
    let size = |s: &str| num(s).and_then(|n| usize::try_from(n).map_err(|_| Invalid));
    let pins = Pins {
        id: v[0].to_string(),
        build: v[1].to_string(),
        window: small(v[2])?,
        vocab: small(v[3])?,
        filler: small(v[4])?,
        prompt_len: size(v[5])?,
        reply_len: size(v[6])?,
        sh: small(v[7])?,
        max_work: small(v[8])?,
        assumed_rounds: num(v[9])?,
    };
    pins.check()?;
    let proof_max = size(v[10])?;
    let complete = REQUIRED_FILES.iter().all(|r| files.iter().any(|(p, _)| p == r))
        && files.iter().any(|(p, _)| p.starts_with("ivk/leaf/"));
    if !(1..=PROOF_LIMIT).contains(&proof_max) || !hex64(v[11]) || !complete {
        return Err(Invalid);
    }
    Ok(Setup { pins, proof_max, verifier: v[11].to_string(), files })
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Statement {
    pub request: Vec<u8>,
    pub next: [u8; 4],
    pub r: u32,
    pub e: u32,
    pub window: u32,
    pub y_checked: bool,
}

impl Statement {
    /// The `tokens=` field `cp_verify` must print: r + 1 to min(e, T - 1).
    pub fn tokens_field(&self) -> String {
        format!("{}..{}", self.r + 1, self.e.min(self.window - 1))
    }
}

/// Request and next-token files from the admitted prompt and the candidate reply.
pub fn build(p: &Pins, prompt: &[u32], reply: &[u32]) -> Result<Statement, Invalid> {
    p.check()?;
    if prompt.len() != p.prompt_len || reply.len() != p.reply_len {
        return Err(Invalid);
    }
    if prompt.iter().chain(reply).any(|&t| t >= p.vocab) {
        return Err(Invalid);
    }
    let t = p.window as usize;
    let mut ids: Vec<u32> = prompt.iter().chain(reply).copied().collect();
    let (next, y_checked) = if ids.len() == t + 1 {
        match ids.pop() {
            Some(y) => (y, true),
            None => return Err(Invalid),
        }
    } else {
        ids.resize(t, p.filler);
        (0, false)
    };
    let r = (p.prompt_len - 1) as u32;
    let e = (p.prompt_len + p.reply_len - 1) as u32;
    let mut request = Vec::with_capacity(4 * (t + 2));
    for v in ids.iter().chain([r, e].iter()) {
        request.extend_from_slice(&v.to_le_bytes());
    }
    Ok(Statement { request, next: next.to_le_bytes(), r, e, window: p.window, y_checked })
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Cause {
    Exit,
    Text,
    TestBuild,
    Verdict,
    ResultLine,
    Commitment,
    Field,
    Work,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Outcome {
    Accept,
    Reject,
    Error(Cause),
}

fn whole(s: &str) -> Option<u64> {
    if s.is_empty() || s.len() > 19 || (s.len() > 1 && s.starts_with('0')) || !s.bytes().all(|b| b.is_ascii_digit()) {
        return None;
    }
    s.parse().ok()
}

fn decimal2(s: &str) -> bool {
    match s.split_once('.') {
        Some((a, b)) => whole(a).is_some() && b.len() == 2 && b.bytes().all(|c| c.is_ascii_digit()),
        None => false,
    }
}

struct Work<'a> {
    bits: u64,
    rounds: u64,
    least: &'a str,
    least_without: &'a str,
}

fn result_line<'a>(p: &Pins, st: &Statement, line: &'a str) -> Option<Work<'a>> {
    let mut parts = line.split(' ');
    if parts.next()? != "result-v1" {
        return None;
    }
    let mut v: Vec<&str> = Vec::with_capacity(KEYS.len());
    for key in KEYS {
        let (k, val) = parts.next()?.split_once('=')?;
        if k != key || val.is_empty() {
            return None;
        }
        v.push(val);
    }
    if parts.next().is_some() {
        return None;
    }
    let m = 1u64 << p.sh;
    let work = whole(v[4])?;
    let rounds = whole(v[5])?;
    let checks = [
        v[0] == "0",
        v[1] == "exact-match",
        v[2] == p.id,
        v[3] == "128",
        work <= u64::from(p.max_work),
        (work == 0) == (rounds == 0),
        decimal2(v[6]),
        decimal2(v[7]),
        whole(v[8]) == Some(p.assumed_rounds),
        v[9] == "1",
        whole(v[10]) == Some(u64::from(st.r)),
        whole(v[11]) == Some(u64::from(st.e)),
        whole(v[12]) == Some(u64::from(st.window)),
        v[13] == st.tokens_field(),
        v[14] == if st.y_checked { "yes" } else { "no" },
        v[15] == "exact-match",
        whole(v[16]) == Some(m),
        whole(v[17]) == Some(m),
        v[18] == "0",
        whole(v[19]).is_some(),
        whole(v[20]) == Some(u64::from(p.sh)),
        whole(v[21]) == Some(u64::from(p.sh) - 4),
        v[22] == "24",
        v[23] == "0",
        whole(v[24]).is_some(),
        v[25] == "key,leaf,request",
        v[26] == "8",
        v[27] == p.build,
    ];
    if checks.iter().all(|&c| c) {
        Some(Work { bits: work, rounds, least: v[6], least_without: v[7] })
    } else {
        None
    }
}

fn work_line(line: &str, w: &Work) -> bool {
    if w.bits == 0 {
        return line == "proof of work: none counted";
    }
    let unit = if w.rounds == 1 { "round" } else { "rounds" };
    let head = format!(
        "proof of work: up to {} bits counted in {} {}; with it the least round has ",
        w.bits, w.rounds, unit
    );
    line == format!("{head}{} bits, without it {} bits", w.least, w.least_without)
}

/// Reads one whole `cp_verify` call. Only `Accept` may release a reply; the outward reply for
/// `Reject` and `Error` is the same fixed `unavailable`.
pub fn judge(p: &Pins, st: &Statement, exit: Option<i32>, stdout: &[u8]) -> Outcome {
    if p.check().is_err() || st.window != p.window {
        return Outcome::Error(Cause::Field);
    }
    let Ok(text) = std::str::from_utf8(stdout) else {
        return Outcome::Error(Cause::Text);
    };
    let mut lines: Vec<&str> = text.split('\n').collect();
    if lines.last() == Some(&"") {
        lines.pop();
    }
    match exit {
        Some(0) => {}
        Some(1) if lines.last().is_some_and(|l| l.starts_with("NO: ")) => return Outcome::Reject,
        _ => return Outcome::Error(Cause::Exit),
    }
    if lines.iter().any(|l| l.starts_with("TEST BUILD:")) {
        return Outcome::Error(Cause::TestBuild);
    }
    let n = lines.len();
    if n < 4 {
        return Outcome::Error(Cause::Text);
    }
    if lines[n - 1] != "YES" {
        return Outcome::Error(Cause::Verdict);
    }
    let found: Vec<usize> = (0..n).filter(|&i| lines[i].starts_with("result-v1")).collect();
    if found != [n - 2] {
        return Outcome::Error(Cause::ResultLine);
    }
    let c = lines[n - 4];
    if !(c.starts_with("commitment: ") && c.ends_with(RECORD_CLAUSE)) {
        return Outcome::Error(Cause::Commitment);
    }
    let Some(w) = result_line(p, st, lines[n - 2]) else {
        return Outcome::Error(Cause::Field);
    };
    if !work_line(lines[n - 3], &w) {
        return Outcome::Error(Cause::Work);
    }
    Outcome::Accept
}
