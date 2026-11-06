#!/bin/sh
# Connect Claude Code to the Kovca MCP server.
# The command is the one published at https://kovca.io/docs/mcp.
# The key is read from the environment and is not written into this file.

set -eu

if [ -z "${KOVCA_API_KEY:-}" ]; then
  echo "Set KOVCA_API_KEY to a workspace key before running this script." >&2
  exit 2
fi

case "${KOVCA_API_KEY}" in
  kvt_*)
    echo "Instant workflow keys cannot use MCP. Create a workspace key on the API keys page." >&2
    exit 2
    ;;
esac

exec claude mcp add --transport http kovca https://kovca.io/api/v1/mcp \
  --header "Authorization: Bearer ${KOVCA_API_KEY}"
