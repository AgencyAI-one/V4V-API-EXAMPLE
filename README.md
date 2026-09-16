# V4V API Tester

A local, dependency-free web console for exercising the [V4V Public API](https://v4v.ai/developers) by hand:
discover models, build a request from the model's own schema, upload media, submit LAB and Studio
generations, watch tasks, preview results, and verify signed callbacks.

Everything runs on your machine. Nothing is uploaded anywhere except the API you point it at.

```
python3 serve.py          →  http://127.0.0.1:4010
```

## Why a server and not just an HTML file

The API sends no CORS headers, so a page opened from `file://` (or hosted anywhere else) cannot call it
from the browser. `serve.py` serves the UI and forwards requests to the API base you choose, attaching
`Authorization: Bearer …` and `Idempotency-Key`. The upstream HTTP status is returned in the
`X-V4V-Status` header so the browser never swallows an error body.

It also gives you something a static page cannot have: a local endpoint that receives task callbacks
and checks their HMAC signature for you.

## Requirements

Python 3.8+. No packages, no build step, no lockfile — the standard library only.

## Usage

```sh
python3 serve.py                                    # UI on :4010
python3 serve.py --port 8080                        # another port
python3 serve.py --api https://staging.example/api/v1   # initial API base
V4V_INSECURE=1 python3 serve.py                     # accept self-signed TLS upstream
```

Open the URL it prints, then paste your **API base** and **API key** into the header bar. The base is a
plain editable field with presets, so one running instance can be pointed at production, staging or a
local build without a restart. The key is kept in the browser's `localStorage`; the server never writes
it to disk.

The server binds to `127.0.0.1` by default. `--host 0.0.0.0` exposes the tester — and with it a proxy
that will attach whatever API key the browser sends — to your whole network. Do that only on a network
you trust.

## What it covers

| Section | Endpoints and behaviour |
|---|---|
| Models | `GET /models`, `GET /models/{model}` — filter, enabled state, raw `input_schema` / `parameters_schema` |
| LAB | `POST /lab` — form generated from the selected model's own schemas (camelCase field names preserved), `project_name`, `Idempotency-Key`, raw-JSON tab |
| Studio | `POST /studio` — every documented input and parameter, with the ProVid/OmniVid limits shown inline |
| Files | `POST /files` (drag & drop, multipart), `GET /files/{id}`, a local library of uploaded ids with a 📎 button that inserts one into any media field |
| Tasks | `GET /tasks/{task_id}`, auto-polling every 5 s, inline preview of image/video/audio results, `credits_reserved` / `credits_used`, `expires_at` |
| Callbacks | a built-in receiver that verifies `X-V4V-Signature` and timestamp freshness |
| Account | `GET /balance`, `GET /usage` (rendered as a table) |
| Raw request | any method, path and body — for exercising 400/401/402/403/404/409/422/429 |
| Log | every call with status, timing and a ready-to-run `curl` |

Task state is driven entirely by `status`: `queued` and `processing` show a loading indicator,
`completed` renders `results`, `failed` shows `error`, and `cancelled` is terminal. Polling stops at
every terminal state.

## Verifying signed callbacks

1. In **Webhooks**, paste the webhook signing secret shown once when the API key was created, and save it.
2. Put the receiver URL (printed at startup, e.g. `http://127.0.0.1:4010/__hook`) into `callback_url`
   when submitting a generation.
3. Events appear with a `valid` / `INVALID signature` verdict.

The signature is `HMAC-SHA256(timestamp + "." + rawBody)` compared in constant time, with timestamps
older than 300 s rejected — the same check the docs prescribe for your own receiver.

This works only if the API server can reach your machine. For a hosted deployment, put a tunnel
(ngrok, cloudflared, …) in front of the port and use the tunnel's URL.

## Optional redaction

Some deployments echo an upstream vendor name in ids, hostnames or metadata that you would rather not
show to whoever is testing. `--redact` scrubs it from every response body — the model catalog, schemas,
task payloads, callbacks, the log and the generated `curl` alike:

```sh
python3 serve.py --redact acme                 # acme.foo → hidden.foo
python3 serve.py --redact acme --redact-as vendor
```

The substitution is reversible: request bodies and URL paths are un-scrubbed on the way out, so
submitting a masked id still hits the real one. Matching is word-bounded inside identifiers, so redacting
`book` never touches `cookbook`. Result URLs that contain a redacted word are streamed through a
local relay (with `Range` support) so the host never reaches the browser; all other URLs load directly.

Redaction is off unless you pass `--redact`.

## Endpoints the tester itself serves

| Path | Purpose |
|---|---|
| `/` | the UI |
| `/__api/*` | proxy to `<base>/*` |
| `/__hook` | callback receiver |
| `/__events` | received callbacks (`DELETE` clears) |
| `/__config` | API base, webhook secret, redaction state |
| `/__media?u=…` | relay for redacted result URLs |

## Notes from the API docs worth remembering

- A field goes in `input` **or** `parameters`, never both; unknown fields are rejected. Keep the exact
  names discovery returns, including camelCase ones like `aspectRatio` or `referenceImageUrls`.
- Media fields take an external HTTPS URL **or** a 24-character file id, and arrays may mix the two.
- `project_name` (1–200 characters, trimmed, non-blank) sets the project title. Changing it under the
  same `Idempotency-Key` is a conflict, exactly like changing the inputs.
- Default limits: 120 requests/min per key, 10 generation submissions/min and 3 concurrent generations
  per account. Rate-limited responses carry `Retry-After`.
- Result links are signed and expire; fetching the task again renews them.

## Contributing

Issues and pull requests are welcome. The whole tool is two files — `serve.py` (stdlib HTTP server)
and `index.html` (no framework, no build) — so a change is usually a small diff in one of them. Please
keep it dependency-free.

## License

MIT — see [LICENSE](LICENSE).

This is an independent tool. It is not an official V4V product and is not affiliated with or endorsed
by the operators of the API.
