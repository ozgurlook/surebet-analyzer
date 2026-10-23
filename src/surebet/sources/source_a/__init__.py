"""Source A public-source adapter (WebSocket JSON-RPC feed)."""

from .protocol import build_games_query, build_markets_query, parse_collection

__all__ = ["build_games_query", "build_markets_query", "parse_collection"]
