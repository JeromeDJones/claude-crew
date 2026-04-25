"""Console entrypoint: ``claude-crew`` runs the MCP server over stdio."""

from claude_crew.server import main

__all__ = ["main"]


if __name__ == "__main__":
    main()
