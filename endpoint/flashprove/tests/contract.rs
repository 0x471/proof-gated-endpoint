use pge_flashprove::{build, judge, Cause, Outcome, Pins, Statement};

const REQ: &[u8] = include_bytes!("data/small/example.req");
const Y: &[u8] = include_bytes!("data/small/example.y");
const ID: &str = include_str!("data/small/ID.txt");
// What the pinned build of cp_verify prints (data/small/outputs/README.md).
const BUILD: &str = "93afdaf592393a54ebd17b681d1c03295eb883f0161ff0e6ab872a3b39939595";

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

fn fixture_ids() -> (Vec<u32>, Vec<u32>) {
    let words: Vec<u32> = REQ.chunks(4).map(|c| u32::from_le_bytes([c[0], c[1], c[2], c[3]])).collect();
    assert_eq!(words.len(), 66);
    assert_eq!(&words[64..], &[60, 64]);
    let mut reply = words[61..64].to_vec();
    reply.push(u32::from_le_bytes([Y[0], Y[1], Y[2], Y[3]]));
    (words[..61].to_vec(), reply)
}

fn statement() -> Statement {
    let (prompt, reply) = fixture_ids();
    build(&pins(), &prompt, &reply).unwrap()
}

// Built from verifier/docs/output.md and examples/accepted-proof/README.md at FlashProve 9e2bdd8:
// the documented closing lines of the stored proof's accepted call, not a captured run.
fn accepted() -> String {
    format!(
        "---- the call ----\n\
         weight hiding: NOT CHECKED BY THIS CALL (it rests on how the commitment was made)\n\
         covered: r=60 e=64 tokens=61..63 y=yes next-token=no policy=exact-match\n\
         commitment: 49 closeness rounds of the one-time files rest on another source than the bound within a third of the distance; the table has a record: the binding of the weights rests on the theorem on codes (ePrint 2025/2055, Theorem 4.6), taken as given\n\
         proof of work: up to 32 bits counted in 38 rounds; with it the least round has 128.00 bits, without it 96.23 bits\n\
         result-v1 status=0 claim=exact-match id={ID} bits=128 work=32 work-rounds=38 least-round=128.00 least-round-without-work=96.23 commitment-assumed-rounds=49 record=1 r=60 e=64 T=64 tokens=61..63 y=yes policy=exact-match C=256 theta=256 cap=0 s=16 sh=8 sT=4 F=24 above-clip=0 leaf=0 stages=key,leaf,request dw=8 build={BUILD}\n\
         YES\n"
    )
}

fn edit_result(from: &str, to: &str) -> String {
    let lines: Vec<String> = accepted()
        .lines()
        .map(|l| if l.starts_with("result-v1 ") { l.replacen(from, to, 1) } else { l.to_string() })
        .collect();
    lines.join("\n") + "\n"
}

fn verdict(out: &str) -> Outcome {
    judge(&pins(), &statement(), Some(0), out.as_bytes())
}

#[test]
fn builds_the_stored_request_byte_for_byte() {
    let st = statement();
    assert_eq!(st.request, REQ);
    assert_eq!(&st.next[..], Y);
    assert_eq!((st.r, st.e, st.y_checked), (60, 64, true));
    assert_eq!(st.tokens_field(), "61..63");
}

#[test]
fn pads_a_short_reply_and_leaves_y_unchecked() {
    let p = Pins { prompt_len: 10, reply_len: 4, filler: 7, ..pins() };
    let st = build(&p, &[1; 10], &[2, 3, 4, 5]).unwrap();
    assert_eq!(st.request.len(), 4 * 66);
    let words: Vec<u32> = st.request.chunks(4).map(|c| u32::from_le_bytes([c[0], c[1], c[2], c[3]])).collect();
    assert_eq!(&words[10..14], &[2, 3, 4, 5]);
    assert!(words[14..64].iter().all(|&w| w == 7));
    assert_eq!(&words[64..], &[9, 13]);
    assert_eq!((st.next, st.y_checked), ([0; 4], false));
    assert_eq!(st.tokens_field(), "10..13");
}

#[test]
fn refuses_statements_it_cannot_bind() {
    let (prompt, reply) = fixture_ids();
    let p = pins();
    assert!(build(&p, &prompt[..60], &reply).is_err());
    assert!(build(&p, &prompt, &reply[..3]).is_err());
    let mut big = reply.clone();
    big[2] = 512;
    assert!(build(&p, &prompt, &big).is_err());
    for bad in [
        Pins { prompt_len: 64, reply_len: 1, ..pins() },
        Pins { prompt_len: 61, reply_len: 5, ..pins() },
        Pins { filler: 512, ..pins() },
        Pins { window: 48, ..pins() },
        Pins { sh: 6, ..pins() },
        Pins { max_work: 33, ..pins() },
        Pins { id: ID.to_uppercase(), ..pins() },
        Pins { build: "ab".into(), ..pins() },
    ] {
        assert!(bad.check().is_err(), "{bad:?}");
    }
}

#[test]
fn accepts_the_documented_answer() {
    assert_eq!(verdict(&accepted()), Outcome::Accept);
    assert_eq!(verdict(accepted().trim_end_matches('\n')), Outcome::Accept);
}

#[test]
fn exit_code_decides_first() {
    let st = statement();
    let p = pins();
    let out = accepted();
    assert_eq!(judge(&p, &st, Some(1), b"---- the call ----\nNO: proof-refused\n"), Outcome::Reject);
    assert_eq!(judge(&p, &st, Some(1), out.as_bytes()), Outcome::Error(Cause::Exit));
    for code in [None, Some(2), Some(5), Some(14), Some(15), Some(-9)] {
        assert_eq!(judge(&p, &st, code, out.as_bytes()), Outcome::Error(Cause::Exit), "{code:?}");
    }
    assert_eq!(judge(&p, &st, Some(5), b"NOT CHECKED: internal-stage\n"), Outcome::Error(Cause::Exit));
}

#[test]
fn every_changed_field_refuses() {
    let swaps = [
        ("status=0", "status=14"),
        ("claim=exact-match", "claim=next-token"),
        ("bits=128", "bits=127"),
        ("commitment-assumed-rounds=49", "commitment-assumed-rounds=48"),
        ("record=1", "record=0"),
        ("r=60", "r=59"),
        ("r=60", "r=060"),
        ("e=64", "e=63"),
        ("T=64", "T=128"),
        ("tokens=61..63", "tokens=61..62"),
        ("y=yes", "y=no"),
        ("policy=exact-match", "policy=clipped-gap"),
        ("C=256", "C=25856"),
        ("theta=256", "theta=512"),
        ("cap=0", "cap=1"),
        ("sh=8", "sh=7"),
        ("sT=4", "sT=3"),
        ("F=24", "F=23"),
        ("above-clip=0", "above-clip=1"),
        ("stages=key,leaf,request", "stages=key,leaf"),
        ("dw=8", "dw=4"),
        ("leaf=0", "leaf=x"),
        ("s=16", "s="),
        ("least-round=128.00", "least-round=128"),
        (" build=", " extra=1 build="),
        ("bits=128 work=32", "work=32 bits=128"),
    ];
    for (from, to) in swaps {
        let out = edit_result(from, to);
        assert_ne!(out, accepted(), "{from}");
        assert_eq!(verdict(&out), Outcome::Error(Cause::Field), "{from} -> {to}");
    }
    let other_id = edit_result(&format!("id={ID}"), &format!("id={}", "0".repeat(64)));
    assert_eq!(verdict(&other_id), Outcome::Error(Cause::Field));
    let other_build = edit_result(&format!("build={BUILD}"), &format!("build={}", "c".repeat(64)));
    assert_eq!(verdict(&other_build), Outcome::Error(Cause::Field));
}

#[test]
fn work_must_match_what_was_decided() {
    let p = Pins { max_work: 0, ..pins() };
    assert_eq!(judge(&p, &statement(), Some(0), accepted().as_bytes()), Outcome::Error(Cause::Field));
    let fewer = accepted().replacen("in 38 rounds", "in 37 rounds", 1);
    assert_eq!(verdict(&fewer), Outcome::Error(Cause::Work));
    let other = accepted().replacen("without it 96.23 bits", "without it 96.24 bits", 1);
    assert_eq!(verdict(&other), Outcome::Error(Cause::Work));
    let none = accepted().replacen("proof of work: up to 32 bits counted in 38 rounds; with it the least round has 128.00 bits, without it 96.23 bits", "proof of work: none counted", 1);
    assert_eq!(verdict(&none), Outcome::Error(Cause::Work));
}

#[test]
fn closing_lines_must_be_exact() {
    let yes_space = accepted().replacen("\nYES\n", "\nYES \n", 1);
    assert_eq!(verdict(&yes_space), Outcome::Error(Cause::Verdict));
    let no = accepted().replacen("\nYES\n", "\nNO: proof-refused\n", 1);
    assert_eq!(verdict(&no), Outcome::Error(Cause::Verdict));
    let within = accepted().replacen("\nYES\n", "\nWITHIN LIMITS: clipped-gap C=25856 theta=512 cap=1 s=16 sh=8\n", 1);
    assert_eq!(verdict(&within), Outcome::Error(Cause::Verdict));
    let line = accepted().lines().nth(5).unwrap().to_string();
    assert!(line.starts_with("result-v1 "));
    let twice = accepted().replacen("---- the call ----\n", &format!("---- the call ----\n{line}\n"), 1);
    assert_eq!(verdict(&twice), Outcome::Error(Cause::ResultLine));
    let moved = accepted().replacen("\nYES\n", "\nYES\nYES\n", 1);
    assert_eq!(verdict(&moved), Outcome::Error(Cause::ResultLine));
    let clause = accepted().replacen(", taken as given\n", "\n", 1);
    assert_eq!(verdict(&clause), Outcome::Error(Cause::Commitment));
    let test_build = format!("TEST BUILD: this program is a test build\n{}", accepted());
    assert_eq!(verdict(&test_build), Outcome::Error(Cause::TestBuild));
    let crlf = accepted().replace('\n', "\r\n");
    assert_ne!(verdict(&crlf), Outcome::Accept);
    assert_eq!(judge(&pins(), &statement(), Some(0), b"\xff\xfe"), Outcome::Error(Cause::Text));
    assert_eq!(verdict("YES\n"), Outcome::Error(Cause::Text));
}

#[test]
fn not_checked_in_the_hiding_line_is_not_a_failure() {
    assert!(accepted().contains("NOT CHECKED"));
    assert_eq!(verdict(&accepted()), Outcome::Accept);
}

#[test]
fn a_statement_for_another_request_is_refused() {
    let p = Pins { prompt_len: 10, reply_len: 4, ..pins() };
    let short = build(&p, &[1; 10], &[2, 3, 4, 5]).unwrap();
    assert_eq!(judge(&pins(), &short, Some(0), accepted().as_bytes()), Outcome::Error(Cause::Field));
}
