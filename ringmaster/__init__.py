"""Ringmaster — GPU workstation AI task orchestrator for home networks.

Ringmaster manages a queue of AI inference tasks (chat completions, embeddings,
image generation) and dispatches them to local GPU workstations running Ollama.
It handles workstation wake/sleep, idle detection, and priority scheduling so
that long-running tasks don't block interactive use.
"""

__version__ = "0.1.0"
