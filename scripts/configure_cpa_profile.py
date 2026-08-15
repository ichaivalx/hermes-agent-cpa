#!/usr/bin/env python3
"""Configure one Hermes profile for the four CPA protocol surfaces.

This helper is executed inside the Hermes container by
``configure-cpa-profile.sh``.  The CPA URL and key arrive on stdin so the
credential never appears in the process command line.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen

from hermes_cli.config import get_config_path, get_env_path, save_env_value
from utils import atomic_yaml_write, fast_safe_load


PROVIDER_ENTRIES: dict[str, dict[str, Any]] = {
    "cpa-chat": {
        "name": "CPA · Chat Completions",
        "transport": "chat_completions",
    },
    "cpa-responses": {
        "name": "CPA · OpenAI Responses",
        "transport": "codex_responses",
    },
    "cpa-messages": {
        "name": "CPA · Anthropic Messages",
        "transport": "anthropic_messages",
    },
}


def normalize_cpa_root(raw_url: str) -> str:
    """Return a CPA deployment root, accepting common endpoint URLs as input."""
    raw_url = raw_url.strip().rstrip("/")
    parsed = urlsplit(raw_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("CPA 地址必须是完整的 http:// 或 https:// URL")
    if parsed.username or parsed.password:
        raise ValueError("CPA 地址不能内嵌用户名或密码")
    if parsed.query or parsed.fragment:
        raise ValueError("CPA 地址不能包含查询参数或 #fragment")

    path = parsed.path.rstrip("/")
    known_suffixes = (
        "/v1/chat/completions",
        "/v1/responses/compact",
        "/v1/responses",
        "/v1/messages/count_tokens",
        "/v1/messages",
        "/v1/models",
        "/v1beta/models",
        "/v1beta",
        "/v1",
    )
    lowered = path.lower()
    for suffix in known_suffixes:
        if lowered.endswith(suffix):
            path = path[: -len(suffix)].rstrip("/")
            break

    return urlunsplit((parsed.scheme, parsed.netloc, path, "", "")).rstrip("/")


def require_profile_home() -> Path:
    raw_home = os.environ.get("HERMES_HOME", "").strip()
    if not raw_home:
        raise RuntimeError("HERMES_HOME 未设置；请通过外层 Shell 脚本运行")

    home = Path(raw_home).resolve(strict=False)
    data_root = Path("/opt/data").resolve(strict=False)
    profiles_root = data_root / "profiles"
    if home != data_root and not home.is_relative_to(profiles_root):
        raise RuntimeError(f"拒绝修改容器数据目录之外的路径：{home}")
    if not home.is_dir():
        raise RuntimeError(f"Profile 目录不存在：{home}")
    if home != data_root and not (home / "SOUL.md").is_file():
        raise RuntimeError(
            f"{home} 不是完整的 Hermes Profile（缺少 SOUL.md）；请先创建 Profile"
        )
    return home


def read_payload() -> tuple[str, str]:
    raw_url = sys.stdin.readline().rstrip("\n")
    api_key = sys.stdin.readline().rstrip("\n")
    if not raw_url:
        raise ValueError("没有收到 CPA 地址")
    if not api_key:
        raise ValueError("没有收到 CPA API Key")
    return normalize_cpa_root(raw_url), api_key


def load_user_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as handle:
        config = fast_safe_load(handle) or {}
    if not isinstance(config, dict):
        raise RuntimeError(f"{path} 的顶层必须是 YAML mapping")
    return config


def mapping(config: dict[str, Any], key: str) -> dict[str, Any]:
    value = config.get(key)
    if value is None:
        value = {}
        config[key] = value
    if not isinstance(value, dict):
        raise RuntimeError(f"config.yaml 中的 {key!r} 不是 mapping，拒绝覆盖")
    return value


def backup_file(path: Path, stamp: str) -> Path | None:
    if not path.exists():
        return None
    backup = path.with_name(f"{path.name}.cpa-backup-{stamp}")
    shutil.copy2(path, backup)
    return backup


def configure_profile(home: Path, cpa_root: str, api_key: str) -> list[Path]:
    config_path = get_config_path()
    env_path = get_env_path()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backups = [
        backup
        for backup in (
            backup_file(config_path, stamp),
            backup_file(env_path, stamp),
        )
        if backup is not None
    ]

    config = load_user_config(config_path)
    providers = mapping(config, "providers")
    openai_base = f"{cpa_root}/v1"

    for provider_id, defaults in PROVIDER_ENTRIES.items():
        current = providers.get(provider_id)
        if current is None:
            current = {}
            providers[provider_id] = current
        if not isinstance(current, dict):
            raise RuntimeError(
                f"providers.{provider_id} 已存在但不是 mapping，拒绝覆盖"
            )
        current.update(defaults)
        current.update(
            {
                "api": cpa_root if provider_id == "cpa-messages" else openai_base,
                "key_env": "CPA_API_KEY",
                "discover_models": True,
                "enabled": True,
            }
        )

    # Apply the user's requested no-learning baseline without deleting any
    # existing memories or skills.  The data stays available for rollback.
    memory = mapping(config, "memory")
    memory.update(
        {
            "memory_enabled": False,
            "user_profile_enabled": False,
            "provider": "",
            "write_approval": True,
        }
    )

    skills = mapping(config, "skills")
    skills.update({"write_approval": True, "guard_agent_created": True})

    curator = mapping(config, "curator")
    curator["enabled"] = False

    agent = mapping(config, "agent")
    disabled = agent.get("disabled_toolsets")
    if disabled is None:
        disabled = []
    if not isinstance(disabled, list) or not all(
        isinstance(item, str) for item in disabled
    ):
        raise RuntimeError("agent.disabled_toolsets 必须是字符串列表")
    agent["disabled_toolsets"] = list(dict.fromkeys([*disabled, "memory", "skills"]))

    atomic_yaml_write(config_path, config, sort_keys=False, create_mode=0o600)

    # Keep the single CPA key out of config.yaml.  Gemini's built-in provider
    # uses GOOGLE_API_KEY + GEMINI_BASE_URL; the three named providers use
    # CPA_API_KEY through key_env.
    save_env_value("CPA_API_KEY", api_key)
    save_env_value("GOOGLE_API_KEY", api_key)
    save_env_value("GEMINI_BASE_URL", f"{cpa_root}/v1beta")

    print(f"已更新 Profile：{home}")
    print("已配置协议端点：")
    print(f"  cpa-chat       -> {openai_base}  [chat_completions]")
    print(f"  cpa-responses  -> {openai_base}  [codex_responses]")
    print(f"  cpa-messages   -> {cpa_root}  [anthropic_messages]")
    print(f"  gemini         -> {cpa_root}/v1beta  [Gemini Native]")
    print("已关闭内置 Memory、USER Profile 注入、Skill 写入入口和 Curator。")
    print("现有 Memory/Skill 文件没有删除，可随时回滚。")
    if backups:
        print("备份文件：")
        for backup in backups:
            print(f"  {backup}")
    return backups


def fetch_json(url: str, headers: dict[str, str], timeout: float = 12.0) -> Any:
    request = Request(url, headers={"Accept": "application/json", **headers})
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code}") from None
    except URLError as exc:
        raise RuntimeError(str(exc.reason)) from None
    except json.JSONDecodeError:
        raise RuntimeError("响应不是 JSON") from None


def probe_catalogs(cpa_root: str, api_key: str) -> bool:
    checks = (
        (
            "Chat / Responses catalog",
            f"{cpa_root}/v1/models",
            {"Authorization": f"Bearer {api_key}"},
            "data",
        ),
        (
            "Anthropic Messages catalog",
            f"{cpa_root}/v1/models",
            {
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
            },
            "data",
        ),
        (
            "Gemini Native catalog",
            f"{cpa_root}/v1beta/models",
            {"x-goog-api-key": api_key},
            "models",
        ),
    )
    all_ok = True
    print("端点发现检查：")
    for label, url, headers, list_key in checks:
        try:
            payload = fetch_json(url, headers)
            models = payload.get(list_key) if isinstance(payload, dict) else None
            if not isinstance(models, list):
                raise RuntimeError(f"缺少 {list_key}[]")
            print(f"  ✓ {label}: {len(models)} 个模型")
        except Exception as exc:
            all_ok = False
            print(f"  ⚠ {label}: {exc}")
    return all_ok


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--skip-probe",
        action="store_true",
        help="Save configuration without querying CPA model catalogs.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        home = require_profile_home()
        cpa_root, api_key = read_payload()
        configure_profile(home, cpa_root, api_key)
        if not args.skip_probe:
            probe_catalogs(cpa_root, api_key)
        print("配置写入完成。脚本没有替你选择默认模型。")
        return 0
    except Exception as exc:
        print(f"配置失败：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
