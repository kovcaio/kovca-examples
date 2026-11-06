# Hold an AI decision until a person approves

A refund, an account change, or a security-looking ticket should not continue because a model was confident. The run needs to stop, show a person the case, and continue only after that person answers.

## Solution

Kovca's approval step does this inside a workflow. `wait:approval@1` pauses the run. It has four branches: `requested`, `approved`, `rejected`, and `expired`. Waits last up to 90 days. Waiting is free. The run still pays its run fee once, and steps before and after the wait are billed when they succeed. https://kovca.io/docs/approvals

The page publishes this graph: input, Support Triage, a condition on `requires_human_review`, an approval, and an HTTP action on the false branch and on `approved`. The copy in `references/approval-graph.documented-fragment.json` is that fragment.

It is not a file you can pass to `kovca workflows apply`. An apply file has the envelope `kovca`, `format`, `id`, `name`, and `definition`, and the public docs do not fully specify `definition`. The HTTP node in the fragment is the docs' own `"..."` placeholder. `conn_...` is not a connection id. Build the workflow in the Studio from the page, publish it, and point this example at that workflow id.

People answer in the first of these that arrives:

- The approval link. Anyone with the link can answer, so send it only to the approver. The link is not in the API response. The `requested` branch, or the step's notification settings, delivers it.
- The Approvals inbox for the workspace owner.
- An optional chat or email notification. A failed notification does not fail the run.
- The API or MCP, only when the step turns on **Allow API decisions**. That setting is off by default, so an agent cannot approve its own run. The API call needs a **Publish** key. Otherwise the response is `403 DECISIONS_NOT_ALLOWED`.

`review.py` is the caller.

- `submit` sends `{ "input": { "message": "..." } }`, which is the only field the documented input node declares.
- `status` reads `GET /v1/runs/{id}`. The body is metadata. Kovca does not store the input, the output, or the approval answers.
- `decide` sends `POST /v1/runs/{run_id}/waits/{step_id}/decision`. It reads `KOVCA_PUBLISH_API_KEY` and does not fall back to the Run key.

A `202` with `"status": "waiting"` means the run paused. `fixtures/sample-waiting-response.from-docs.json` is the waiting envelope from the approvals page, including the callback object that page shows for a callback step. An approval-only workflow may omit `callbacks`. The file uses synthetic ids. It is not a live response. The sample's resume URL is not a credential.

Studio tests and `test_workflow` do not wait. They end the approval immediately with the outcome you pick. A real pause only happens on a published run.

## Prerequisites and minimum scopes

- Python 3.11 or newer.
- A published workflow whose input is the string `message`, and which reaches `wait:approval@1` for the cases you want a person to see.
- `KOVCA_API_KEY` with **Run** access for `submit` and `status`.
- `KOVCA_PUBLISH_API_KEY` with **Publish** access for `decide`, and Allow API decisions turned on for that step. Leave the setting off if a person should be the only one who can answer. `decide` will then fail on purpose.
- The documented form field is `category`, one of `billing`, `technical`, `account`, `product`, `other`. `--no-category-check` turns that check off for a workflow whose form is different.

`list_waits` over MCP can list open approvals for a Run key, and it never returns the message or the answers. `decide_wait` is the MCP equivalent of `decide` and has the same Publish and Allow API decisions rules. See `guides/mcp/README.md`.

## Setup

```bash
python3 examples/ai-approval-workflows/review.py --help
export KOVCA_API_KEY="a Run key"
export KOVCA_WORKFLOW_ID="wf_..."
# only for decide:
export KOVCA_PUBLISH_API_KEY="a Publish key"
```

If your Studio workflow's input fields differ, change the fixture. Unknown fields are rejected with `400 INVALID_REQUEST`.

## Dry run

```bash
python3 examples/ai-approval-workflows/review.py submit \
  --fixture examples/ai-approval-workflows/fixtures/refund-request.json \
  --idempotency-key refund-1001
```

No network. The same output is checked in at `fixtures/dry-run.output.json`. The body is the synthetic refund sentence in the fixture. The idempotency key is `refund-1001`. Without `--idempotency-key` the dry run still prints, and it tells you that a live submit will be refused until you set one.

```bash
python3 examples/ai-approval-workflows/review.py decide \
  --run-id run_synthetic \
  --step review \
  --decision approved \
  --field category=billing
```

No network. The printed request is the decision call, with the publish key redacted. `--decision rejected` is the other allowed value.

```bash
python3 examples/ai-approval-workflows/review.py status --run-id run_synthetic
```

Prints the GET and does not send it.

## Expected results

A submit that reaches the approval returns `202`, `status` `waiting`, and a `Location` header. It does not include the approval URL. Follow the run with `status`, the Executions page, or the `run.waiting` outbound webhook. That webhook carries run metadata only, never the case text. https://kovca.io/docs/webhooks

When the person approves, the run continues on the published version it started on. If you need the workflow output, the actions after the wait have to perform it (a ticket update, a refund call). A caller that only saw the `202` does not later receive the output, because Kovca does not store outputs. `GET /v1/runs/{id}` will show `succeeded` or `failed` and the step list after the wait ends.

`decide` answers `202` and the run continues in the background. A second decision gets `409 WAIT_CLOSED`. The first answer wins.

## Live usage

Submit creates a real run and may pause it. It does not approve anything.

```bash
python3 examples/ai-approval-workflows/review.py submit \
  --fixture examples/ai-approval-workflows/fixtures/refund-request.json \
  --idempotency-key refund-1001 \
  --live --confirm-side-effects
```

Read it back:

```bash
python3 examples/ai-approval-workflows/review.py status \
  --run-id run_... \
  --live
```

`status --live` is a read. It does not continue the run. It still sends the Run key.

Decide continues the run, including whatever the approved or rejected branch does. The HTTP action in the documented graph is that side effect.

```bash
python3 examples/ai-approval-workflows/review.py decide \
  --run-id run_... \
  --step review \
  --decision approved \
  --field category=billing \
  --live --confirm-side-effects
```

Do not point `decide` at a run you do not intend to finish. There is no undo in this program.

## Failure, retry, and idempotency

- A live submit without `--idempotency-key` is refused. A timeout followed by a second submit would otherwise open a second waiting run.
- The same key and the same body within 24 hours return the original run and do not start another. The duplicate omits the output, so keep the first `202`.
- The same key and a different message return `409 IDEMPOTENCY_KEY_CONFLICT`. Use a new key for a new case.
- If the first run was `interrupted`, the key stays bound for 24 hours. Kovca does not resume it. A new key starts a new run. https://kovca.io/docs/runs
- This client retries `429 RATE_LIMITED`, `500`, `503`, and `504 TIMEOUT` on its own HTTP call. It does not retry usage-limit responses, payment-required responses, or `ACTION_TIMEOUT`.
- If continuing after the wait is refused for credits or rate limits, Kovca keeps the run waiting and tries later, backing off from a minute to an hour. That retry is Kovca's, not this program's.
- `403 DECISIONS_NOT_ALLOWED` means the step does not allow API decisions. Stop. Do not switch to a more powerful workaround. A person answers from the link or the inbox.
- `409 WAIT_CLOSED` means the approval was already answered, expired, or the run ended.
- `413 RUN_STATE_TOO_LARGE` means the data kept across the wait would exceed 1 MB. The run fails and nothing waits. Send a shorter message.
- `429 TOO_MANY_WAITING_RUNS` means the plan's waiting-run cap is full (25 on Free, 10,000 on Pay As You Go).

Workflows that contain a wait cannot be called by a Workflow node. The run continues on the version it started on. If the workflow is archived or turned off while it waits, the run fails when the wait ends. Cancel a waiting run from its page in Executions.

## Cost and side effects

From https://kovca.io/docs/billing and the approvals page. This repository has not executed a waiting run.

- An API-started run costs 1 credit. Waiting itself is free.
- Support Triage, if your workflow uses it, costs 5 credits when it succeeds.
- An HTTP action after the approval costs 1 credit when it runs for real. A connected-app action costs 2.
- A Run key cannot approve. A Publish key can, and only when the step allows it. That call continues the workflow.
- The case text is untrusted and is sent only as `message`. The documented approval message template interpolates it for the approver. It is not executed as code by this program.
- Do not enable Pay As You Go from this example. Nothing here changes billing.

The documented graph notifies Slack through a connection id you replace in the Studio. A live submit can cause that notification if your published workflow has it. Read the draft before the first live submit.

## Cleanup

- Leave the approval unanswered and it expires. The documented fragment sets two days. The product default when you do not set one is seven days. The `expired` branch runs if you connected it. The fragment does not connect `expired`.
- Cancel the run from Executions if you do not want it to sit until expiry.
- Revoke the Run key and the Publish key if they were created for the test.
- This program stores nothing. If you passed `--out`, that file is on your machine and contains the response metadata, not the API key.

## Troubleshooting

| Symptom | What to check |
| --- | --- |
| Live submit refused before any network call | `--confirm-side-effects` and `--idempotency-key` are both required. |
| `401 UNAUTHORIZED` | The key is a current workspace key. `kvt_` keys do not work. `decide` uses `KOVCA_PUBLISH_API_KEY`, not `KOVCA_API_KEY`. |
| `403 INSUFFICIENT_ACCESS` | `decide` was called with a Run or Author key. Create a Publish key. |
| `403 DECISIONS_NOT_ALLOWED` | Allow API decisions is off. That is the safe default. |
| `400 INVALID_REQUEST` | The workflow input is not a single `message` string, or `category` is not in the form's enum. |
| `404 NOT_FOUND` | The run id is wrong, or the run is older than 30 days. Waiting runs are kept until they finish and for 30 days after. |
| Status stays `waiting` | Nobody has answered, and the API decision was not accepted. The approval link is not in the execute response. |
| Output missing after the run succeeds | Expected. Kovca does not store outputs. The actions after the wait have to deliver the result. |

## Official docs

- Approvals and waits: https://kovca.io/docs/approvals
- Runs: https://kovca.io/docs/runs
- Workflows API: https://kovca.io/docs/workflows
- Idempotency: https://kovca.io/docs/idempotency
- Errors: https://kovca.io/docs/errors
- Billing: https://kovca.io/docs/billing
- API keys: https://kovca.io/docs/api-keys
- Outbound webhooks, including `run.waiting`: https://kovca.io/docs/webhooks
- Decision AI: https://kovca.io/docs/decision-ai
- MCP, including `decide_wait`: https://kovca.io/docs/mcp
