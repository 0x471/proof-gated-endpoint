# Order

> **In short**
> - With several questions open, the server can choose which to answer first.
> - For admitted questions, the endpoint writes replies in admission order.
> - This removes the server's choice of write order; timing and immediate refusals need separate controls.
> - With FlashProve's slower checks, answer order can still choose which request runs out of time: order turned into failures.

## How the attack works

The accomplice sends three questions at the same moment: A, B and C. The server can answer them in six possible orders. Both sides share a table that uses four of them:

| Order the replies arrive | Means |
|---|---|
| A, B, C | 00 |
| A, C, B | 01 |
| B, A, C | 10 |
| B, C, A | 11 |

To send `10`, the server holds all three answers, then sends B first, then A, then C. Every answer has a valid proof. Only the order is chosen.

With k open questions there are k! orders, where k! means k x (k - 1) x ... x 1. Their information is log2(k!) bits: log2 measures how many binary choices distinguish the possibilities. Our test server uses the whole-bit part: 2 bits for 3 questions, 4 bits for 4.

## The defence: arrival order

Under arrival-order or fixed release, the endpoint does not pass an answer on just because its proof checked. It numbers admitted questions, meaning those accepted for processing, and holds checked answers. A reply may leave only when it is the oldest one still waiting. If the oldest question has no answer yet, nothing behind it leaves.

So if the server answers B, then A, then C, the endpoint writes A, then B, then C. The same rule holds for failures: an `unavailable` for a rejected answer also waits its turn. Otherwise, its position could reveal the server's chosen order.

Requests refused before admission do not wait. These include `bad_request` for invalid inputs and `unavailable` when the queue is full. After a halt, new questions also get `unavailable` immediately; see [Failures](failures.md#capping-versus-stopping).

## What we found

Without the defence, the order code got every planted bit through. With arrival order, and with fixed release, the decoded bits stayed constant whatever the server planted (the matrix on the [overview](../leakage.md#the-channels-at-a-glance)). Across separate connections, one short run showed the same.

## What it costs, and what is left

- **Head-of-line delay.** A slow or silent answer holds up every reply behind it until the deadline produces `unavailable`. Sending can still be late.
- **Many connections** were tested in one short run only. The endpoint controls write order, but separate connections and reader scheduling can change the order observed outside.
- **One queue for everyone** means one slow question delays all users. Ordering per user would avoid that, but the server could then choose the order across users. This trade-off is still open.

The state-machine tests exercise the admission and release rules with different answer orders. They check the endpoint's decisions, not every possible network observation.

## With slower checks: order turned into failures

Admission order controls the order replies leave. It doesn't control the order answers are checked. The endpoint has one checker, and it takes answers in the order they arrive. With the EZKL toy model a check used a small part of the release budget, so every waiting answer was checked in time, whatever the order. A FlashProve check takes a large part of the deadline ([Measurements](../results.md#does-the-endpoint-work-with-a-large-proof-verifier)), and that opens a new channel.

The accomplice sends two questions together. The deadline fits one check, not two. The server sends both valid answers, the one it wants to keep first. The checker starts on it; the other answer waits, its check can't finish before its deadline, and that request gets `unavailable`. Which of the two failed is one bit. Every proof is valid, and the replies still leave in admission order.

A registered test on the Pi (protocol 10):

{{#include ../results.md:protocol-10}}

So the reply-order invariant held, and the order code on its own wasn't detected. But order fed into failures. What helps, and what it costs:

- **A deadline that fits every waiting check.** The control with a longer deadline gave nothing to read. The general form is a start-up rule: refuse to start unless max_pending x C + A <= D, where C is a time limit per check, A the time allowed for an answer to arrive and D the deadline. An answer that arrives in time then waits behind at most max_pending - 1 checks of at most C each, whatever the server does with the others. This is a design, not a tested defence: the endpoint has no per-check limit yet, and the rule hasn't been tested against a server that withholds, delays or spoils answers. Its cost is fewer questions in flight or a longer deadline.
- **One pending question** (`--max-pending 1`) leaves no choice, but the second of two simultaneous questions is turned away as `busy`.
- **The failure cap** slows the channel but doesn't stop it, and the receiver can see which pairs it erased.
- **Checking strictly in admission order doesn't stop it.** The server withholds the oldest answer: that request fails, and the wait also fails the one behind it. It does take away the free lever, since the server must now spoil or delay an answer, and those failures count against the cap.

Neither the order tests nor the failure tests found this on their own. It appeared when the proof system changed: fast checks had hidden it.
