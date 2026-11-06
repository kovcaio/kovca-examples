# Contributing

These examples are meant to stay small enough to copy. A change should keep that shape.

## Tests

Python 3.11 or newer. No third-party packages.

```bash
python3 -m unittest discover -s tests -t . -v
```

The tests do not call Kovca, GitHub, or Slack. They use local fixtures and a local mock HTTP server. Do not add a test that needs a real API key.

## Secrets and fixtures

- Put placeholders in `.env.example` only. Do not commit a key, a webhook secret, or a filled-in `.env`.
- New fixtures should be obviously synthetic. Names such as `acme/storefront` and `maria-chen` in the existing fixtures are the stand-ins from Kovca's public guides.
- Do not add a personal email, a personal GitHub login, or a path from a private machine.

## API shapes

Check request and response changes against the current public docs before editing a mapper:

- https://kovca.io/docs/workflows
- https://kovca.io/docs/api-reference
- https://kovca.io/docs/errors
- https://kovca.io/docs/idempotency
- https://kovca.io/docs/approvals
- https://kovca.io/docs/mcp

Do not invent a node name or a `kovca workflows apply` file. The public export envelope is documented, and the approval page shows one graph fragment. The fragment in this repo is that documented example. It is not an apply file, because the `definition` object inside an export is not fully specified in the public docs.

## Live calls

Keep dry-run as the default. A path that contacts Kovca has to require the same explicit flags the existing commands use, and it has to keep using the caller's idempotency key on retries.
