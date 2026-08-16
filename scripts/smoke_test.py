"""Runtime smoke tests executed against the finished container image."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
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
    assert headers["user-agent"].startswith("hermes-cli/")
    assert "authorization" not in headers
    assert models == ["ag/gemini-pro"]


def test_dashboard_model_catalog_secret_scope() -> None:
    import hermes_cli.inventory as inventory
    import hermes_cli.web_server as web_server
    from agent import secret_scope

    with tempfile.TemporaryDirectory() as tmp:
        profile_home = Path(tmp)
        (profile_home / ".env").write_text(
            "CPA_API_KEY=profile-only-key\n", encoding="utf-8"
        )

        @contextmanager
        def fake_profile_scope(_profile):
            yield profile_home

        original_scope = web_server._profile_scope
        original_context = inventory.load_picker_context
        original_builder = inventory.build_model_options_payload
        previous_multiplex = secret_scope.is_multiplex_active()
        web_server._profile_scope = fake_profile_scope
        inventory.load_picker_context = lambda: object()
        inventory.build_model_options_payload = (
            lambda *_args, **_kwargs: {
                "key": secret_scope.get_secret("CPA_API_KEY", "")
            }
        )
        secret_scope.set_multiplex_active(True)
        try:
            payload = asyncio.run(
                web_server.get_model_options(profile="smoke", refresh=True)
            )
        finally:
            secret_scope.set_multiplex_active(previous_multiplex)
            inventory.build_model_options_payload = original_builder
            inventory.load_picker_context = original_context
            web_server._profile_scope = original_scope

        assert payload == {"key": "profile-only-key"}


def test_profile_scoped_provider_base_url() -> None:
    import os

    from agent import secret_scope
    from hermes_cli.auth import resolve_api_key_provider_credentials

    previous_key = os.environ.get("GOOGLE_API_KEY")
    previous_url = os.environ.get("GEMINI_BASE_URL")
    previous_multiplex = secret_scope.is_multiplex_active()
    os.environ["GOOGLE_API_KEY"] = "dashboard-process-key"
    os.environ["GEMINI_BASE_URL"] = "https://dashboard-process.example/v1beta"
    token = secret_scope.set_secret_scope(
        {
            "GOOGLE_API_KEY": "profile-key",
            "GEMINI_BASE_URL": "https://profile.example/v1beta",
        }
    )
    secret_scope.set_multiplex_active(True)
    try:
        creds = resolve_api_key_provider_credentials("gemini")
    finally:
        secret_scope.set_multiplex_active(previous_multiplex)
        secret_scope.reset_secret_scope(token)
        if previous_key is None:
            os.environ.pop("GOOGLE_API_KEY", None)
        else:
            os.environ["GOOGLE_API_KEY"] = previous_key
        if previous_url is None:
            os.environ.pop("GEMINI_BASE_URL", None)
        else:
            os.environ["GEMINI_BASE_URL"] = previous_url

    assert creds["api_key"] == "profile-key"
    assert creds["base_url"] == "https://profile.example/v1beta"


def test_qq_full_group_observe_and_upgrade() -> None:
    from gateway.config import PlatformConfig
    from gateway.platforms.qqbot import QQAdapter

    class Store:
        def __init__(self):
            self.session = SimpleNamespace(session_id="qq-group-session")
            self.messages: list[dict] = []

        def get_or_create_session(self, _source, touch_activity=True):
            return self.session

        def append_to_transcript(self, _session_id, message):
            self.messages.append(dict(message))

        def load_transcript(self, _session_id):
            return list(self.messages)

        def discard_observed_platform_message(self, _session_id, message_id):
            self.messages = [
                row
                for row in self.messages
                if not (
                    row.get("observed")
                    and row.get("message_id") == message_id
                )
            ]
            return True

    async def exercise() -> None:
        adapter = QQAdapter(PlatformConfig(enabled=True, extra={
            "app_id": "smoke-app",
            "client_secret": "smoke-secret",
            "group_policy": "allowlist",
            "group_allow_from": ["group-1"],
            "group_sessions_per_user": False,
            "group_message_mode": "observe",
        }))
        store = Store()
        adapter._session_store = store
        handled = []

        async def capture(event):
            handled.append(event)

        adapter.handle_message = capture
        payload = {
            "id": "message-1",
            "group_openid": "group-1",
            "content": "ordinary chatter",
            "timestamp": "2026-08-16T00:00:00+00:00",
            "author": {
                "member_openid": "member-1",
                "username": "Alice",
            },
        }

        await adapter._on_message("GROUP_MESSAGE_CREATE", payload)
        assert handled == []
        assert len(store.messages) == 1
        assert store.messages[0]["observed"] is True

        await adapter._on_message("GROUP_AT_MESSAGE_CREATE", payload)
        assert len(handled) == 1
        assert store.messages == []
        assert handled[0].metadata["shared_group_session"] is True
        assert handled[0].text.startswith("[Alice|member-1]")

        direct = QQAdapter(PlatformConfig(enabled=True, extra={
            "app_id": "smoke-app",
            "client_secret": "smoke-secret",
            "group_policy": "allowlist",
            "group_allow_from": ["group-1"],
            "group_sessions_per_user": False,
            "group_message_mode": "direct",
            "group_prompts": {
                "direct": "custom direct smoke prompt",
                "addressed": "custom addressed smoke prompt",
            },
        }))
        direct._session_store = Store()
        direct_handled = []

        async def capture_direct(event):
            direct_handled.append(event)

        direct.handle_message = capture_direct
        direct_payload = {**payload, "id": "message-2"}

        await direct._on_message("GROUP_MESSAGE_CREATE", direct_payload)
        await direct._on_message("GROUP_AT_MESSAGE_CREATE", direct_payload)
        assert len(direct_handled) == 1
        assert direct_handled[0].allow_gateway_control is False
        assert direct_handled[0].metadata["defer_intermediate_delivery"] is True
        assert direct_handled[0].channel_prompt == "custom direct smoke prompt"

        command_payload = {
            **payload,
            "id": "message-3",
            "content": "<@bot-openid>/new",
            "mentions": [{"id": "bot-openid", "bot": True}],
        }
        await direct._on_message("GROUP_MESSAGE_CREATE", command_payload)
        assert len(direct_handled) == 2
        command_event = direct_handled[1]
        assert command_event.text == "/new"
        assert command_event.is_command() is True
        assert command_event.allow_gateway_control is True
        assert command_event.metadata["shared_group_session"] is False

        from tools.qq_group_send_tool import (
            QQ_GROUP_SEND_MEDIA_SCHEMA,
            QQ_GROUP_SEND_SCHEMA,
            qq_group_send,
            qq_group_send_media,
        )

        assert set(QQ_GROUP_SEND_SCHEMA["parameters"]["properties"]) == {"message"}
        assert set(QQ_GROUP_SEND_MEDIA_SCHEMA["parameters"]["properties"]) == {
            "area", "path", "caption"
        }
        assert "error" in json.loads(qq_group_send("outside an ambient turn"))
        assert "error" in json.loads(qq_group_send_media(
            "generated", "/tmp/not-bound.png"
        ))

    asyncio.run(exercise())


def test_qq_group_file_isolation() -> None:
    from gateway.platforms.qqbot.group_workspace import group_workspace_paths
    from gateway.session_context import reset_session_vars, set_session_vars
    from hermes_constants import (
        reset_hermes_home_override,
        set_hermes_home_override,
    )
    from tools.qq_group_files_tool import (
        qq_group_file_copy,
        qq_group_file_list,
        qq_group_file_patch,
        qq_group_file_read,
        qq_group_file_search,
        qq_group_file_write,
    )
    from tools.qq_group_send_tool import resolve_current_group_media_path
    from toolsets import resolve_toolset

    safe_root = os.getenv("HERMES_WRITE_SAFE_ROOT")
    temp_parent = safe_root if safe_root and Path(safe_root).is_dir() else None
    with tempfile.TemporaryDirectory(dir=temp_parent) as tmp:
        profile_home = Path(tmp) / "profile"
        home_token = set_hermes_home_override(profile_home)
        reset_session_vars()
        set_session_vars(
            platform="qqbot",
            chat_type="group",
            chat_id="group-a",
            profile="qq-main",
            session_key="qqbot:group-a",
        )
        try:
            write_result = json.loads(
                qq_group_file_write("notes/hello.txt", "hello group")
            )
            assert "error" not in write_result
            assert "hello group" in qq_group_file_read("notes/hello.txt")
            patch_result = json.loads(
                qq_group_file_patch(
                    "notes/hello.txt", "hello group", "hello patched group"
                )
            )
            assert patch_result["success"] is True
            assert "hello patched group" in qq_group_file_read("notes/hello.txt")
            qq_group_file_write("notes/move-me.txt", "move me")
            v4a_result = json.loads(qq_group_file_patch(
                mode="patch",
                patch="""*** Begin Patch
*** Update File: notes/hello.txt
-hello patched group
+hello v4a group
*** Add File: archive/created.txt
+created
*** Move File: notes/move-me.txt -> archive/moved.txt
*** End Patch""",
            ))
            assert v4a_result["success"] is True
            assert "hello v4a group" in qq_group_file_read("notes/hello.txt")
            assert "move me" in qq_group_file_read("archive/moved.txt")
            assert "created" in qq_group_file_read("archive/created.txt")
            copied = json.loads(
                qq_group_file_copy("archive/moved.txt", "incoming/copied.txt")
            )
            assert copied["success"] is True
            assert "move me" in qq_group_file_read("incoming/copied.txt")
            search_result = json.loads(
                qq_group_file_search("hello v4a", path="notes")
            )
            assert search_result["matches"][0]["path"] == "notes/hello.txt"
            listing = json.loads(qq_group_file_list("notes"))
            assert [entry["name"] for entry in listing["entries"]] == [
                "hello.txt"
            ]

            other = group_workspace_paths(
                profile_home, "group-b", create=True
            ).workspace / "private.txt"
            other.write_text("other group", encoding="utf-8")
            escaped = json.loads(qq_group_file_read(str(other)))
            assert "outside this group workspace" in escaped["error"]

            generated = profile_home / "cache" / "images" / "generated.png"
            generated.parent.mkdir(parents=True)
            generated.write_bytes(b"image")
            assert resolve_current_group_media_path(
                "generated", str(generated)
            ) == generated
        finally:
            reset_session_vars()
            reset_hermes_home_override(home_token)

    assert set(resolve_toolset("qq_group_files")) == {
        "qq_group_file_copy",
        "qq_group_file_list",
        "qq_group_file_patch",
        "qq_group_file_read",
        "qq_group_file_search",
        "qq_group_file_write",
    }


def test_multiplex_profile_session_partition() -> None:
    from gateway.config import Platform
    from gateway.session import SessionSource, SessionStore

    store = object.__new__(SessionStore)
    store.config = SimpleNamespace(
        multiplex_profiles=True,
        group_sessions_per_user=True,
        thread_sessions_per_user=False,
    )
    store._profile_session_partitions = {}
    store.set_profile_session_partition(
        "secondary",
        group_sessions_per_user=False,
        thread_sessions_per_user=False,
    )

    def source(user_id: str) -> SessionSource:
        return SessionSource(
            platform=Platform.QQBOT,
            chat_id="group-1",
            chat_type="group",
            user_id=user_id,
            profile="secondary",
        )

    assert store._generate_session_key(source("alice")) == (
        store._generate_session_key(source("bob"))
    )


if __name__ == "__main__":
    test_native_url_detection()
    test_native_request_shape()
    test_native_model_catalog()
    test_dashboard_model_catalog_secret_scope()
    test_profile_scoped_provider_base_url()
    test_qq_full_group_observe_and_upgrade()
    test_qq_group_file_isolation()
    test_multiplex_profile_session_partition()
    print("CPA, QQ full-group, and isolated group-file smoke tests passed")
