# Connect Claude Code or Cursor to the Kovca MCP server

An agent that can list your workflows is useful. An agent that can publish them, or approve a waiting run, is a different level of access. Kovca's remote MCP server uses one workspace API key, and the key's access level is the whole permission boundary.

This guide is the setup published on https://kovca.io/docs/mcp, plus the Cursor header interpolation documented at https://cursor.com/docs/mcp. It does not open a live MCP session. No key is included.

## The endpoint

```text
POST https://kovca.io/api/v1/mcp
Authorization: Bearer $KOVCA_API_KEY
```

The server is Streamable HTTP without sessions: JSON responses, no server-sent event stream. A workspace key is the bearer token. Instant workflow keys, the temporary `kvt_` keys, cannot use it. Hosted clients that can only sign in with OAuth cannot connect yet. Use a client that sends the bearer header.

MCP requests are limited to 120 a minute per workspace, separate from the plan's API rate. Over the limit the server returns `429 RATE_LIMITED` with `Retry-After`. Reading and editing are free. `run_workflow`, a workflow exposed as its own tool, and `test_workflow` are billed like the matching Studio or API run.

## Which key to create

Create the key on the API keys page. It is shown once. Kovca stores a hash and a 12-character display prefix. Revocation is immediate and a revoked key returns `401 UNAUTHORIZED`.

| Access | What the agent can see | Use it for |
| --- | --- | --- |
| Run | `list_workflows`, `get_workflow` (published versions and schemas), `run_workflow`, `get_run`, `list_runs`, `list_waits`, and any workflow you exposed as its own tool | An assistant that runs a workflow you already published. `run_workflow` performs real actions. |
| Author | Everything Run can do, plus templates, node specs, connections, draft edits, `validate_workflow`, and `test_workflow` | Building a draft. Tests bill AI steps. HTTP and app actions return samples unless `live_actions` is true. |
| Publish | Everything Author can do, plus `publish_workflow`, automation activate/pause/resume/deactivate, `set_workflow_enabled`, `archive_workflow`, and `decide_wait` | A maintainer key. `decide_wait` still fails with `403 DECISIONS_NOT_ALLOWED` unless that approval step allows API decisions. |

A Run key that calls an Author or Publish tool gets `403 INSUFFICIENT_ACCESS`. Access cannot be changed on an existing key. Create a new key and revoke the old one.

`list_waits` returns open approvals, delays, and callbacks. It does not return the approval message or the answers. `decide_wait` is the MCP form of `POST /v1/runs/{run_id}/waits/{step_id}/decision`.

The server also exposes the resource `kovca://guide` and node specs at `kovca://nodes/{ref}`. Agents are told to read the guide first. Signing in to GitHub, Slack, or another app still happens in a browser. `connect_app` returns a link. The agent never receives the account token.

## Claude Code

The Kovca docs give this command. `guides/mcp/claude-code.example.sh` runs it only after `KOVCA_API_KEY` is set, and it refuses a `kvt_` key.

```bash
claude mcp add --transport http kovca https://kovca.io/api/v1/mcp \
  --header "Authorization: Bearer $KOVCA_API_KEY"
```

Set the variable in the shell that launches Claude Code. Do not paste the key into a project file. Run access is the right default until you want the agent to edit drafts.

## Cursor

Kovca's docs show a `~/.cursor/mcp.json` entry with the bearer value written out as a `kvc_` placeholder. Do not commit that. Cursor resolves `${env:NAME}` inside `headers` (https://cursor.com/docs/mcp). Project config is `.cursor/mcp.json`. User config is `~/.cursor/mcp.json`.

`guides/mcp/cursor.mcp.json`:

```json
{
  "mcpServers": {
    "kovca": {
      "url": "https://kovca.io/api/v1/mcp",
      "headers": {
        "Authorization": "Bearer ${env:KOVCA_API_KEY}"
      }
    }
  }
}
```

Export `KOVCA_API_KEY` in the environment Cursor inherits, then reload MCP from Customize. Remote servers do not take an `envFile`. A value pasted into `mcp.json` is a secret in a file that is easy to commit. Cursor asks before a tool call unless you have allowed that tool to run.

`run_workflow` is a real run. Leave it requiring approval if the workflow can label an issue, post a message, or spend credits.

## VS Code

Kovca's docs use an input prompt so the key is typed, not stored in the JSON. `guides/mcp/vscode.mcp.json` matches that shape. Put it at `.vscode/mcp.json` if you want it for one project. The file contains no key.

## Check the connection

These checks need your own key. This repository has not run them.

1. Start the client and open its MCP log or tool list.
2. With a Run key you should see the Run tools listed above. You should not see `edit_workflow`, `publish_workflow`, or `decide_wait`.
3. Call `list_workflows`. A successful result is the workspace's workflows, their latest version, and the draft revision. Reading is free.
4. Do not call `run_workflow` or `test_workflow` as a connection test. Both can bill, and `run_workflow` runs actions for real. `test_workflow` bills AI steps even when actions are simulated.

## 401 and permission errors

| What you see | What it usually means |
| --- | --- |
| `401 UNAUTHORIZED` | The header is missing, the word `Bearer` is missing, the key was revoked, the key is malformed, or it is a `kvt_` instant key. Create a workspace key and retry. |
| `403 INSUFFICIENT_ACCESS` | The tool needs Author or Publish and this key is lower. The call did not change the draft. |
| `403 DECISIONS_NOT_ALLOWED` | `decide_wait` reached an approval that does not allow API decisions. A person uses the approval link or the Approvals inbox. |
| `403 WORKFLOW_DISABLED` | The workflow is turned off. A run did not start. |
| `409 REVISION_CONFLICT` | `base_revision` is stale. Read the draft again. Nothing was saved from that call. |
| `429 RATE_LIMITED` | Wait for `Retry-After`. The MCP limit is 120 requests a minute per workspace. |
| Tool missing | The client cached the tool list from an older key. Reconnect after you switch keys. A workflow exposed as a tool appears the next time tools are listed, and only for a published workflow, up to 50 per workspace. |

A 401 is not a sign that the workflow id is wrong. An unknown workflow is `404 NOT_FOUND`.

## Cleanup

Revoke the key on the API keys page when you are done with the experiment. Remove the MCP entry from the client, or leave the entry in place with the env var unset so the header cannot be built. Deleting the JSON does not revoke the key.

## Official docs

- MCP server: https://kovca.io/docs/mcp
- API keys: https://kovca.io/docs/api-keys
- Errors: https://kovca.io/docs/errors
- Approvals, including `decide_wait`: https://kovca.io/docs/approvals
- CLI, which uses the same server: https://kovca.io/docs/cli
- Cursor MCP config interpolation: https://cursor.com/docs/mcp
- What Kovca stores: https://kovca.io/security
