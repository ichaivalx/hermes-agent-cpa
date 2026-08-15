"""Runtime smoke tests executed against the finished container image."""

from __future__ import annotations

import json
from types import SimpleNamespace


def test_native_url_detection() -> None:
    from agent.gemini_native_adapter import is_native_gemini_base_url

    assert is_native_gemini_base_url(
        "https://generativelanguage.googleapis.com/v1beta"
    )
    assert is_native_gemini_base_url("https://cpa.example.test/v1beta")
    assert is_native_gemini_base_url("https://cpa.example.test/v1beta/")
    assert not is_native_gemini_base_url("https://cpa.example.test/v1")
    assert not is_native_gemini_base_url(
        "https://generativelanguage.googleapis.com/v1beta/openai"
    )


def test_native_request_shape() -> None:
    from agent.gemini_native_adapter import GeminiNativeClient

    recorded: dict[str, object] = {}

    class DummyHTTP:
        def post(self, url, json=None, headers=None, timeout=None):
            recorded.update(url=url, json=json, headers=headers)
            return SimpleNamespace(
                status_code=200,
                headers={},
                json=lambda: {
                    "candidates": [
                        {
                            "content": {"parts": [{"text": "ok"}]},
                            "finishReason": "STOP",
                        }
                    ],
                    "usageMetadata": {
                        "promptTokenCount": 1,
                        "candidatesTokenCount": 1,
                        "totalTokenCount": 2,
                    },
                },
            )

        def close(self):
            return None

    client = GeminiNativeClient(
        api_key="cpa-secret",
        base_url="https://cpa.example.test/v1beta",
        http_client=DummyHTTP(),
    )
    response = client.chat.completions.create(
        model="ag/gemini-pro",
        messages=[{"role": "user", "content": "ping"}],
    )

    assert recorded["url"] == (
        "https://cpa.example.test/v1beta/"
        "models/ag/gemini-pro:generateContent"
    )
    headers = recorded["headers"]
    assert isinstance(headers, dict)
    assert headers["x-goog-api-key"] == "cpa-secret"
    assert "Authorization" not in headers
    assert response.choices[0].message.content == "ok"


def test_native_model_catalog() -> None:
    import providers
    import hermes_cli.urllib_security as urllib_security

    profile = providers.get_provider_profile("gemini")
    assert profile is not None

    payload = {
        "models": [
            {
                "name": "models/ag/gemini-pro",
                "supportedGenerationMethods": ["generateContent"],
            },
            {
                "name": "models/embedding-only",
                "supportedGenerationMethods": ["embedContent"],
            },
        ]
    }
    recorded: dict[str, object] = {}

    class DummyResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def read(self):
            return json.dumps(payload).encode()

    def fake_open(request, *, timeout):
        recorded.update(request=request, timeout=timeout)
        return DummyResponse()

    original_open = urllib_security.open_credentialed_url
    urllib_security.open_credentialed_url = fake_open
    try:
        models = profile.fetch_models(
            api_key="cpa-secret",
            base_url="https://cpa.example.test/v1beta",
        )
    finally:
        urllib_security.open_credentialed_url = original_open

    request = recorded["request"]
    headers = {key.lower(): value for key, value in request.header_items()}
    assert request.full_url == "https://cpa.example.test/v1beta/models"
    assert headers["x-goog-api-key"] == "cpa-secret"
    assert "authorization" not in headers
    assert models == ["ag/gemini-pro"]


if __name__ == "__main__":
    test_native_url_detection()
    test_native_request_shape()
    test_native_model_catalog()
    print("CPA Gemini native smoke tests passed")
