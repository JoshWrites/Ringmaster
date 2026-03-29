"""Tests for ringmaster.ollama — async Ollama API client.

These tests follow TDD: they were written before the implementation existed.
All network calls are intercepted by pytest-httpx so no real Ollama server is
needed.  Each test exercises a single method and a single scenario so that a
failure points directly at what broke.
"""

import pytest
from pytest_httpx import HTTPXMock

from ringmaster.ollama import OllamaClient, OllamaError


@pytest.fixture
def client() -> OllamaClient:
    """Return an OllamaClient pointed at the default local Ollama address.

    Using the default URL keeps the fixture simple and matches the most common
    deployment.  Tests that need a custom URL can construct their own client.
    """
    return OllamaClient()


@pytest.mark.asyncio
async def test_generate_sends_correct_request(
    client: OllamaClient, httpx_mock: HTTPXMock
) -> None:
    """generate() POSTs the right payload and returns the response text.

    Verifies that:
      - The request goes to POST /api/generate.
      - The payload contains the model name, prompt, and stream=False.
      - The returned string is the ``response`` field from the JSON body.
    """
    httpx_mock.add_response(
        method="POST",
        url="http://localhost:11434/api/generate",
        json={"response": "Paris", "done": True},
    )

    result = await client.generate("llama3:8b", "What is the capital of France?")

    assert result == "Paris"

    request = httpx_mock.get_request()
    assert request is not None
    import json
    payload = json.loads(request.content)
    assert payload["model"] == "llama3:8b"
    assert payload["prompt"] == "What is the capital of France?"
    assert payload["stream"] is False


@pytest.mark.asyncio
async def test_list_models(client: OllamaClient, httpx_mock: HTTPXMock) -> None:
    """list_models() GETs /api/tags and returns the models list.

    Verifies that:
      - The request goes to GET /api/tags.
      - The returned list is the ``models`` array from the response JSON.
    """
    httpx_mock.add_response(
        method="GET",
        url="http://localhost:11434/api/tags",
        json={
            "models": [
                {"name": "llama3:8b", "size": 4_661_226_496},
                {"name": "mistral:7b", "size": 4_109_854_720},
            ]
        },
    )

    models = await client.list_models()

    assert len(models) == 2
    assert models[0]["name"] == "llama3:8b"
    assert models[1]["name"] == "mistral:7b"


@pytest.mark.asyncio
async def test_load_model(client: OllamaClient, httpx_mock: HTTPXMock) -> None:
    """load_model() sends a generate request with an empty prompt.

    Ollama pre-loads a model into VRAM by calling /api/generate with an empty
    prompt — this confirms that load_model() uses that exact mechanism rather
    than a non-existent dedicated endpoint.
    """
    httpx_mock.add_response(
        method="POST",
        url="http://localhost:11434/api/generate",
        json={"response": "", "done": True},
    )

    await client.load_model("llama3:8b")

    request = httpx_mock.get_request()
    assert request is not None
    import json
    payload = json.loads(request.content)
    assert payload["model"] == "llama3:8b"
    assert payload["prompt"] == ""
    assert payload["stream"] is False


@pytest.mark.asyncio
async def test_unload_model(client: OllamaClient, httpx_mock: HTTPXMock) -> None:
    """unload_model() sends a generate request with keep_alive=0.

    Ollama evicts a model from VRAM when keep_alive is set to 0 on a generate
    call.  This confirms the correct eviction mechanism is used.
    """
    httpx_mock.add_response(
        method="POST",
        url="http://localhost:11434/api/generate",
        json={"response": "", "done": True},
    )

    await client.unload_model("llama3:8b")

    request = httpx_mock.get_request()
    assert request is not None
    import json
    payload = json.loads(request.content)
    assert payload["model"] == "llama3:8b"
    assert payload["keep_alive"] == 0
    assert payload["stream"] is False


@pytest.mark.asyncio
async def test_list_running(client: OllamaClient, httpx_mock: HTTPXMock) -> None:
    """list_running() GETs /api/ps and returns the running models list.

    Verifies that:
      - The request goes to GET /api/ps.
      - The returned list is the ``models`` array from the response JSON.
    """
    httpx_mock.add_response(
        method="GET",
        url="http://localhost:11434/api/ps",
        json={
            "models": [
                {"name": "llama3:8b", "expires_at": "2024-06-04T14:38:31.83753-07:00"},
            ]
        },
    )

    running = await client.list_running()

    assert len(running) == 1
    assert running[0]["name"] == "llama3:8b"


@pytest.mark.asyncio
async def test_generate_raises_on_ollama_error(
    client: OllamaClient, httpx_mock: HTTPXMock
) -> None:
    """generate() raises OllamaError when Ollama returns a non-200 status.

    A 500 from Ollama indicates an internal error (e.g. the model failed to
    load).  Surfacing this as OllamaError lets callers catch it specifically
    without having to inspect raw HTTP responses.
    """
    httpx_mock.add_response(
        method="POST",
        url="http://localhost:11434/api/generate",
        status_code=500,
        json={"error": "model 'bad-model' not found"},
    )

    with pytest.raises(OllamaError):
        await client.generate("bad-model", "hello")
