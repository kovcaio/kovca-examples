# Triage unlabeled GitHub issues

New GitHub issues arrive unlabeled. Bugs, feature requests, questions, and noise sit in one list. Someone sorts them by hand, and a bug that never includes steps waits until a person asks.

## Solution

Kovca's guide [Triage and label new GitHub issues](https://kovca.io/guides/triage-and-label-new-github-issues) builds this in the Studio from the template named GitHub issue triage.

| Step | What the template does |
| --- | --- |
| GitHub trigger `Issue opened or changed` | Starts on `action`, `created_at`, `created_by`, `description`, `issue_id`, `number`, `title`, and `url`. |
| Condition | Continues only when `action` is `opened`. Anything else goes to the output and does not spend an evaluation. |
| Text, then Support Triage (`builtin:support/triage@1`) | Classifies the title and description into `category`, `priority`, and `requires_human_review`. |
| Lookup | Maps `technical` to `["bug"]`, `product` to `["product"]`, `billing` to `["billing"]`, `account` to `["account"]`, `other` to `["other"]`, and anything else to `["triage"]`. Those names are stand-ins. |
| GitHub `action:github/add_labels@1` | Adds that list. The guide says this action is safe to retry. A failure stops the run, so the comment does not run. |
| Condition | `category` equals `technical`. |
| GitHub `action:github/comment_on_issue@1` | Posts a fixed request for steps, expected result, and actual result. The guide says this action is sent once. A failure continues the run with `commented` false. |

Support Triage does not invent a label. `technical` is its bucket for errors, bugs, outages, crashes, slow performance, and integration problems. `product` covers both a how-to and a feature request. `priority` is not used by the stock template.

Two ways to run it:

- Activate the automation in the Studio. Kovca receives GitHub events on the repository you connect. You choose the GitHub account there. This program is not involved.
- Publish the workflow and call `POST /v1/workflows/{id}/execute` from a service that already receives the GitHub webhook. `triage.py` is that caller.

`triage.py` checks the payload, maps it onto the trigger fields, and prints the request. It does not classify the issue and it does not call the GitHub API. The label and the comment, if they happen, happen inside the published workflow.

The public `kovca workflows export` envelope is documented at https://kovca.io/docs/cli. The `definition` object inside it is not fully specified, so this directory does not ship a file for `kovca workflows apply`. Create the draft from the template in the Studio. `kovca workflows templates` lists template ids once you are signed in. This repo does not guess an id.

## Prerequisites and minimum scopes

- Python 3.11 or newer. No packages to install.
- A published copy of that workflow. Drafts cannot be called through the API.
- `KOVCA_API_KEY` with **Run** access for a live call. Author and Publish are not required to execute.
- The workflow's GitHub connection, owner, and repo are configured in Kovca. This program has no GitHub token.
- `GITHUB_WEBHOOK_SECRET` only if you use `--delivery`. Signature rules: https://docs.github.com/en/webhooks/using-webhooks/validating-webhook-deliveries
- Optional `GITHUB_REPOSITORY` (`acme/storefront` in the guide). When it is set, a delivery for any other repository is refused.

## Setup

From the repository root:

```bash
python3 examples/github-issue-triage/triage.py --help
```

For a live call, export the variables. `.env.example` lists them. This program does not read a `.env` file, so a forgotten file cannot be picked up.

```bash
export KOVCA_API_KEY="the key from the API keys page"
export KOVCA_WORKFLOW_ID="wf_..."
# optional:
# export GITHUB_REPOSITORY="acme/storefront"
# export KOVCA_WORKFLOW_VERSION="1"
```

Replace the template's `acme` / `storefront` stand-ins on the trigger, on Add labels, and on Comment before you activate. The event does not include owner or repo, so those three steps each store the repository themselves.

## Dry run

Nothing in this command contacts Kovca or GitHub.

```bash
python3 examples/github-issue-triage/triage.py \
  --fixture examples/github-issue-triage/fixtures/issue-opened.webhook.json
```

The printed JSON is a dry run. `live_call` is false. The same output is checked in at `fixtures/dry-run.output.json`. The body `input` is:

- `action`: `opened`
- `created_at`: `2026-11-02T14:03:00Z`
- `created_by`: `maria-chen`
- `description`: the synthetic cart-total paragraph from the guide
- `issue_id`: `3092845511`
- `number`: `1287`
- `title`: `Cart total missing shipping for CA orders`
- `url`: the guide's stand-in issue URL

`Idempotency-Key` is `3092845511`, the issue id, which is what the guide uses. The Authorization value is either `(not set)` or the key's 12-character prefix. The full key is not printed.

An edited issue is not forwarded. The template would not classify it, and calling Kovca would spend a run credit for a path that stops at the condition.

```bash
python3 examples/github-issue-triage/triage.py \
  --fixture examples/github-issue-triage/fixtures/issue-edited.webhook.json
```

`skipped` is true and there is no request. `--include-other-actions` forwards it with a different idempotency key, `github-issue:{id}:{action}`, so it does not collide with the opened call. The stock template still does not label or comment that delivery.

`fixtures/issue-untrusted-instructions.webhook.json` is a synthetic issue that tells the reader to print an API key and add an admin label. The dry run puts that text in `description` and does not act on it.

## Expected results

`fixtures/sample-response.shape-from-guide.json` is the success shape published in the guide, with synthetic ids (`req_synthetic`, `run_synthetic`, `wf_synthetic`). It is not a response captured from a live run. Support Triage's category depends on the text. The guide's sample for this cart-total issue is `technical` / `medium`, labels `["bug"]`, and `commented` true. Treat that as the documented sample, then run your own examples in the Studio before you rely on a route.

On a non-technical category the guide says `comment` and `commented` are null. On an action other than `opened`, `opened` is false and the classification, label, and comment are null. Values for steps that did not run are null. Kovca does not invent them.

## Live usage

This spends credits and can label and comment through the published workflow's GitHub connection.

```bash
python3 examples/github-issue-triage/triage.py \
  --fixture examples/github-issue-triage/fixtures/issue-opened.webhook.json \
  --live --confirm-side-effects
```

`--live` without `--confirm-side-effects` exits with an error and sends nothing.

For a real webhook, verify the raw body before mapping it:

```bash
python3 examples/github-issue-triage/triage.py \
  --delivery ./body.json \
  --signature "$SIGNATURE_HEADER" \
  --live --confirm-side-effects
```

`SIGNATURE_HEADER` is the `X-Hub-Signature-256` value. `GITHUB_WEBHOOK_SECRET` must be the webhook secret. A bad signature sends nothing.

Prefer the Studio test, with **Send real requests** off, before the first live call. Simulated GitHub actions do not change the issue. The guide's test examples are named Cart total and How-to question.

## Failure, retry, and idempotency

- The idempotency key for an opened issue is the issue id. The same key and the same body within 24 hours return `202` with the original run. Nothing runs again and the duplicate body does not include `output`. Keep the first response. https://kovca.io/docs/idempotency
- The same key with a different body returns `409 IDEMPOTENCY_KEY_CONFLICT`. A later `edited` event is not sent by default, so it does not burn that key.
- This client retries `429 RATE_LIMITED`, `500`, `503`, and `504 TIMEOUT`, with the same key, up to three attempts. It does not retry `429 USAGE_LIMIT_EXCEEDED`, `402`, other `4xx`, or `504 ACTION_TIMEOUT`. An action timeout may already have changed the issue.
- Add labels, in the guide, stops the run when it fails and is safe to retry. Comment on issue is sent once and is not retried by Kovca. If the comment fails, the label is already on the issue and the run can still succeed with `commented` false.
- A failed automation run is not retried by Kovca, because the label or the comment may already have happened. While an automation is paused, events are skipped.
- A GitHub redelivery of the same opened payload maps to the same key and the same body, so it collapses to the original run.

## Cost and side effects

Credits are charged for steps that succeed. The numbers below are the ones in the guide and in https://kovca.io/docs/billing. This repository has not measured a live run.

- API-started run: 1 credit. An automation event from GitHub is 10 credits instead.
- Support Triage: 5 credits, and only when `action` is `opened`.
- Add labels: 2 credits when the GitHub action runs for real.
- Comment: 2 credits when it runs for real, on the technical branch only.
- Conditions, text, and lookup: free. A branch that does not run is not charged.
- The guide's Activate panel sums every step, including branches that cannot run together, and shows 19 credits as an upper bound. `credits` on the response is the run's total.
- Studio tests are free, and simulated actions are free.

A live call from this program uses the API price, not the automation price. It still runs whatever actions the published version contains. Do not point it at a production repository until the Studio tests match the labels you want. Issue text is untrusted. The stock template does not copy it into a shell or into the label name. If you add a step that does, the issue body can steer it.

Free workspaces include 5,000 credits a month in the first workspace. This example does not turn on Pay As You Go.

## Cleanup

- Revoke the Run key on the API keys page if you created it for the experiment.
- Pause or deactivate the automation in the Studio if you turned it on. Pausing skips events that arrive while it is paused.
- If a live run labeled or commented, remove that label or comment in GitHub. This program cannot undo it. It has no GitHub token.
- Nothing in this directory creates a remote resource of its own.

## Troubleshooting

| Symptom | What to check |
| --- | --- |
| Dry run says `KOVCA_WORKFLOW_ID` is not set | Expected until you export the published id. The dry run is still valid. |
| `401 UNAUTHORIZED` | The key is missing, revoked, or is a `kvt_` instant key. Run access must be a `kvc_` workspace key. https://kovca.io/docs/api-keys |
| `400 INVALID_REQUEST` | The published workflow's input fields do not match the trigger fields. Unknown fields are rejected. A manual test in the Studio shows the schema. |
| `403 WORKFLOW_DISABLED` | The workflow is turned off. Nothing ran. |
| `404 NOT_FOUND` | The id is not in this key's workspace, or the version does not exist. |
| `409 IDEMPOTENCY_KEY_CONFLICT` | This issue id was already used with a different body in the last 24 hours. |
| `429 USAGE_LIMIT_EXCEEDED` | The workspace is out of credits or at its cap. Do not retry until that changes. |
| Signature error | The header is `X-Hub-Signature-256`, the secret matches the webhook, and the file is the raw body. |
| Empty title, or title plus description over 8,000 characters | Refused locally. Support Triage's message limit is 8,000, and the template builds that message from both fields. |
| Comment did not appear, label did | The guide's comment step continues on failure. `commented` is false. Comment is not retried by Kovca. |

## Official docs

- Guide: https://kovca.io/guides/triage-and-label-new-github-issues
- Workflows API: https://kovca.io/docs/workflows
- Idempotency: https://kovca.io/docs/idempotency
- Errors: https://kovca.io/docs/errors
- Billing: https://kovca.io/docs/billing
- Automations: https://kovca.io/docs/automations
- API keys: https://kovca.io/docs/api-keys
- GitHub webhook signatures: https://docs.github.com/en/webhooks/using-webhooks/validating-webhook-deliveries
