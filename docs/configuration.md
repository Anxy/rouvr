# Configuration

Copy `.env.example` to `.env`. Secrets are read from the environment only; never commit `.env`.

| Variable | Default | Purpose |
|---|---|---|
| `ROUVR_MODEL_PROVIDER` | `stub` | `stub` (offline), `meta` (Meta Model API), `ollama` (local open weights) |
| `META_API_KEY` | – | Key for the Meta Model API when provider is `meta` |
| `OLLAMA_MODEL` | – | Model name for Ollama, e.g. `llama3.1` |
| `HYEVE_AIX_SERVICE_*` | see `.env.example` | Core service settings (database, Redis, Temporal) |
| `RECEIPT_SIGNING_KEY_PATH` | `/srv/rouvr/keys/receipt_ed25519.pem` | Ed25519 private key (PEM, PKCS8, no encryption) for signed receipts; file must be `0600` |
| `RECEIPT_KEY_ID` | `rouvr-receipt-1` | Key identifier carried in every receipt envelope (`key_id`) |
| `ROUVR_LAUNCH_WINDOWS` | `0` | `0` = headless `muse exec` for coder/reviewer; `1` = interactive Muse chat windows |
| `WORKSPACE_ROOT` | `.data/workspaces` | Task checkouts; per-task git grants live at `WORKSPACE_ROOT/../grants/.git-grant-<id>.json` |
| `SPEC_ROOT` | `.data/specs` | Exec specs (`<tenant>/<project>.yaml`); UI `PUT /v1/projects/{project}/exec-spec` writes here |

`PYTHONPATH` must include `.:core:core/src:core/libs`; the Makefile sets it. See `.env.example` for the full list.

## Shared volumes (coding delivery from the web)

Ledger `POST /v1/tasks/{id}/git-grant` and Temporal workers must see the **same**
`WORKSPACE_ROOT` parent (so `grants/` resolves identically). With `./scripts/rouvr`,
both use `.data/workspaces` and `.data/grants` on the host. If the ledger runs in
Docker, bind-mount that host `.data` directory read-write into the ledger and
worker containers at the same path. Git-grant refuses with a clear error when
the grants directory is not writable.

## Receipt signing key (S010)

Task close returns a signed receipt `{digest, signature, key_id}`. The
digest covers the same 11 links as the former unsigned digest; the Ed25519
signature covers the digest bytes. Verifiers check the signature against
the published public key for that `key_id`.

Generate a key (0600 is enforced — signing refuses a looser file before use):

```bash
python3 - <<'PY'
from pathlib import Path
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

key = Ed25519PrivateKey.generate()
priv = Path("/srv/rouvr/keys/receipt_ed25519.pem")
priv.parent.mkdir(parents=True, exist_ok=True)
priv.write_bytes(key.private_bytes(serialization.Encoding.PEM,
                                   serialization.PrivateFormat.PKCS8,
                                   serialization.NoEncryption()))
priv.chmod(0o600)
# Publish this for verifiers (PEM, SubjectPublicKeyInfo):
print(key.public_key().public_bytes(serialization.Encoding.PEM,
                                    serialization.PublicFormat.SubjectPublicKeyInfo).decode())
PY
```

Set `RECEIPT_SIGNING_KEY_PATH` to the private-key path and `RECEIPT_KEY_ID`
to its identifier, then restart the workers (`rouvr.worker`). The private
key is never logged or placed in evidence; only `digest`, `signature` and
`key_id` leave the signer.

### Rotation

1. Generate a new Ed25519 key at a new path (still `0600`).
2. Bump `RECEIPT_KEY_ID` (for example `rouvr-receipt-2`), point
   `RECEIPT_SIGNING_KEY_PATH` at the new file, and restart the workers.
   New receipts now carry the new `key_id`.
3. Publish the new public key alongside the old one. Verifiers keep a
   `{key_id: public key}` pool (`rouvr.receipts.verify_receipt` accepts
   either a single key or the pool), so receipts signed under the old
   `key_id` keep verifying (AC-4).
4. Retire the old `key_id` from the pool only after its receipts age out;
   dropping it makes those receipts fail closed (explicit, not silent).
