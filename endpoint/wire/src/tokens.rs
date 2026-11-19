//! Token-id messages for the FlashProve backend (docs/FLASHPROVE_BINDING.md). They use their own kind
//! bytes, so a token frame never parses as an EZKL frame and an EZKL frame never parses as a token one.

use crate::{Invalid, MAX_ID};

pub const REQUEST: u8 = 5;
pub const FORWARD: u8 = 6;
pub const ANSWER: u8 = 7;
pub const REPLY: u8 = 8;
pub const PROOF_LIMIT: usize = 1 << 26;

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum TokenMessage {
    Request(u64, Vec<u32>),
    Forward(u64, Vec<u32>),
    Answer(u64, Vec<u32>, Vec<u8>),
    Reply(u64, TokenReply),
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum TokenReply {
    Ok(Vec<u32>),
    BadRequest,
    Unavailable,
}

#[derive(Debug, Clone, Copy)]
pub struct TokenWire {
    prompt: usize,
    reply: usize,
    proof_max: usize,
    pub request_size: usize,
    pub answer_fixed: usize,
    pub answer_max: usize,
    pub reply_size: usize,
}

impl TokenWire {
    pub fn new(prompt: usize, reply: usize, proof_max: usize) -> Result<Self, Invalid> {
        if prompt == 0 || reply == 0 || proof_max == 0 || proof_max > PROOF_LIMIT {
            return Err(Invalid);
        }
        let request_size = prompt.checked_mul(4).and_then(|n| n.checked_add(9)).ok_or(Invalid)?;
        let answer_fixed = reply.checked_mul(4).and_then(|n| n.checked_add(9)).ok_or(Invalid)?;
        let answer_max = answer_fixed.checked_add(proof_max).ok_or(Invalid)?;
        let reply_size = reply.checked_mul(4).and_then(|n| n.checked_add(10)).ok_or(Invalid)?;
        for n in [request_size, answer_max, reply_size] {
            u32::try_from(n).map_err(|_| Invalid)?;
        }
        Ok(Self { prompt, reply, proof_max, request_size, answer_fixed, answer_max, reply_size })
    }

    pub fn parse(&self, kind: u8, raw: &[u8]) -> Result<TokenMessage, Invalid> {
        if raw.first() != Some(&kind) {
            return Err(Invalid);
        }
        let id = u64::from_be_bytes(raw.get(1..9).ok_or(Invalid)?.try_into().map_err(|_| Invalid)?);
        if id >= MAX_ID {
            return Err(Invalid);
        }
        let message = match kind {
            REQUEST | FORWARD if raw.len() == self.request_size => {
                let ids = words(&raw[9..]);
                if kind == REQUEST {
                    TokenMessage::Request(id, ids)
                } else {
                    TokenMessage::Forward(id, ids)
                }
            }
            ANSWER if raw.len() > self.answer_fixed && raw.len() <= self.answer_max => {
                TokenMessage::Answer(id, words(&raw[9..self.answer_fixed]), raw[self.answer_fixed..].to_vec())
            }
            REPLY if raw.len() == self.reply_size => {
                let reply = match raw[9] {
                    0 => TokenReply::Ok(words(&raw[10..])),
                    1 if raw[10..].iter().all(|b| *b == 0) => TokenReply::BadRequest,
                    2 if raw[10..].iter().all(|b| *b == 0) => TokenReply::Unavailable,
                    _ => return Err(Invalid),
                };
                TokenMessage::Reply(id, reply)
            }
            _ => return Err(Invalid),
        };
        if self.encode(&message)? != raw {
            return Err(Invalid);
        }
        Ok(message)
    }

    pub fn encode(&self, message: &TokenMessage) -> Result<Vec<u8>, Invalid> {
        let (kind, id) = match message {
            TokenMessage::Request(id, _) => (REQUEST, id),
            TokenMessage::Forward(id, _) => (FORWARD, id),
            TokenMessage::Answer(id, _, _) => (ANSWER, id),
            TokenMessage::Reply(id, _) => (REPLY, id),
        };
        if *id >= MAX_ID {
            return Err(Invalid);
        }
        let mut out = vec![kind];
        out.extend(id.to_be_bytes());
        match message {
            TokenMessage::Request(_, ids) | TokenMessage::Forward(_, ids) => put_words(&mut out, ids, self.prompt)?,
            TokenMessage::Answer(_, ids, proof) => {
                if proof.is_empty() || proof.len() > self.proof_max {
                    return Err(Invalid);
                }
                put_words(&mut out, ids, self.reply)?;
                out.extend(proof);
            }
            TokenMessage::Reply(_, TokenReply::Ok(ids)) => {
                out.push(0);
                put_words(&mut out, ids, self.reply)?;
            }
            TokenMessage::Reply(_, status) => {
                out.push(if *status == TokenReply::BadRequest { 1 } else { 2 });
                out.resize(self.reply_size, 0);
            }
        }
        Ok(out)
    }
}

fn words(raw: &[u8]) -> Vec<u32> {
    raw.chunks_exact(4).map(|b| u32::from_be_bytes([b[0], b[1], b[2], b[3]])).collect()
}

fn put_words(out: &mut Vec<u8>, ids: &[u32], n: usize) -> Result<(), Invalid> {
    if ids.len() != n {
        return Err(Invalid);
    }
    for id in ids {
        out.extend(id.to_be_bytes());
    }
    Ok(())
}
