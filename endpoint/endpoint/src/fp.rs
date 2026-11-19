//! The FlashProve backend (docs/FLASHPROVE_BINDING.md). At start-up every pinned file and the
//! verifier are hashed and copied into a private directory; each check writes its request, y and
//! proof into a fresh directory there and runs one whole `cp_verify` call on the copies, started
//! through this binary so that it is limited and confined before the verifier runs.

use crate::process;
use pge_flashprove::{judge, parse_setup, Outcome, Setup, Statement};
use pge_verify::{digest, read_regular, Verdict};
use std::{
    fs::{self, DirBuilder, OpenOptions},
    io::Write,
    os::unix::{
        ffi::OsStrExt,
        fs::{DirBuilderExt, OpenOptionsExt},
        process::CommandExt,
    },
    path::{Path, PathBuf},
    process::{Command, ExitCode},
    time::{Duration, Instant},
};

const FILE_LIMIT: usize = 64 << 20;

pub struct Flashprove {
    pub setup: Setup,
    root: PathBuf,
    verifier: PathBuf,
    interpreter: Option<PathBuf>,
    landlock: bool,
}

/// The program interpreter a dynamically linked 64-bit little-endian ELF file names (PT_INTERP).
pub fn interpreter(elf: &[u8]) -> Result<Option<PathBuf>, &'static str> {
    let u16_at = |o: usize| elf.get(o..o + 2).map(|b| u16::from_le_bytes([b[0], b[1]]) as usize);
    let u32_at = |o: usize| elf.get(o..o + 4).map(|b| u32::from_le_bytes([b[0], b[1], b[2], b[3]]));
    let u64_at = |o: usize| {
        elf.get(o..o + 8)
            .and_then(|b| b.try_into().ok())
            .and_then(|b| usize::try_from(u64::from_le_bytes(b)).ok())
    };
    if elf.get(..6) != Some(b"\x7fELF\x02\x01".as_slice()) {
        return Err("verifier is not a 64-bit little-endian ELF file");
    }
    let (phoff, size, count) = match (u64_at(0x20), u16_at(0x36), u16_at(0x38)) {
        (Some(o), Some(s), Some(n)) if s >= 56 => (o, s, n),
        _ => return Err("verifier ELF header"),
    };
    for i in 0..count {
        let ph = phoff.checked_add(i * size).ok_or("verifier ELF header")?;
        if elf.get(ph..ph.checked_add(size).ok_or("verifier ELF header")?).is_none() {
            return Err("verifier ELF header");
        }
        if u32_at(ph) != Some(3) {
            continue;
        }
        let (Some(offset), Some(len)) = (u64_at(ph + 8), u64_at(ph + 32)) else {
            return Err("verifier ELF header");
        };
        let raw = elf.get(offset..offset.checked_add(len).ok_or("verifier ELF header")?);
        let path = raw.and_then(|r| r.strip_suffix(b"\0")).ok_or("verifier interpreter")?;
        if path.is_empty() || path.contains(&0) || path[0] != b'/' {
            return Err("verifier interpreter");
        }
        return Ok(Some(PathBuf::from(std::ffi::OsStr::from_bytes(path))));
    }
    Ok(None)
}

fn write_new(path: &Path, data: &[u8], mode: u32) -> std::io::Result<()> {
    OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(mode)
        .open(path)?
        .write_all(data)
}

impl Flashprove {
    pub fn load(bundle: &Path, pins: &Path, verifier: &Path, jobs: &Path, landlock: bool) -> Result<Self, String> {
        let text = read_regular(pins, 65536)?;
        let setup = parse_setup(std::str::from_utf8(&text).map_err(|_| "pins encoding")?)
            .map_err(|_| "invalid FlashProve pins")?;
        let binary = read_regular(verifier, FILE_LIMIT)?;
        if digest(&binary) != setup.verifier {
            return Err("verifier does not match its pin".into());
        }
        let interpreter = match interpreter(&binary)? {
            Some(p) => Some(fs::canonicalize(&p).map_err(|_| "verifier interpreter missing")?),
            None => None,
        };
        let mut files = Vec::with_capacity(setup.files.len());
        for (path, want) in &setup.files {
            let data = read_regular(&bundle.join(path), FILE_LIMIT)?;
            if digest(&data) != *want {
                return Err(format!("{path} does not match its pin"));
            }
            files.push((path.clone(), data));
        }
        let root = jobs.join(format!("pge-flashprove-{}", std::process::id()));
        let dir = |p: &Path| {
            DirBuilder::new()
                .mode(0o700)
                .create(p)
                .map_err(|e| format!("{}: {e}", p.display()))
        };
        dir(&root)?;
        for sub in ["config", "ivk", "ivk/leaf"] {
            dir(&root.join(sub))?;
        }
        for (path, data) in &files {
            write_new(&root.join(path), data, 0o400).map_err(|e| format!("{path}: {e}"))?;
        }
        let copy = root.join("cp_verify");
        write_new(&copy, &binary, 0o500).map_err(|e| format!("verifier copy: {e}"))?;
        Ok(Self {
            setup,
            root,
            verifier: copy,
            interpreter,
            landlock,
        })
    }

    /// The confinement every check runs under, as `--confine` names it.
    pub fn confinement(&self) -> &'static str {
        if self.landlock {
            "landlock"
        } else {
            "seccomp-only"
        }
    }

    /// The private copies, read again before every check: a changed copy is an error, never a check.
    fn intact(&self) -> bool {
        let same = |path: &Path, want: &str| {
            read_regular(path, FILE_LIMIT).is_ok_and(|data| digest(&data) == want)
        };
        same(&self.verifier, &self.setup.verifier)
            && self
                .setup
                .files
                .iter()
                .all(|(path, want)| same(&self.root.join(path), want))
    }

    pub fn check(&self, seq: u64, st: &Statement, proof: &[u8], budget: Duration) -> Verdict {
        let start = Instant::now();
        if !self.intact() {
            return Verdict::Error;
        }
        let dir = self.root.join(format!("job-{seq}"));
        if DirBuilder::new().mode(0o700).create(&dir).is_err() {
            return Verdict::Error;
        }
        let verdict = self.run(&dir, st, proof, budget, start);
        let _ = fs::remove_dir_all(&dir);
        verdict
    }

    fn run(&self, dir: &Path, st: &Statement, proof: &[u8], budget: Duration, start: Instant) -> Verdict {
        let (request, next, proof_file) = (dir.join("request"), dir.join("next"), dir.join("proof"));
        if write_new(&request, &st.request, 0o400).is_err()
            || write_new(&next, &st.next, 0o400).is_err()
            || write_new(&proof_file, proof, 0o400).is_err()
        {
            return Verdict::Error;
        }
        let Some(exe) = crate::EXE.get() else {
            return Verdict::Error;
        };
        let Some(left) = budget.checked_sub(start.elapsed()).filter(|d| !d.is_zero()) else {
            return Verdict::Error;
        };
        let interpreter = self.interpreter.as_deref().unwrap_or(Path::new("-"));
        let mut cmd = Command::new(exe);
        cmd.arg("--flashprove-child")
            .arg(self.confinement())
            .arg(left.as_secs_f64().ceil().max(1.).to_string())
            .arg(&self.root)
            .arg(&self.verifier)
            .arg(interpreter)
            .arg(self.root.join("config"))
            .arg(self.root.join("ivk"))
            .arg(&request)
            .arg(&next)
            .arg(&proof_file)
            .arg(&self.setup.pins.id);
        match process::collect(cmd, left, b"") {
            Some((code, out, err)) if err.is_empty() => match judge(&self.setup.pins, st, code, &out) {
                Outcome::Accept => Verdict::Accept,
                Outcome::Reject => Verdict::Reject,
                Outcome::Error(_) => Verdict::Error,
            },
            _ => Verdict::Error,
        }
    }
}

/// `--flashprove-child CONFINE CPU ROOT VERIFIER INTERPRETER CONFIG IVK REQUEST NEXT PROOF ID`:
/// limits and confines this fresh process, then replaces it with one whole call of the verifier.
pub fn child(args: &[String]) -> ExitCode {
    if args.len() != 12 {
        return ExitCode::from(2);
    }
    let landlock = match args[1].as_str() {
        "landlock" => true,
        "seccomp-only" => false,
        _ => return ExitCode::from(2),
    };
    let Ok(cpu) = args[2].parse::<u64>() else {
        return ExitCode::from(2);
    };
    let interpreter = (args[5] != "-").then(|| Path::new(&args[5]));
    if !process::die_with_parent()
        || process::limits(cpu).is_err()
        || process::confine_verifier(Path::new(&args[3]), Path::new(&args[4]), interpreter, landlock).is_err()
    {
        return ExitCode::from(2);
    }
    let _ = Command::new(&args[4])
        .args(&args[6..11])
        .args(["-id", &args[11], "-q"])
        .env_clear()
        .exec();
    ExitCode::from(2)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn elf_with_interp(path: &[u8]) -> Vec<u8> {
        let mut elf = vec![0u8; 0x200];
        elf[..6].copy_from_slice(b"\x7fELF\x02\x01");
        elf[0x20..0x28].copy_from_slice(&64u64.to_le_bytes());
        elf[0x36..0x38].copy_from_slice(&56u16.to_le_bytes());
        elf[0x38..0x3a].copy_from_slice(&2u16.to_le_bytes());
        // A PT_LOAD first, then PT_INTERP pointing at the path at 0x100.
        elf[64..68].copy_from_slice(&1u32.to_le_bytes());
        let ph = 64 + 56;
        elf[ph..ph + 4].copy_from_slice(&3u32.to_le_bytes());
        elf[ph + 8..ph + 16].copy_from_slice(&0x100u64.to_le_bytes());
        elf[ph + 32..ph + 40].copy_from_slice(&(path.len() as u64).to_le_bytes());
        elf[0x100..0x100 + path.len()].copy_from_slice(path);
        elf
    }

    #[test]
    fn finds_the_program_interpreter() {
        let elf = elf_with_interp(b"/lib/ld-linux-aarch64.so.1\0");
        assert_eq!(interpreter(&elf), Ok(Some(PathBuf::from("/lib/ld-linux-aarch64.so.1"))));
        let mut stat = elf.clone();
        stat[64 + 56..64 + 60].copy_from_slice(&1u32.to_le_bytes());
        assert_eq!(interpreter(&stat), Ok(None));
    }

    #[test]
    fn refuses_odd_interpreters_and_headers() {
        for path in [&b"lib/ld.so\0"[..], b"/lib/ld.so", b"\0", b"/lib/l\0d.so\0"] {
            assert!(interpreter(&elf_with_interp(path)).is_err(), "{path:?}");
        }
        assert!(interpreter(b"#!/bin/sh\n").is_err());
        let mut short = elf_with_interp(b"/x\0");
        short.truncate(0x40);
        assert!(interpreter(&short).is_err());
        let mut far = elf_with_interp(b"/x\0");
        far[64 + 56 + 8..64 + 56 + 16].copy_from_slice(&u64::MAX.to_le_bytes());
        assert!(interpreter(&far).is_err());
    }

    #[cfg(target_os = "linux")]
    fn scratch(tag: &str) -> PathBuf {
        let p = std::env::temp_dir().join(format!("pge-fp-{tag}-{}", std::process::id()));
        let _ = fs::remove_dir_all(&p);
        fs::create_dir_all(&p).unwrap();
        p
    }

    // A bundle of stand-in files and a pins file that pins them, with a real ELF as the verifier.
    #[cfg(target_os = "linux")]
    fn bundle(dir: &Path) -> (PathBuf, PathBuf, PathBuf) {
        let src = dir.join("bundle");
        for sub in ["config", "ivk/leaf"] {
            fs::create_dir_all(src.join(sub)).unwrap();
        }
        let names = ["config/policy.txt", "config/KL.bin", "config/layout.txt", "ivk/key.bin", "ivk/leaf/0.bin"];
        // Any small dynamically linked program will do; the test binary is over the size limit.
        let verifier = fs::canonicalize("/bin/true").unwrap();
        let mut pins = String::from(include_str!("../../../device/flashprove_small.pins"))
            .lines()
            .filter(|l| !l.starts_with("file ") && !l.starts_with("verifier "))
            .map(|l| format!("{l}\n"))
            .collect::<String>();
        pins += &format!("verifier {}\n", digest(&fs::read(&verifier).unwrap()));
        for name in names {
            fs::write(src.join(name), name.as_bytes()).unwrap();
            pins += &format!("file {name} {}\n", digest(name.as_bytes()));
        }
        let pins_path = dir.join("pins");
        fs::write(&pins_path, pins).unwrap();
        (src, pins_path, verifier)
    }

    #[cfg(target_os = "linux")]
    #[test]
    fn copies_the_pinned_files_into_a_private_directory() {
        let dir = scratch("copy");
        let (src, pins, verifier) = bundle(&dir);
        let jobs = dir.join("jobs");
        fs::create_dir(&jobs).unwrap();
        let fp = Flashprove::load(&src, &pins, &verifier, &jobs, true).unwrap();
        assert!(fp.intact());
        use std::os::unix::fs::PermissionsExt;
        let mode = |p: &Path| fs::metadata(p).unwrap().permissions().mode() & 0o777;
        assert_eq!(mode(&fp.root), 0o700);
        assert_eq!(mode(&fp.verifier), 0o500);
        assert_eq!(mode(&fp.root.join("ivk/key.bin")), 0o400);
        assert_eq!(fs::read(fp.root.join("config/KL.bin")).unwrap(), b"config/KL.bin");
        // A second endpoint with the same process id can't reuse the directory.
        assert!(Flashprove::load(&src, &pins, &verifier, &jobs, true).is_err());
        fs::remove_file(fp.root.join("ivk/key.bin")).unwrap();
        fs::write(fp.root.join("ivk/key.bin"), b"changed").unwrap();
        assert!(!fp.intact());
        fs::remove_dir_all(&dir).unwrap();
    }

    #[cfg(target_os = "linux")]
    #[test]
    fn refuses_anything_that_does_not_match_its_pin() {
        let dir = scratch("pins");
        let (src, pins, verifier) = bundle(&dir);
        let jobs = dir.join("jobs");
        fs::create_dir(&jobs).unwrap();
        fs::write(src.join("ivk/leaf/0.bin"), b"other").unwrap();
        assert!(Flashprove::load(&src, &pins, &verifier, &jobs, true).is_err());
        fs::write(src.join("ivk/leaf/0.bin"), b"ivk/leaf/0.bin").unwrap();
        fs::remove_file(src.join("config/layout.txt")).unwrap();
        assert!(Flashprove::load(&src, &pins, &verifier, &jobs, true).is_err());
        fs::write(src.join("config/layout.txt"), b"config/layout.txt").unwrap();
        let other = dir.join("other");
        fs::write(&other, b"not the pinned verifier").unwrap();
        assert!(Flashprove::load(&src, &pins, &other, &jobs, true).is_err());
        assert!(Flashprove::load(&src, &pins, &verifier, &dir.join("missing"), true).is_err());
        // Nothing was created by the refused loads.
        assert_eq!(fs::read_dir(&jobs).unwrap().count(), 0);
        assert!(Flashprove::load(&src, &pins, &verifier, &jobs, true).is_ok());
        fs::remove_dir_all(&dir).unwrap();
    }
}
