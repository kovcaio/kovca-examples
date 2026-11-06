# Security

This repository is a set of examples. It does not hold Kovca, GitHub, or Slack credentials, and the checked-in fixtures are synthetic.

## Reporting

If you believe the example code can be tricked into sending a credential somewhere it should not, or into acting without the live confirmation flags, open a private report with the maintainers of this repository. For a vulnerability in Kovca itself, email support@kovca.io and give Kovca a chance to fix it before a public write-up. That address is the one published at https://kovca.io/security.

## What this code will and will not do

- Dry-run is the default. A live Kovca call needs `--live` and, for anything that can spend credits or continue a workflow, `--confirm-side-effects`.
- API keys are read from the environment. Dry-run output shows at most the 12-character dashboard prefix.
- The HTTP client does not follow redirects and does not use an ambient proxy.
- GitHub and Slack deliveries are verified against the raw body before they are parsed. Comparison uses `hmac.compare_digest`. Slack timestamps older than five minutes are rejected.
- Issue titles, issue bodies, and Slack messages are sent as JSON strings. The programs do not pass them to a shell.

## Limits of these examples

The programs cannot make a published Kovca workflow safe on their own. A workflow that adds a GitHub label, comments, opens an incident, or posts to Slack will do that when a live run reaches those steps. Approval decisions continue the published workflow. Review the workflow in the Studio before you pass `--live`.

Nothing here is an enterprise certification, a penetration test, or a statement that a live workspace was exercised. The automated tests use a local mock of the documented HTTP responses.
