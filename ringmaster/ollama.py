"""Async HTTP client wrapper for the Ollama local inference API.

Ollama exposes a simple REST API on localhost:11434.  This module wraps it
so that the rest of Ringmaster never needs to know the raw endpoints or
response shapes — callers just call generate(), load_model(), etc.

Design decisions:
  - Uses httpx.AsyncClient throughout because Ringmaster is an async FastAPI
    application; a sync client would block the event loop during inference.
  - A single AsyncClient instance is reused across calls to benefit from
    HTTP connection pooling.  The caller is responsible for calling close()
    when the client is no longer needed (typically at application shutdown).
  - Timeout of 600 seconds: large models can take minutes to respond to the
    first generate call while they load into VRAM.  A shorter timeout would
    cause spurious OllamaError exceptions on first use.
  - stream=False on all generate calls: Ringmaster enqueues tasks and stores
    results, so streaming the response back line-by-line adds complexity
    without benefit.  The full response arrives in one shot.
  - Model loading/unloading uses the generate endpoint with an empty prompt
    or keep_alive=0 respectively — Ollama has no dedicated load/unload
    endpoint; this is the documented mechanism.
"""

from __future__ import annotations

import httpx


class OllamaError(Exception):
    """Raised when the Ollama API returns a non-200 HTTP status.

    Wraps the raw status code and response body so callers can log details
    without having to understand the httpx response object.
    """


class OllamaClient:
    """Async client for the Ollama local inference API.

    Wraps the Ollama REST API (https://github.com/ollama/ollama/blob/main/docs/api.md)
    so that callers interact with typed Python methods rather than raw HTTP.

    Usage::

        client = OllamaClient()
        text = await client.generate("llama3:8b", "Explain async/await.")
        await client.close()

    Or as an async context manager via a wrapper — close() must be called
    explicitly because OllamaClient intentionally does not implement
    ``__aenter__``/``__aexit__``; lifecycle is managed by the application
    startup/shutdown hooks in Ringmaster.
    """

    def __init__(self, base_url: str = "http://localhost:11434") -> None:
        """Create the client and its underlying httpx.AsyncClient.

        Args:
            base_url: Base URL of the Ollama server.  Defaults to the standard
                local address.  Override in tests or when Ollama runs on a
                different host/port.
        """
        self._base_url = base_url.rstrip("/")
        # 600 s timeout: large models take minutes to load into VRAM on the
        # first generate call.  A tighter value causes spurious errors.
        self._http = httpx.AsyncClient(timeout=600.0)

    async def generate(self, model: str, prompt: str) -> str:
        """Run inference and return the model's response as a string.

        Makes a single non-streaming POST to /api/generate and returns the
        complete response text once Ollama finishes generating.

        Args:
            model: Ollama model tag, e.g. ``"llama3:8b"`` or ``"mistral:7b"``.
            prompt: The input text to send to the model.

        Returns:
            The model's response text.

        Raises:
            OllamaError: If Ollama returns a non-200 HTTP status.
        """
        response = await self._http.post(
            f"{self._base_url}/api/generate",
            json={"model": model, "prompt": prompt, "stream": False},
        )
        if response.status_code != 200:
            raise OllamaError(
                f"Ollama returned {response.status_code}: {response.text}"
            )
        return response.json()["response"]

    async def load_model(self, model: str) -> None:
        """Pre-load a model into VRAM without running inference.

        Sending an empty prompt to /api/generate causes Ollama to load the
        model into GPU memory and return immediately, so the first real
        generate() call does not pay the cold-start penalty.

        Args:
            model: Ollama model tag to pre-load.
        """
        await self.generate(model, "")

    async def unload_model(self, model: str) -> None:
        """Evict a model from VRAM to free GPU memory.

        Setting keep_alive=0 tells Ollama to unload the model immediately
        after responding.  This is the documented mechanism for releasing
        GPU memory when a model is no longer needed.

        Args:
            model: Ollama model tag to evict from VRAM.
        """
        response = await self._http.post(
            f"{self._base_url}/api/generate",
            json={"model": model, "prompt": "", "stream": False, "keep_alive": 0},
        )
        if response.status_code != 200:
            raise OllamaError(
                f"Ollama returned {response.status_code} on unload: {response.text}"
            )

    async def list_models(self) -> list[dict]:
        """Return a list of all models available on this Ollama server.

        Calls GET /api/tags, which lists every model that has been pulled
        locally.  Each dict in the returned list includes at least ``name``
        and ``size`` keys.

        Returns:
            List of model dicts as returned by Ollama.

        Raises:
            OllamaError: If Ollama returns a non-200 HTTP status.
        """
        response = await self._http.get(f"{self._base_url}/api/tags")
        if response.status_code != 200:
            raise OllamaError(
                f"Ollama returned {response.status_code} on list_models: {response.text}"
            )
        return response.json()["models"]

    async def list_running(self) -> list[dict]:
        """Return a list of models currently loaded in VRAM.

        Calls GET /api/ps.  Models appear here only while they are loaded;
        they disappear once Ollama evicts them due to keep_alive expiry or
        an explicit unload_model() call.

        Returns:
            List of running model dicts as returned by Ollama.

        Raises:
            OllamaError: If Ollama returns a non-200 HTTP status.
        """
        response = await self._http.get(f"{self._base_url}/api/ps")
        if response.status_code != 200:
            raise OllamaError(
                f"Ollama returned {response.status_code} on list_running: {response.text}"
            )
        return response.json()["models"]

    async def close(self) -> None:
        """Close the underlying HTTP client and release its connection pool.

        Must be called at application shutdown to avoid ResourceWarning about
        unclosed sockets.  In Ringmaster this is called from the FastAPI
        lifespan shutdown hook.
        """
        await self._http.aclose()
