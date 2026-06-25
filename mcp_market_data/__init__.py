"""Market-Data MCP — precise, timestamped, structured numbers for the daily
financial morning report.

This package exposes 8 tools (one per morning-report section). Every tool
returns the same JSON envelope (see ``contract.py``): value + unit + source +
as-of timestamp, with graceful degradation to ``status: unavailable`` instead
of raising. News / drivers stay in the morning-report prompt's web search —
this MCP only owns the hard numbers.
"""

__version__ = "0.1.0"
