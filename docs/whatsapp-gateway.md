# WhatsApp gateway: source-only integration

`personal_ai_brain.whatsapp_gateway` supplies a bounded Meta webhook and delivery adapter; it starts no server, scheduler or network connection. The operator must provide protected configuration, a private state directory on durable local storage, one authenticated API callable for `PrivateControlIssues`, and an explicitly supplied Meta HTTP callable. The HTTP implementations must reject redirects, bound responses and raise on errors. The existing private control worker remains the sole execution authority; the gateway stores only a message-ID hash, request correlation and delivery state, never message text.

Call `verify_challenge` for GET and `Gateway.receive` with the exact raw POST bytes and signature; HTTP 200 follows durable reservation of all eligible records and never implies final delivery. Schedule the bounded `Gateway.work` worker to submit accepted receipts and fetch authenticated results without holding the ingress lock across external requests. Legacy `post`/`poll` remain available for offline backwards compatibility; do not mix multiple independent worker instances. Configure a single owner for the state directory, durable filesystem and a single trusted sender, WABA and recipient phone; protect the directory and secrets from other principals. The issue author and result author are checked against the configured numeric owner ID. Both the private repository and exact control submission are checked before accepting a result. Only the fixed `homelab` status selector is supported; message text cannot select a capability or target.

A `sent` record means the Meta API returned a message ID, not that the recipient received it. Interrupted or ambiguous issue creation and send attempts are `uncertain` and are never automatically repeated. A `pending` control request can be polled again without repeating execution. A crashed send reservation is treated as uncertain even if the provider may not have received it; operator reconciliation is required. State loss, manual file modification, shared network filesystems and multiple independent state directories are outside the guarantee. Status-only Meta callbacks do not establish end-user delivery in this slice.

The offline tests use fake Meta and control clients; no production activation, public ingress, real Meta calls or credentials are included. A separately reviewed deployment and live acceptance remain necessary.

## G3.7 ngrok Free HTTPS ingress — source-only, not activated

The operator's ngrok Free account has the dashboard-assigned **development** hostname
`disperser-greedless-shifty.ngrok-free.dev`. Target Meta callback:
`https://disperser-greedless-shifty.ngrok-free.dev/webhook`.
This is a reserved account hostname, **not a running endpoint** and not evidence of
actual Meta callback reachability. This dev-domain option replaces the earlier
Cloudflare-domain requirement for the first acceptance test; no separate purchased
domain is required.

A reviewed **candidate** edge policy is kept in
[`config/ngrok-whatsapp-traffic-policy.yml`](../config/ngrok-whatsapp-traffic-policy.yml):
deny all HTTP methods/paths except exact `GET /webhook` and `POST /webhook` at the
ngrok edge, with HTTP 404. An allowed request passes unchanged to the already
loopback-bound Gateway (GET challenge including query string; POST signature and
original body). It does **not** expose `/health`, any control-worker endpoint, SSH,
Docker API or another private network path. Do not add OAuth, browser login,
Basic Auth or request-body modifications to the webhook; Meta must invoke it
noninteractively. Gateway retains the definitive Meta token/HMAC and trusted
sender/recipient checks. Edge policy is defense in depth, **not** provider
authentication.

**Required checks before activation (not completed):**

1. Operator separately authorizes the new public HTTPS endpoint and a narrowly
   scoped ngrok agent/credential. Confirm the target Ubuntu distribution,
   approved signed ngrok installation method and agent version; do not blindly
   paste the dashboard's Debian Bookworm APT example. Use a native host agent
   with a dedicated protected config/credential readable only by its service
   identity; never paste the `authtoken` into GitHub, terminal history, CI or
   chat. The dashboard's `ngrok http 80` demo is **not** our Gateway port.
2. First meet the independently approved Gateway host deployment/recoverability
   gates from the runtime plan below (Infrastructure #33 is owned by another
   workstream). Confirm the Gateway's actual dedicated host-loopback port,
   immutable image, private state and health; previous candidate port `18881`
   is **not reserved or an authorization**. Keep the private control worker,
   mounts and ledger untouched.
3. With that one reviewed local port and source-controlled policy copied to
   an operator-owned absolute path, the **example only** for the approved
   operator is:

   ```sh
   ngrok http "http://127.0.0.1:${APPROVED_GATEWAY_PORT:?set-reviewed-port}" \
     --url https://disperser-greedless-shifty.ngrok-free.dev \
     --traffic-policy-file /absolute/approved/path/ngrok-whatsapp-traffic-policy.yml
   ```

   Do **not** run until public ingress and host changes are separately
   authorized. One dedicated tunnel; no router port forwarding or wildcard
   forwarding. Do not expose the agent's local inspector/administration API.
4. Verify in ngrok **Settings → Observability** that **Full Capture is OFF**:
   metadata-only collection is the vendor default, while Full Capture may
   retain query tokens, webhook bodies and headers. Keep raw body, Meta app
   secret, verify token and authorization headers out of logs, browser
   screenshots and replay tooling. The free tier's browser-warning
   interstitial may interfere with automated clients; test this **before**
   Meta subscription using synthetic no-secret GET/POST requests, without
   assuming Meta can send a bypass header.
5. In a separately approved synthetic test, verify:
   `/health`, `/`, `/webhook/other` and other methods do not reach Gateway
   (edge 404); unsigned/bad-signature `POST /webhook` is rejected by Gateway
   (403); GET challenge is forwarded without token loss and has the exact
   verified response; no unauthenticated external effect occurs. Verify edge
   policy via the installed agent's own validator/actual responses; the
   repository's offline YAML test **does not** validate ngrok CEL support,
   live limits, free-plan interstitial or body preservation. Only after this
   may Meta callback activation and an authorized real inbound/reply test
   be considered.

**Rollback:** disable only the ngrok endpoint/agent and verify the public
hostname is no longer routing; preserve the Gateway's private state,
request/delivery reservations and all unrelated services. Never automatically
resend uncertain effects. A failure of ingress testing is not permission to
expose the host port on `0.0.0.0` or relax Gateway authentication.

Free-tier restrictions should be rechecked before long-term reliance:
one assigned dev hostname, 20,000 HTTP requests/month, 1 GB egress/month,
and a finite free Traffic Policy rule allowance. The state backlog/retention
and Meta's finite retry horizon remain independent operational limitations.

Official vendor references:
[agent endpoints and Traffic Policy](https://ngrok.com/docs/gateway/traffic-policy/getting-started/agent-endpoints),
[public HTTPS agent endpoints](https://ngrok.com/docs/gateway/endpoints/http),
[Traffic Inspector privacy](https://ngrok.com/docs/obs/traffic-inspection/http-requests),
[Free limits](https://ngrok.com/docs/pricing-limits).

## Source-only runtime and operator plan

`personal_ai_brain.whatsapp_runtime` connects the adapter to a fixed-origin HTTPS GitHub client and Meta client, serves `GET/POST /webhook`, and runs the bounded single worker every 30 seconds. It rejects redirects, bounds external responses and suppresses request, token, payload and exception logging. `GET /health` is 200 only after a recent successful poll; this is process/poll health, not proof of provider delivery or absence of uncertain records. Shutdown stops polling; any interrupted outbound attempt retains the existing uncertain reservation.

The following is a **plan, not authorization to deploy**. Public ingress and real inbound acceptance require a separate explicit operational authorization. The existing `personal-ai-brain-control` Compose project, image, mounts and ledger must not be recreated or modified. Do not expose a public port or create a tunnel as part of this plan.

1. In an authorized maintenance window, record the actual running control service image ID, health, Compose configuration and ledger metadata without reading secrets. Verify a backup of its ledger and retain its accepted image. Select a private host directory for the new gateway state, owned by the dedicated numeric service UID/GID, mode 0700; choose a free loopback port. Verify the host filesystem supports durable atomic rename and `fsync` and that only one gateway instance can use that state. Preserve this directory across every restart and rollback.
2. At the reviewed merged source commit, build the existing Dockerfile as a separate image and capture its immutable local image ID; this does not retag or rebuild the control image:

   ```sh
   docker build -f docker/Dockerfile -t pab-whatsapp-candidate .
   gateway_image_id=$(docker image inspect pab-whatsapp-candidate --format '{{.Id}}')
   printf '%s\n' "$gateway_image_id"
   ```

   Pin `PAB_WHATSAPP_IMAGE` to that `sha256:...` ID in the gateway's private operator environment file, not a mutable tag.
3. Create a private `whatsapp.json` with only `owner_id` (numeric GitHub issue/result author ID), `user_id`, `waba_id`, `phone_id`, `recipient` (exact Meta display phone string), `sender` (authorized digits), and `api_version` (for example `v22.0`). Create or reference four separately protected files for the existing narrowly scoped control Issues token, Meta access token, Meta app secret and webhook verify token. Only path names belong in the environment file; never place token values in Compose, Git, shell history or logs. Ensure the dedicated UID can read only those files and can write the state directory. Do not copy the control worker's ledger or its other secrets.
4. In a mode-0600 private environment file set `PAB_WHATSAPP_IMAGE`, `PAB_WHATSAPP_UID`, `PAB_WHATSAPP_GID`, `PAB_WHATSAPP_PORT`, `PAB_WHATSAPP_STATE`, `PAB_WHATSAPP_CONFIG`, `PAB_WHATSAPP_GITHUB_TOKEN_FILE`, `PAB_WHATSAPP_META_TOKEN_FILE`, `PAB_WHATSAPP_APP_SECRET_FILE`, and `PAB_WHATSAPP_VERIFY_TOKEN_FILE` to the reviewed image ID, numeric identity, loopback port and absolute paths. Validate without starting anything:

   ```sh
   docker image inspect "$PAB_WHATSAPP_IMAGE" --format '{{.Id}}'
   docker compose --env-file "$gateway_env" -f compose.whatsapp.yaml config --quiet
   ```

   Load the image ID from the protected environment file in the operator shell before `image inspect`; do not print secret contents. Review `docker compose config` privately if needed: it must show loopback-only port binding, the new private state path, non-root UID, read-only root and no control ledger mount.
5. Only after a separate deployment authorization, run `docker compose --env-file "$gateway_env" -f compose.whatsapp.yaml up -d --no-build --pull never --wait gateway`. Check `docker compose --env-file "$gateway_env" -f compose.whatsapp.yaml ps`, local `GET /health`, and the unchanged control service image/health/ledger metadata. No inbound test reaches Meta until separately authorized ingress exists. Do not infer provider delivery from `sent`.
6. **Rollback:** run `docker compose --env-file "$gateway_env" -f compose.whatsapp.yaml stop gateway`. Preserve the gateway state directory and all uncertain reservations; never use `down -v`, delete state, or resend uncertain messages automatically. If returning to an earlier gateway image, verify that it understands the existing state format before changing only `PAB_WHATSAPP_IMAGE` and restarting the same isolated service. If no compatible prior gateway exists, leave it stopped. Verify the control service and ledger are unchanged. Reconcile any uncertain GitHub or Meta operation manually before resuming.

### Webhook batch boundary

The production HTTP handler verifies HMAC over exact raw bytes, then calls `Gateway.receive` to persist authenticated eligible text events before sending HTTP 200. It accepts at most **3 MiB per POST** (covering Meta's documented 3 MB) and at most **1,000 aggregate updates**, counted across message/status lists or individual changes. The legacy direct `Gateway.post` entrypoint keeps its original 64 KiB/100-per-list boundary and synchronous semantics for source consumers; it is **not** used by the HTTP runtime.

Mixed signed batches ignore messages for unrelated senders/recipients and unsupported types. Structurally invalid or tampered envelopes are rejected, without processing any external effects. Received events use stable UUID/hash/binding reservations and replay deduplication; the single bounded worker reserves `submitting` before GitHub issue creation and `sending` before Meta reply. The worker's external I/O does not hold the ingress-state lock. An uncertain request/send remains recorded and is never retried automatically.

Ingest has a 10,000-artifact/16 MiB shared durable state ceiling and **does not delete records automatically**. At exhausted capacity, invalid state, lock contention or disk/fsync failure, the server returns non-2xx instead of acknowledging an unpersisted eligible event. The capacity limit is not a throughput or zero-loss guarantee: Meta's retries last up to seven days, and a prolonged outage or full disk may outlast them. Completed records are kept at least 14 days (currently indefinitely), while uncertain ones require operator reconciliation. Retention and capacity maintenance must be separately reviewed before indefinite operations. The worker is bounded to at most eight records per 30-second tick, with a fair rotating scan, and may lag during bursts or slow backend operations. The existing control request freshness window can expire before an excessively delayed worker reaches an event; such an outcome must be monitored, not silently treated as completed.

References: [Meta webhook overview](https://developers.facebook.com/documentation/business-messaging/whatsapp/webhooks/overview) and [Meta webhook endpoint](https://developers.facebook.com/documentation/business-messaging/whatsapp/webhooks/create-webhook-endpoint/). SOURCE-ONLY; provider interoperability and production delivery are not established until independently accepted live tests.

### G3.6A offline durable-ingest foundation

`Gateway.ingest` accepts a bounded list of event tuples only from a caller that has already authenticated the raw webhook and trusted sender/recipient. It revalidates each request, preflights conflicts and capacity, and reserves stable UUID/hash/binding in the existing per-message files with control state `received`. It makes no external calls. Legacy direct `post`/`poll` defer a `received` record without executing it, while the current HTTP runtime now calls `Gateway.receive` and the bounded `Gateway.work` worker.

Ingest accepts at most 1,000 event tuples per call and caps the shared state inventory at 10,000 record/temporary-file artifacts and 16 MiB of file contents. Existing and orphaned temporary files count toward capacity. These are ingest-only limits: the unchanged legacy synchronous path does not enforce them. The operation checks all existing records and fails closed on unknown, foreign or corrupt state; partial writes are safe to submit again. It fsyncs each record and, on POSIX, its parent directory including duplicate-only retries. The state directory must already reside on a durable filesystem; Windows directory fsync is not implemented. There is no automatic deletion: completed and uncertain records are retained indefinitely, satisfying the minimum 14-day retention at the cost of eventually requiring operator capacity management. Source support for 1,000-update/3 MiB webhook input and the received-event executor was added subsequently; production acceptance still requires an authorized live test and recovery gate.
