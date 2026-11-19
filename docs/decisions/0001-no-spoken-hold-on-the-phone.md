# 1. The phone does not announce sends; the voice's read-back and the undo delay do

Date: 19.11.2026. Status: decided by the owner.

## What was asked
The design (messages-v1 §8) offered one guard outside the language model. Before a
message bridged from this laptop goes, the phone itself would speak in the earbuds. It
would say "Sending to <name> in 10 seconds", with Android's own text-to-speech. A model steered by a
stranger's message could not silence that voice. To fail closed, the delay would start
only once the phone confirmed it had begun speaking, within 3 seconds. Without that, the
send would be refused. This was build slice M10.

## The decision
It is dropped. M10 is not built.

## Why
Sending must work without the phone. The voice reads the draft back before it can be
confirmed, and every confirmed message waits 10 seconds, so there is time to say "stop".
The owner judged that to be enough.

## What it costs
The read-back and the "sending in 10 seconds" line both come from the model. A model
steered by a stranger's message could create a draft and confirm it without saying
either. What still holds in code:
- A confirm must name the version that was read back.
- A draft goes only to a one-to-one chat the owner has written in before.
- Text with a link, a long run of digits or a hidden character is refused.
- At most 3 sends go to one chat in 10 minutes, 10 in an hour and 30 in a day.
- Every send is logged, without its text.
