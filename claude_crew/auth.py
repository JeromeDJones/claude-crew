"""Auth detection and startup-time validation for the Claude Code SDK.

Fast pre-flight check: confirms a credential is *findable* in one of the
places the SDK itself will look. Does not validate the credential by
calling the API — that happens lazily on first send and surfaces via
the per-turn error envelope path.

Precedence (highest to lowest), matching the SDK's own order:
1. ANTHROPIC_API_KEY env var
2. CLAUDE_CODE_OAUTH_TOKEN env var
3. ~/.claude/.credentials.json (Claude Code OAuth session)
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def has_usable_credential() -> bool:
    """Return True iff the SDK is likely to find a working credential."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        return True
    if os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
        return True
    if (Path.home() / ".claude" / ".credentials.json").exists():
        return True
    return False


def validate_auth_or_exit() -> None:
    """Exit the process with code 2 if no credential is findable."""
    if has_usable_credential():
        return
    sys.stderr.write(
        "claude-crew: no Claude credentials found.\n"
        "  Run 'claude login' to set up Claude Code session auth, or\n"
        "  export ANTHROPIC_API_KEY=<your-key>.\n"
    )
    sys.exit(2)
