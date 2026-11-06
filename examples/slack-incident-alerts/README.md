# Escalate a Slack message into an incident

Urgent outage reports land in a support channel next to ordinary chatter. Nobody opens an incident until someone happens to read the channel. By then the thread has moved on, and the people on call have not heard.

## Solution

Kovca's guide [Escalate urgent Slack channel messages into incidents](https://kovca.io/guides/escalate-urgent-slack-channel-messages) uses the template named Slack incident escalation.

| Step | What the template does |
| --- | --- |
| Slack trigger `New message` | Starts on `text`, `channel`, `ts`, `user`, and the other message fields listed in the guide. |
| Support Triage | Returns `category`, `priority` (`low`, `medium`, `high`), and `requires_human_review`. |
| Condition | Continues only when `priority` is `high`. Other messages are classified and then left alone. |
| HTTP request | Opens an incident. The template's sample response is an id of `INC-1042`. You point the step at your tracker and paste a real sample. |
| Text, then Slack `action:slack/send_message@1` | Posts to the on-call channel. The guide says this action is sent once. Failure continues the run with `paged` false. |
| Slack `action:slack/add_reaction@1` | Adds `eyes` on the original message. The guide says this action is safe to retry. Failure continues with `reacted` false. |
| Merge and output | Returns `escalated`, `priority`, `category`, `incident_id`, `paged`, and `reacted`. |

Activate that automation when Kovca should listen to the channel. The Slack account is connected in the Studio. The sign-in page names Composio, which the guide says holds the Slack access. Kovca keeps an account id.

Use `alert.py` when a Slack Events API request already hits your service and you want to forward one message to the published workflow. The program maps the documented fields and prints the execute request. It does not post to Slack, does not open an incident, and does not decide the priority. Those steps are inside the workflow.

There is no `kovca workflows apply` file here. The export `definition` schema is not fully published. Build the draft from the template.

## Prerequisites and minimum scopes

- Python 3.11 or newer.
- The workflow published. A Run key (`KOVCA_API_KEY`) can execute it. This program does not need Author or Publish access.
- Slack is connected on the trigger, on Send message, and on Add reaction inside Kovca, not in this program.
- `SLACK_SIGNING_SECRET` only for `--delivery`. Verification follows https://docs.slack.dev/authentication/verifying-requests-from-slack. Timestamps more than five minutes off are rejected.
- Optional `SLACK_CHANNEL_ID`. When set, other channels are skipped. The guide's stand-in is `C08SUPPORT1`.

This adapter also skips bot messages unless you pass `--include-bots`. The stock template does not do that. The skip is there so a message the workflow posts back into the channel is not forwarded into another run. The guide says a message the workflow itself posts does not restart the native automation. An application-side receiver does not get that protection unless it filters.

## Setup

```bash
python3 examples/slack-incident-alerts/alert.py --help
export KOVCA_API_KEY="the key from the API keys page"
export KOVCA_WORKFLOW_ID="wf_..."
# optional:
# export SLACK_CHANNEL_ID="C08SUPPORT1"
```

In the Studio, replace `C08SUPPORT1` and the on-call channel name `on-call`, and point the HTTP step at a tracker you control. Leave **Send real requests** off while you test. The HTTP step then returns its sample, and the Slack steps return their samples.

## Dry run

```bash
python3 examples/slack-incident-alerts/alert.py \
  --fixture examples/slack-incident-alerts/fixtures/message-outage.event.json
```

No network call. The same output is checked in at `fixtures/dry-run.output.json`. The request input is the guide's synthetic checkout outage:

- `text`: checkout failing with a 502 after card details
- `channel`: `C08SUPPORT1`
- `channel_type`: `channel`
- `user`: `U04MARIA01`
- `ts`: `1762003954.001200`
- `team_id`: `T02ACME0001`
- empty `attachments`, `blocks`, and `files`, because the fixture includes those arrays

`Idempotency-Key` is the message `ts`. The guide uses that so a retry does not open a second incident.

```bash
python3 examples/slack-incident-alerts/alert.py \
  --fixture examples/slack-incident-alerts/fixtures/message-from-bot.event.json
```

`skipped` is true. The message is not forwarded.

```bash
python3 examples/slack-incident-alerts/alert.py \
  --fixture examples/slack-incident-alerts/fixtures/url-verification.json
```

Slack's URL verification is answered locally. `local_response.challenge` is `synthetic_challenge_token`. Kovca is not called. Your HTTP handler should return that challenge itself. This command only shows it.

## Expected results

`fixtures/sample-response.shape-from-guide.json` is the success shape from the guide, with synthetic ids. It is not a live capture. The guide's sample for the checkout text is `technical` / `high`, `incident_id` `INC-1042`, and `paged` and `reacted` true, using the template's sample action results. A message Support Triage does not call high priority skips the incident, the post, and the reaction. Those output fields come back null. Run the Studio examples (the guide names Checkout outage and How-to question) before you treat a route as settled.

## Live usage

A live call can spend credits. If the published workflow classifies the message as high priority, its HTTP step and its Slack steps run for real.

```bash
python3 examples/slack-incident-alerts/alert.py \
  --fixture examples/slack-incident-alerts/fixtures/message-outage.event.json \
  --live --confirm-side-effects
```

A signed delivery:

```bash
python3 examples/slack-incident-alerts/alert.py \
  --delivery ./body.json \
  --timestamp "$SLACK_TIMESTAMP" \
  --signature "$SLACK_SIGNATURE" \
  --live --confirm-side-effects
```

The signature is computed over the raw body. Parse it only after verification. `--live` alone is refused.

## Failure, retry, and idempotency

- The idempotency key is the Slack message `ts`. The same key and body within 24 hours return the original run and do not open another incident. The duplicate response has no output. Keep the first one.
- A different payload with the same `ts` returns `409 IDEMPOTENCY_KEY_CONFLICT`.
- The HTTP incident step carries its own `Idempotency-Key` of the form `kovca_<run>_<step>`, stable across retries of that step. POST is retried only if you turn retry on for the step. https://kovca.io/docs/actions
- Send message is sent once. Add reaction is safe to retry. Both continue the run on failure, so a missed page or a missed emoji does not roll back an incident that was created.
- This client retries only `429 RATE_LIMITED`, `500`, `503`, and `504 TIMEOUT` on the call to Kovca. It does not retry `ACTION_TIMEOUT` or `USAGE_LIMIT_EXCEEDED`.
- Kovca does not retry a failed automation run on its own, because the incident or the Slack post may already have happened.
- Slack retries the Events API when your handler is slow. Verify the signature, then rely on the `ts` key. Answer `url_verification` yourself and do not forward it.

## Cost and side effects

From the guide and https://kovca.io/docs/billing. Not measured live by this repository.

- API-started run: 1 credit. A Slack automation event is 10.
- Support Triage: 5.
- HTTP incident create: 1 when it runs for real.
- Slack Send message or Add reaction: 2 each, when that action runs for real.
- The condition, the text step, and the merge are free.
- The guide's upper bound on the Activate panel is 20 credits per event, because it adds branches that cannot all run. The response `credits` field is the actual total.
- Studio tests and simulated actions are free.

The message text is untrusted. It is sent as the workflow `text` field and nothing else. Do not add a workflow step that shells out to it. A thread reply is forwarded, because the stock template leaves the thread filter unset. A reply whose text is "thanks, that fixed it" should not be escalated because an outage was quoted above it. Send the new message, not a pasted thread. The dry-run notes when `thread_ts` differs from `ts`.

This program never posts the on-call message itself. If you do not want a Slack post, do not run `--live` against a workflow whose high-priority branch contains Send message.

## Cleanup

- Revoke the Run key if it was only for the test.
- Pause the automation in the Studio. Events that arrive while it is paused are skipped.
- Delete a synthetic incident in your tracker and delete a synthetic Slack message or reaction if a live run created them. This program cannot do that.
- Rotate `SLACK_SIGNING_SECRET` in the Slack app if it was exposed. It is not read from a committed file.

## Troubleshooting

| Symptom | What to check |
| --- | --- |
| Bot message skipped | Default. Pass `--include-bots` only if you mean to forward it. |
| Wrong channel skipped | `SLACK_CHANNEL_ID` is set and does not match. |
| Empty text refused | A file-only message has nothing for Support Triage to read. |
| Text longer than 8,000 characters | Refused locally. That is the node's limit and the trigger field's limit. |
| `401` from Slack verification | The signing secret, the raw body, or the timestamp does not match. Five minutes is the replay window. |
| `401 UNAUTHORIZED` from Kovca | Workspace `kvc_` key, not a `kvt_` key, and not a revoked key. |
| `400 INVALID_REQUEST` | The published input schema does not match these trigger fields. Extra fields are rejected. |
| Incident created, no on-call post | Send message continues on failure. `paged` is false. It is not retried by Kovca. |
| `409 IDEMPOTENCY_KEY_CONFLICT` | That `ts` was already used with a different body. |

## Official docs

- Guide: https://kovca.io/guides/escalate-urgent-slack-channel-messages
- Workflows API: https://kovca.io/docs/workflows
- HTTP actions: https://kovca.io/docs/actions
- Automations: https://kovca.io/docs/automations
- Idempotency: https://kovca.io/docs/idempotency
- Billing: https://kovca.io/docs/billing
- Errors: https://kovca.io/docs/errors
- API keys: https://kovca.io/docs/api-keys
- Slack signatures: https://docs.slack.dev/authentication/verifying-requests-from-slack
