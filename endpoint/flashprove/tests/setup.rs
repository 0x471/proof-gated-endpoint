use pge_flashprove::{parse_setup, PROOF_LIMIT};

const SMALL: &str = include_str!("../../../device/flashprove_small.pins");

fn replace_line(prefix: &str, with: &str) -> String {
    SMALL.lines().map(|l| if l.starts_with(prefix) { with.to_string() } else { l.to_string() }).collect::<Vec<_>>().join("\n") + "\n"
}

fn without(prefix: &str) -> String {
    SMALL.lines().filter(|l| !l.starts_with(prefix)).collect::<Vec<_>>().join("\n") + "\n"
}

#[test]
fn reads_the_stored_proofs_pins() {
    let s = parse_setup(SMALL).unwrap();
    assert_eq!(s.pins.id, "79a5a2bc7a469b40df73dedf40abeb740671e8ae8077738bd3743897aeb22183");
    assert_eq!((s.pins.window, s.pins.vocab, s.pins.prompt_len, s.pins.reply_len), (64, 512, 61, 4));
    assert_eq!((s.pins.sh, s.pins.max_work, s.pins.assumed_rounds), (8, 32, 49));
    assert_eq!(s.proof_max, 8 << 20);
    assert_eq!(s.files.len(), 5);
    assert_eq!(s.files[4], ("ivk/leaf/0.bin".into(), "c9484f50fedfaf4e28ec9d1b5d19c3cd5e97bf4582511da49f9eb5693f10e1e0".into()));
    assert_eq!(parse_setup(SMALL.trim_end_matches('\n')).unwrap(), s);
}

#[test]
fn every_setting_is_required() {
    for key in ["id ", "build ", "window ", "vocab ", "filler ", "prompt ", "reply ", "sh ", "work ", "assumed-rounds ", "proof-bytes ", "verifier "] {
        assert!(parse_setup(&without(key)).is_err(), "{key}");
    }
    for file in ["file config/policy.txt", "file config/KL.bin", "file config/layout.txt", "file ivk/key.bin", "file ivk/leaf/0.bin"] {
        assert!(parse_setup(&without(file)).is_err(), "{file}");
    }
}

#[test]
fn repeats_unknown_lines_and_loose_spacing_are_refused() {
    let first = SMALL.lines().next().unwrap();
    for text in [
        format!("{SMALL}{first}\n"),
        format!("{SMALL}file ivk/leaf/0.bin {}\n", "0".repeat(64)),
        format!("{SMALL}policy exact-match\n"),
        format!("{SMALL}\n"),
        format!("# pins\n{SMALL}"),
        SMALL.replacen("window 64", "window  64", 1),
        SMALL.replacen("window 64", "window 64 ", 1),
        SMALL.replacen("window 64", "window\t64", 1),
        SMALL.replace('\n', "\r\n"),
        String::new(),
        "\n".into(),
    ] {
        assert!(parse_setup(&text).is_err(), "{text:?}");
    }
}

#[test]
fn values_must_be_canonical_and_in_range() {
    for line in [
        "window 064",
        "window 48",
        "window 1",
        "vocab 0",
        "filler 512",
        "prompt 64",
        "prompt 0",
        "reply 5",
        "reply 0",
        "sh 6",
        "work 33",
        "work -1",
        "assumed-rounds 4.9",
        "proof-bytes 0",
        "id 79A5A2BC7A469B40DF73DEDF40ABEB740671E8AE8077738BD3743897AEB22183",
        "build 93afdaf5",
        "verifier xyz",
    ] {
        let key = line.split(' ').next().unwrap();
        assert!(parse_setup(&replace_line(&format!("{key} "), line)).is_err(), "{line}");
    }
    let limit = replace_line("proof-bytes ", &format!("proof-bytes {}", PROOF_LIMIT + 1));
    assert!(parse_setup(&limit).is_err());
    let at_limit = replace_line("proof-bytes ", &format!("proof-bytes {PROOF_LIMIT}"));
    assert_eq!(parse_setup(&at_limit).unwrap().proof_max, PROOF_LIMIT);
}

#[test]
fn only_files_a_whole_call_reads_are_pinned() {
    let digest = "c9484f50fedfaf4e28ec9d1b5d19c3cd5e97bf4582511da49f9eb5693f10e1e0";
    for path in ["config/other.txt", "ivk/leaf/01.bin", "ivk/leaf/x.bin", "ivk/leaf/0.txt", "ivk/../config/KL.bin", "/ivk/key.bin", "ivk/leaf/0.bin/", "ivk/leaf/.bin"] {
        assert!(parse_setup(&format!("{SMALL}file {path} {digest}\n")).is_err(), "{path}");
    }
    let more = parse_setup(&format!("{SMALL}file ivk/leaf/1.bin {digest}\n")).unwrap();
    assert_eq!(more.files.len(), 6);
    let bad = SMALL.replacen("c9484f50", "C9484F50", 1);
    assert!(parse_setup(&bad).is_err());
}
