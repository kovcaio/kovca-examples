# Kovca examples

Small programs that call a [Kovca](https://kovca.io/docs) workflow you have already published, and a setup guide for the Kovca MCP server. Each example starts from a concrete operational problem: an unlabeled GitHub issue, a Slack message that should have opened an incident, or an AI decision that should wait for a person.

The programs are dry-run by default. They do not create GitHub comments, Slack messages, incidents, or approval decisions unless you pass `--live` and `--confirm-side-effects` with your own workspace key. The automated tests use a local mock. They do not call Kovca.

Python 3.11 or newer. There are no third-party packages.

```bash
python3 -m unittest discover -s tests -t . -v
```

## Triage unlabeled GitHub issues

A new issue sits unlabeled until someone sorts it. The Studio template **GitHub issue triage** classifies an opened issue, adds one label from that category, and asks for repro steps when the category is technical.

[examples/github-issue-triage](examples/github-issue-triage/README.md) maps a GitHub `issues` webhook onto the fields that template documents and shows the `POST /v1/workflows/{id}/execute` request. It does not call the GitHub API. Run access is enough.

```bash
python3 examples/github-issue-triage/triage.py \
  --fixture examples/github-issue-triage/fixtures/issue-opened.webhook.json
```

## Escalate a Slack message into an incident

A support channel mixes outage reports with ordinary chatter. The Studio template **Slack incident escalation** classifies a new message and, only when the priority is high, opens an incident, posts to on-call, and reacts to the message.

[examples/slack-incident-alerts](examples/slack-incident-alerts/README.md) maps a Slack message event onto that template's input. It does not post to Slack. Bot messages are skipped unless you opt in. Run access is enough.

```bash
python3 examples/slack-incident-alerts/alert.py \
  --fixture examples/slack-incident-alerts/fixtures/message-outage.event.json
```

## Hold an AI decision until a person approves

A refund or a similar case should pause until a person answers. Kovca's `wait:approval@1` step does that. API decisions are off unless the step allows them, and answering over the API needs a Publish key.

[examples/ai-approval-workflows](examples/ai-approval-workflows/README.md) submits a one-field `message`, reads run metadata, and can send a decision only after a second confirmation. The checked-in graph is the fragment from the approvals doc. It is not a `kovca workflows apply` file. The public export envelope is documented, and the `definition` object inside it is not fully specified, so this repo does not invent one.

```bash
python3 examples/ai-approval-workflows/review.py submit \
  --fixture examples/ai-approval-workflows/fixtures/refund-request.json
```

## Connect Claude Code or Cursor to Kovca MCP

The remote server is `POST https://kovca.io/api/v1/mcp`, Streamable HTTP, with a workspace bearer key. Instant `kvt_` keys cannot use it. Run access can list and run. Author access can edit drafts. Publish access can publish and decide approvals.

[guides/mcp](guides/mcp/README.md) has the Claude Code command from the Kovca docs, a Cursor config that uses `${env:KOVCA_API_KEY}` instead of a pasted key, the VS Code input prompt from the Kovca docs, and the 401 and `INSUFFICIENT_ACCESS` cases.

## What you publish first

Kovca runs a workflow you built in the Studio. These programs only call one you have published. Node references used by the official guides and the approvals page include `builtin:support/triage@1`, `action:github/add_labels@1`, `action:github/comment_on_issue@1`, `action:slack/send_message@1`, `action:slack/add_reaction@1`, and `wait:approval@1`. Create the GitHub and Slack drafts with **Use this template** on the linked guides. `kovca workflows templates` lists ids after `kovca login`. This repo does not hard-code a template id.

## Keys

Copy [`.env.example`](.env.example) into your shell. Do not commit a filled-in file.

- `KOVCA_API_KEY`: Run access for execute and run lookup.
- `KOVCA_PUBLISH_API_KEY`: Publish access, used only by the approval decision command.
- `KOVCA_WORKFLOW_ID`: the published workflow id from the Studio.

Keys start with `kvc_`. A `kvt_` key is rejected before a request is sent.

## Checked-in samples

Fixtures under each example are synthetic, including the stand-ins `acme/storefront` and `maria-chen` from Kovca's guides. Files named `sample-*.json` copy a response shape from the docs and use ids such as `run_synthetic`. They are not recordings of a live call.

## Docs used here

- Getting started and the execute API: https://kovca.io/docs and https://kovca.io/docs/workflows
- API keys: https://kovca.io/docs/api-keys
- MCP: https://kovca.io/docs/mcp
- CLI and workflows as code: https://kovca.io/docs/cli
- Approvals: https://kovca.io/docs/approvals
- Idempotency, errors, runs, billing: https://kovca.io/docs/idempotency, https://kovca.io/docs/errors, https://kovca.io/docs/runs, https://kovca.io/docs/billing
- GitHub issue guide: https://kovca.io/guides/triage-and-label-new-github-issues
- Slack incident guide: https://kovca.io/guides/escalate-urgent-slack-channel-messages
- Security: https://kovca.io/security

License: [MIT](LICENSE), copyright Kovca.
