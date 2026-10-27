# Demo: "Done. No action needed."

`demo/morning_report.py` compiles yesterday's lane report from the ledger, delivers it to a local outbox, verifies it independently, and closes the task with receipts.

| Script | Injected failure | Expected behaviour |
|---|---|---|
| `failure_injection/kill_midstep.sh` | Worker dies after delivery, before close | The rerun resumes, sends nothing twice (one outbox file), then `done` |
| `failure_injection/silent_delivery.sh` | The primary sink returns OK but delivers nothing | The verifier readback fails, the fallback sink is used, then `done` |
| `failure_injection/revoked_key.sh` | The model credential is unavailable | The task parks as `blocked` before claim, with an escalation evidence row |

These cases are covered by `demo/test_morning_report.py`. Delivery here is a file outbox, and the default model is the offline stub. Set `ROUVR_MODEL_PROVIDER=meta` and `META_MODEL_API_KEY` to use Llama.
