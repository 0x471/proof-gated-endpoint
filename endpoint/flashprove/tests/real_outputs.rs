use pge_flashprove::{build, judge, Cause, Outcome, Pins, Statement};

const REQ: &[u8] = include_bytes!("data/small/example.req");
const Y: &[u8] = include_bytes!("data/small/example.y");
const ID: &str = include_str!("data/small/ID.txt");
const BUILD: &str = "93afdaf592393a54ebd17b681d1c03295eb883f0161ff0e6ab872a3b39939595";
const ACCEPT: &[u8] = include_bytes!("data/small/outputs/accept.out");
const NO_ID: &[u8] = include_bytes!("data/small/outputs/no_id_exit14.out");
const REJECTED: [(&str, &[u8]); 8] = [
    ("proof-refused", include_bytes!("data/small/outputs/no_prompt_changed.out")),
    ("reply-differs", include_bytes!("data/small/outputs/no_y_changed.out")),
    ("statement-range", include_bytes!("data/small/outputs/no_range.out")),
    ("model-statement", include_bytes!("data/small/outputs/no_other_id.out")),
    ("cp-proof-head", include_bytes!("data/small/outputs/no_proof_head.out")),
    ("proof-refused", include_bytes!("data/small/outputs/no_proof_flipped.out")),
    ("cp-proof-length", include_bytes!("data/small/outputs/no_proof_empty.out")),
    ("key-refused", include_bytes!("data/small/outputs/no_key_changed.out")),
];

fn pins() -> Pins {
    Pins {
        id: ID.to_string(),
        build: BUILD.to_string(),
        window: 64,
        vocab: 512,
        filler: 0,
        prompt_len: 61,
        reply_len: 4,
        sh: 8,
        max_work: 32,
        assumed_rounds: 49,
    }
}

fn statement() -> Statement {
    let words: Vec<u32> = REQ.chunks(4).map(|c| u32::from_le_bytes([c[0], c[1], c[2], c[3]])).collect();
    let mut reply = words[61..64].to_vec();
    reply.push(u32::from_le_bytes([Y[0], Y[1], Y[2], Y[3]]));
    build(&pins(), &words[..61], &reply).unwrap()
}

#[test]
fn the_real_accepted_call_is_accepted() {
    assert_eq!(judge(&pins(), &statement(), Some(0), ACCEPT), Outcome::Accept);
}

#[test]
fn the_real_accepted_text_needs_exit_code_zero() {
    for code in [None, Some(1), Some(5), Some(14), Some(15)] {
        assert_eq!(judge(&pins(), &statement(), code, ACCEPT), Outcome::Error(Cause::Exit), "{code:?}");
    }
}

#[test]
fn every_real_rejection_is_a_rejection() {
    for (name, out) in REJECTED {
        let text = std::str::from_utf8(out).unwrap();
        assert_eq!(text.lines().last(), Some(format!("NO: {name}").as_str()));
        assert_eq!(judge(&pins(), &statement(), Some(1), out), Outcome::Reject, "{name}");
        assert_eq!(judge(&pins(), &statement(), Some(0), out), Outcome::Error(Cause::Text), "{name}");
    }
}

#[test]
fn a_call_without_an_id_is_never_accepted() {
    assert_eq!(judge(&pins(), &statement(), Some(14), NO_ID), Outcome::Error(Cause::Exit));
    assert_eq!(judge(&pins(), &statement(), Some(0), NO_ID), Outcome::Error(Cause::Verdict));
}

#[test]
fn the_real_accepted_call_binds_the_pins() {
    let other = Pins { id: "0".repeat(64), ..pins() };
    assert_eq!(judge(&other, &statement(), Some(0), ACCEPT), Outcome::Error(Cause::Field));
    let other = Pins { build: "c".repeat(64), ..pins() };
    assert_eq!(judge(&other, &statement(), Some(0), ACCEPT), Outcome::Error(Cause::Field));
    let other = Pins { assumed_rounds: 48, ..pins() };
    assert_eq!(judge(&other, &statement(), Some(0), ACCEPT), Outcome::Error(Cause::Field));
    let other = Pins { max_work: 31, ..pins() };
    assert_eq!(judge(&other, &statement(), Some(0), ACCEPT), Outcome::Error(Cause::Field));
}

#[test]
fn a_cut_accepted_call_is_never_accepted() {
    let text = std::str::from_utf8(ACCEPT).unwrap();
    let lines: Vec<&str> = text.lines().collect();
    for keep in [lines.len() - 1, lines.len() - 2, lines.len() - 3, 3] {
        let cut = lines[..keep].join("\n") + "\n";
        assert_ne!(judge(&pins(), &statement(), Some(0), cut.as_bytes()), Outcome::Accept, "{keep}");
    }
}
