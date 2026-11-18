"""
recaply — the game-agnostic clipping core.

Stages: ingest -> analyze (signals) -> score (fusion + LLM re-rank) ->
select -> render -> publish. Each stage lives in its own subpackage and is
usable on its own; `recaply.pipeline.run` chains them, and the web app,
the desktop app and the CLI (`python -m recaply`) all call it.
"""

__all__ = ["__version__"]
__version__ = "3.0.0-dev"
