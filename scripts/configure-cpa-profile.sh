#!/usr/bin/env bash
set -Eeuo pipefail

usage() {
  cat <<'EOF'
用法：
  ./configure-cpa-profile.sh [Profile 名称] [--skip-probe] [--restart]

示例：
  ./configure-cpa-profile.sh qq-main
  ./configure-cpa-profile.sh qq-main --restart

环境变量（可选）：
  HERMES_CONTAINER            Gateway 容器名，默认 hermes
  CPA_BASE_URL                CPA 根地址；未设置时交互输入
  CPA_API_KEY                 CPA Key；未设置时交互输入
EOF
}

profile="qq-main"
skip_probe=false
restart_gateway=false
profile_seen=false

for arg in "$@"; do
  case "$arg" in
    -h|--help)
      usage
      exit 0
      ;;
    --skip-probe)
      skip_probe=true
      ;;
    --restart)
      restart_gateway=true
      ;;
    --*)
      echo "未知参数：$arg" >&2
      usage >&2
      exit 2
      ;;
    *)
      if [[ "$profile_seen" == true ]]; then
        echo "只能指定一个 Profile。" >&2
        exit 2
      fi
      profile="$arg"
      profile_seen=true
      ;;
  esac
done

container="${HERMES_CONTAINER:-hermes}"
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
helper="$script_dir/configure_cpa_profile.py"

if [[ ! -f "$helper" ]]; then
  echo "缺少同目录文件：$helper" >&2
  exit 1
fi
if ! command -v docker >/dev/null 2>&1; then
  echo "找不到 docker 命令。" >&2
  exit 1
fi
if [[ "$(docker inspect --format '{{.State.Running}}' "$container" 2>/dev/null || true)" != "true" ]]; then
  echo "Hermes Gateway 容器未运行：$container" >&2
  exit 1
fi

case "${profile,,}" in
  default|this-dashboard|this_dashboard)
    profile="default"
    profile_home="/opt/data"
    ;;
  *)
    if [[ ! "$profile" =~ ^[a-zA-Z0-9][a-zA-Z0-9_-]*$ ]]; then
      echo "Profile 名称只能包含字母、数字、下划线和短横线。" >&2
      exit 1
    fi
    profile_home="/opt/data/profiles/${profile,,}"
    ;;
esac

if [[ "$profile" != "default" ]]; then
  if ! docker exec --user hermes "$container" test -f "$profile_home/SOUL.md"; then
    echo "Profile 不存在或不完整：$profile" >&2
    echo "请先创建：docker exec $container hermes profile create ${profile,,} --no-skills" >&2
    exit 1
  fi
fi

cpa_base_url="${CPA_BASE_URL:-}"
if [[ -z "$cpa_base_url" ]]; then
  read -r -p "CPA 根地址（例如 https://cpa.example.com）：" cpa_base_url
fi

cpa_api_key="${CPA_API_KEY:-}"
if [[ -z "$cpa_api_key" ]]; then
  # The user requested visible credential entry.  The value is never printed
  # by this script and is sent to the container over stdin, not argv.
  read -r -p "CPA API Key（输入内容会显示）：" cpa_api_key
fi

if [[ -z "$cpa_base_url" || -z "$cpa_api_key" ]]; then
  echo "CPA 地址和 API Key 都不能为空。" >&2
  exit 1
fi

remote_helper="/tmp/hermes-configure-cpa-$$.py"
cleanup() {
  docker exec "$container" rm -f -- "$remote_helper" >/dev/null 2>&1 || true
}
trap cleanup EXIT

docker cp "$helper" "$container:$remote_helper" >/dev/null
docker exec "$container" chmod 0644 "$remote_helper"

python_args=()
if [[ "$skip_probe" == true ]]; then
  python_args+=(--skip-probe)
fi

printf '%s\n%s\n' "$cpa_base_url" "$cpa_api_key" \
  | docker exec -i --user hermes \
      --env "HERMES_HOME=$profile_home" \
      "$container" \
      python "$remote_helper" "${python_args[@]}"

docker exec --user hermes \
  --env "HERMES_HOME=$profile_home" \
  "$container" \
  hermes config get providers --json >/dev/null

if [[ "$restart_gateway" == true ]]; then
  if [[ "$profile" == "default" ]]; then
    docker exec "$container" hermes gateway restart
  else
    docker exec "$container" hermes --profile "${profile,,}" gateway restart
  fi
else
  echo
  echo "配置已验证。需要立即重启该 Profile Gateway 时运行："
  if [[ "$profile" == "default" ]]; then
    echo "  docker exec $container hermes gateway restart"
  else
    echo "  docker exec $container hermes --profile ${profile,,} gateway restart"
  fi
fi

echo
echo "Dashboard 中会出现四条逻辑路由："
echo "  CPA · Chat Completions"
echo "  CPA · OpenAI Responses"
echo "  CPA · Anthropic Messages"
echo "  Gemini（内置 Provider，已指向 CPA /v1beta）"
echo "下一步只需在对应 Provider 下选择模型；本脚本不会擅自改默认模型。"
