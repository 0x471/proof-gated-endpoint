use pge_wire::tokens::*;
use pge_wire::{Message, Reply, Wire, MAX_ID};

fn next(s: &mut u64) -> u64 {
    *s ^= *s << 13;
    *s ^= *s >> 7;
    *s ^= *s << 17;
    *s
}

// The shape of FlashProve's stored small proof: 61 prompt ids, 4 continuation ids.
fn wire() -> TokenWire {
    TokenWire::new(61, 4, 1 << 23).unwrap()
}

#[test]
fn round_trips_and_sizes() {
    let w = wire();
    assert_eq!((w.request_size, w.answer_fixed, w.reply_size), (9 + 4 * 61, 9 + 4 * 4, 10 + 4 * 4));
    let messages = [
        TokenMessage::Request(1, (0..61).collect()),
        TokenMessage::Forward(2, vec![u32::MAX; 61]),
        TokenMessage::Answer(3, vec![336, 473, 346, 199], vec![7; 4096]),
        TokenMessage::Reply(4, TokenReply::Ok(vec![336, 473, 346, 199])),
        TokenMessage::Reply(5, TokenReply::BadRequest),
        TokenMessage::Reply(6, TokenReply::Unavailable),
    ];
    for m in messages {
        let raw = w.encode(&m).unwrap();
        assert_eq!(w.parse(raw[0], &raw).unwrap(), m);
    }
}

#[test]
fn refused_or_exactly_canonical_for_seeded_random_and_mutated_inputs() {
    let w = TokenWire::new(3, 2, 64).unwrap();
    let bases = [
        TokenMessage::Request(7, vec![1, 2, 3]),
        TokenMessage::Forward(8, vec![0, u32::MAX, 5]),
        TokenMessage::Answer(9, vec![4, 5], vec![42; 32]),
        TokenMessage::Reply(10, TokenReply::Ok(vec![4, 5])),
        TokenMessage::Reply(11, TokenReply::Unavailable),
    ];
    let mut rng = 20261111;
    for kind in REQUEST..=REPLY {
        let mut accepted = 0;
        for i in 0..15000 {
            let mut raw: Vec<u8> = if i < 3000 {
                let n = (next(&mut rng) % 120) as usize;
                (0..n).map(|_| next(&mut rng) as u8).collect()
            } else {
                w.encode(&bases[(next(&mut rng) % 5) as usize]).unwrap()
            };
            for _ in 0..(next(&mut rng) % 4) {
                let pos = (next(&mut rng) as usize) % (raw.len() + 1);
                match next(&mut rng) % 3 {
                    0 if pos < raw.len() => raw[pos] ^= 1 << (next(&mut rng) % 8),
                    1 => raw.insert(pos, next(&mut rng) as u8),
                    _ if pos < raw.len() => {
                        raw.remove(pos);
                    }
                    _ => (),
                }
            }
            if let Ok(message) = w.parse(kind, &raw) {
                accepted += 1;
                assert_eq!(w.encode(&message).unwrap(), raw);
            }
        }
        assert!(accepted > 0, "kind {kind}");
    }
}

#[test]
fn sizes_counts_and_ids_are_enforced() {
    let w = wire();
    assert!(w.encode(&TokenMessage::Request(1, vec![0; 60])).is_err());
    assert!(w.encode(&TokenMessage::Request(1, vec![0; 62])).is_err());
    assert!(w.encode(&TokenMessage::Answer(1, vec![0; 3], vec![1])).is_err());
    assert!(w.encode(&TokenMessage::Reply(1, TokenReply::Ok(vec![0; 5]))).is_err());
    assert!(w.encode(&TokenMessage::Request(MAX_ID, vec![0; 61])).is_err());
    let raw = w.encode(&TokenMessage::Request(MAX_ID - 1, vec![0; 61])).unwrap();
    assert!(w.parse(REQUEST, &raw).is_ok());
    let mut too_big = raw.clone();
    too_big[1..9].copy_from_slice(&MAX_ID.to_be_bytes());
    assert!(w.parse(REQUEST, &too_big).is_err());
    assert!(w.parse(FORWARD, &raw).is_err());
    assert!(w.parse(REQUEST, &raw[..raw.len() - 1]).is_err());
    let mut longer = raw.clone();
    longer.push(0);
    assert!(w.parse(REQUEST, &longer).is_err());
}

#[test]
fn proofs_are_bounded_and_never_empty() {
    let w = TokenWire::new(2, 1, 100).unwrap();
    let ids = vec![9];
    assert!(w.encode(&TokenMessage::Answer(1, ids.clone(), vec![])).is_err());
    assert!(w.encode(&TokenMessage::Answer(1, ids.clone(), vec![0; 101])).is_err());
    let raw = w.encode(&TokenMessage::Answer(1, ids.clone(), vec![0; 100])).unwrap();
    assert_eq!(raw.len(), w.answer_max);
    assert!(w.parse(ANSWER, &raw).is_ok());
    let mut over = raw.clone();
    over.push(0);
    assert!(w.parse(ANSWER, &over).is_err());
    assert!(w.parse(ANSWER, &raw[..w.answer_fixed]).is_err());
}

#[test]
fn error_replies_have_one_form() {
    let w = wire();
    for status in [TokenReply::BadRequest, TokenReply::Unavailable] {
        let raw = w.encode(&TokenMessage::Reply(3, status.clone())).unwrap();
        assert_eq!(raw.len(), w.reply_size);
        assert!(raw[10..].iter().all(|b| *b == 0));
        let mut dirty = raw.clone();
        *dirty.last_mut().unwrap() = 1;
        assert!(w.parse(REPLY, &dirty).is_err());
    }
    let mut bad_status = w.encode(&TokenMessage::Reply(3, TokenReply::Unavailable)).unwrap();
    bad_status[9] = 3;
    assert!(w.parse(REPLY, &bad_status).is_err());
}

#[test]
fn shapes_that_cannot_be_framed_are_refused() {
    assert!(TokenWire::new(0, 4, 10).is_err());
    assert!(TokenWire::new(4, 0, 10).is_err());
    assert!(TokenWire::new(4, 4, 0).is_err());
    assert!(TokenWire::new(4, 4, PROOF_LIMIT + 1).is_err());
    assert!(TokenWire::new(usize::MAX / 2, 4, 10).is_err());
    assert!(TokenWire::new(4, 4, PROOF_LIMIT).is_ok());
}

#[test]
fn token_and_ezkl_frames_never_cross() {
    let t = TokenWire::new(1, 1, 64).unwrap();
    let e = Wire::new(1, 1).unwrap();
    let token_frames = [
        t.encode(&TokenMessage::Request(1, vec![5])).unwrap(),
        t.encode(&TokenMessage::Answer(1, vec![5], vec![1; 8])).unwrap(),
        t.encode(&TokenMessage::Reply(1, TokenReply::Unavailable)).unwrap(),
    ];
    for raw in &token_frames {
        for kind in 1..=4 {
            assert!(e.parse(kind, raw).is_err());
        }
    }
    let ezkl_frames = [
        e.encode(&Message::Request(1, vec![0.5])).unwrap(),
        e.encode(&Message::Answer(1, vec![[0; 32]], vec![1; 8])).unwrap(),
        e.encode(&Message::Reply(1, Reply::Unavailable)).unwrap(),
    ];
    for raw in &ezkl_frames {
        for kind in REQUEST..=REPLY {
            assert!(t.parse(kind, raw).is_err());
        }
    }
}

#[test]
fn shared_vectors_with_the_python_side() {
    // tests/test_tokens.py checks the same bytes against pge/tokens.py.
    let hex = |b: &[u8]| b.iter().map(|x| format!("{x:02x}")).collect::<String>();
    let t = TokenWire::new(2, 1, 16).unwrap();
    let cases = [
        (TokenMessage::Request(7, vec![1, 0x0102_0304]), "0500000000000000070000000101020304"),
        (TokenMessage::Answer(7, vec![511], vec![0xab, 0xcd]), "070000000000000007000001ffabcd"),
        (TokenMessage::Reply(7, TokenReply::Ok(vec![511])), "08000000000000000700000001ff"),
        (TokenMessage::Reply(7, TokenReply::Unavailable), "0800000000000000070200000000"),
        (TokenMessage::Reply(7, TokenReply::BadRequest), "0800000000000000070100000000"),
    ];
    for (message, want) in cases {
        let raw = t.encode(&message).unwrap();
        assert_eq!(hex(&raw), want);
        assert_eq!(t.parse(raw[0], &raw).unwrap(), message);
    }
    assert_eq!((t.request_size, t.answer_fixed, t.answer_max, t.reply_size), (17, 13, 29, 14));
}
