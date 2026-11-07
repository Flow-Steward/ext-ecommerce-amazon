#!/usr/bin/env bash
# TEST / MOCK Amazon UI fixture on Core's isolated manual OAuth browser lane.
#
#   FS_COMPOSE_PROJECT_NAME=fs-amazon-mock-manual-<id> \
#   FS_OAUTH_MANUAL_PORT=<port> FS_OAUTH_MANUAL_PROVIDER_PORT=<port> \
#   FS_AMAZON_MOCK_CORE_ROOT=<clean Core checkout> FS_AMAZON_MOCK_CORE_REVISION=<sha> \
#   FS_AMAZON_MOCK_EXT_REVISION=<extension sha> \
#   tests/ui_mock/run.sh up|exercise|ca|status|down
#
# up        builds the lane's three images from the Core checkout (offline, no pull),
#           streams the extension at FS_AMAZON_MOCK_EXT_REVISION into a volume, checks
#           it blob by blob, starts the stack and seeds users, the TEST / MOCK
#           extension and the project only (no configuration, no connection).
# exercise  creates the TEST LWA configuration, the three connections and the three
#           refusals through the HTTP API and checks the mock's egress log.
# ca        exports the per-run CA certificate for a browser that should trust it.
# status    shows the project's containers.
# down      removes the project's containers, networks, volumes and images, and
#           fails unless none is left.
#
# Run only inside an explicit Docker/build grant, after recording the project in the
# owner ledger. It never touches another Compose project.
set -euo pipefail

FIXTURE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
EXT_ROOT="$(cd -- "${FIXTURE_DIR}/../.." && pwd)"
PROJECT_NAME="${FS_COMPOSE_PROJECT_NAME:-}"
CORE_ROOT="${FS_AMAZON_MOCK_CORE_ROOT:-}"
CORE_REVISION="${FS_AMAZON_MOCK_CORE_REVISION:-}"
EXT_REVISION="${FS_AMAZON_MOCK_EXT_REVISION:-}"
MANUAL_PORT="${FS_OAUTH_MANUAL_PORT:-}"
PROVIDER_PORT="${FS_OAUTH_MANUAL_PROVIDER_PORT:-}"
action="${1:-}"

fail() { echo "$*" >&2; exit 2; }

[[ "${PROJECT_NAME}" =~ ^fs-amazon-mock-manual-[a-z0-9-]+$ ]] \
  || fail "Set a unique FS_COMPOSE_PROJECT_NAME matching fs-amazon-mock-manual-*."
[[ -n "${CORE_ROOT}" && -f "${CORE_ROOT}/infra/docker/docker-compose.oauth-browser-manual.yaml" ]] \
  || fail "Set FS_AMAZON_MOCK_CORE_ROOT to a Core checkout with the manual OAuth lane."
for port in "${MANUAL_PORT}" "${PROVIDER_PORT}"; do
  [[ "${port}" =~ ^[0-9]+$ && "${port}" -ge 1024 && "${port}" -le 65535 ]] \
    || fail "Set FS_OAUTH_MANUAL_PORT and FS_OAUTH_MANUAL_PROVIDER_PORT to unused ports 1024-65535."
done
[[ "${MANUAL_PORT}" != "${PROVIDER_PORT}" ]] || fail "The two ports must differ."

export FS_COMPOSE_PROJECT_NAME="${PROJECT_NAME}"
export FS_OAUTH_PROVIDER_INTERNAL_PORT="${PROVIDER_PORT}"
export FS_OAUTH_PROVIDER_BASE_URL="https://oauth.127.0.0.1.nip.io:${PROVIDER_PORT}"
export FS_AMAZON_MOCK_EXT_REVISION="${EXT_REVISION:-cleanup}"
compose=(
  docker compose --project-name "${PROJECT_NAME}"
  --file "${CORE_ROOT}/infra/docker/docker-compose.yaml"
  --file "${CORE_ROOT}/infra/docker/docker-compose.oauth-browser.yaml"
  --file "${CORE_ROOT}/infra/docker/docker-compose.oauth-browser-manual.yaml"
  --file "${FIXTURE_DIR}/compose.amazon-mock.yaml"
)
tag="${CORE_REVISION:0:16}"
export FS_OAUTH_ACCEPTANCE_APP_IMAGE="${PROJECT_NAME}-app:${tag:-cleanup}"
export FS_OAUTH_ACCEPTANCE_AUDIT_IMAGE="${PROJECT_NAME}-audit:${tag:-cleanup}"
export FS_OAUTH_ACCEPTANCE_FRONTEND_IMAGE="${PROJECT_NAME}-frontend:${tag:-cleanup}"

remove_images() {
  local ids=()
  local repository image_id
  for repository in app audit frontend; do
    while IFS= read -r image_id; do
      [[ -n "${image_id}" ]] && ids+=("${image_id}")
    done < <(docker image ls --quiet --filter "reference=${PROJECT_NAME}-${repository}:*")
  done
  if [[ ${#ids[@]} -gt 0 ]]; then docker image rm "${ids[@]}" >/dev/null; fi
}

down() {
  "${compose[@]}" --profile amazon-mock-tools down --volumes --remove-orphans
  remove_images
  test -z "$(docker ps -aq --filter "label=com.docker.compose.project=${PROJECT_NAME}")"
  test -z "$(docker network ls -q --filter "label=com.docker.compose.project=${PROJECT_NAME}")"
  test -z "$(docker volume ls -q --filter "label=com.docker.compose.project=${PROJECT_NAME}")"
  test -z "$(docker image ls -q --filter "reference=${PROJECT_NAME}-*")"
  echo "removed ${PROJECT_NAME}: no container, network, volume or image left"
}

case "${action}" in
  down) down; exit 0 ;;
  status) "${compose[@]}" ps --all; exit 0 ;;
  ca)
    out="${TMPDIR:-/tmp}/${PROJECT_NAME}-ca.crt"
    "${compose[@]}" cp oauth-cert-init:/certs/ca.crt "${out}"
    printf '%s\n' "${out}"
    openssl x509 -in "${out}" -noout -fingerprint -sha256
    exit 0 ;;
  exercise)
    FS_AMAZON_MOCK_SEED_STAGE=exercise "${compose[@]}" run --rm --no-deps amazon-mock-seed
    "${compose[@]}" run --rm --no-deps --entrypoint cat amazon-mock-seed /state/amazon-mock.json
    exit 0 ;;
  up) ;;
  *) fail "Usage: run.sh up|exercise|ca|status|down" ;;
esac

[[ "${CORE_REVISION}" =~ ^[0-9a-f]{40}$ ]] || fail "Set FS_AMAZON_MOCK_CORE_REVISION to the full Core SHA."
[[ "${EXT_REVISION}" =~ ^[0-9a-f]{40}$ ]] || fail "Set FS_AMAZON_MOCK_EXT_REVISION to the full extension SHA."
[[ "$(git -C "${CORE_ROOT}" rev-parse HEAD)" == "${CORE_REVISION}" ]] || fail "Core checkout is not at ${CORE_REVISION}."
[[ -z "$(git -C "${CORE_ROOT}" status --porcelain=v1 --untracked-files=all)" ]] || fail "Core checkout is not clean."
git -C "${EXT_ROOT}" cat-file -e "${EXT_REVISION}^{commit}" || fail "Extension revision ${EXT_REVISION} is unknown."

cleanup_failure() { down >/dev/null 2>&1 || true; }
trap cleanup_failure EXIT INT TERM

# The rendered project: no bind mounts, nothing published but Traefik on loopback,
# the mock unpublished, and the default network internal.
"${compose[@]}" config --format json | python3 -c '
import json, os, sys
config = json.load(sys.stdin)
services = config["services"]
for name, service in services.items():
    assert all(mount.get("type") != "bind" for mount in service.get("volumes", [])), name
    ports = service.get("ports", [])
    if name in {"traefik", "oauth-provider"}:
        assert all(port.get("host_ip") == "127.0.0.1" for port in ports), name
    else:
        assert not ports, name
assert config["networks"]["default"].get("internal") is True
assert not services["amazon-mock"].get("ports")
'

for target in app-runtime audit frontend-development; do
  case "${target}" in
    app-runtime) image="${FS_OAUTH_ACCEPTANCE_APP_IMAGE}" ;;
    audit) image="${FS_OAUTH_ACCEPTANCE_AUDIT_IMAGE}" ;;
    *) image="${FS_OAUTH_ACCEPTANCE_FRONTEND_IMAGE}" ;;
  esac
  docker build --network=none --pull=false --target "${target}" \
    --build-arg "BUILD_SHA=${CORE_REVISION:0:16}" --tag "${image}" \
    --file "${CORE_ROOT}/infra/docker/Dockerfile" "${CORE_ROOT}"
done

git -C "${EXT_ROOT}" archive --format=tar "${EXT_REVISION}" \
  | "${compose[@]}" --profile amazon-mock-tools run --rm -T --no-deps amazon-mock-source-init
git -C "${EXT_ROOT}" ls-tree -r --format='%(objectmode) %(objectname) %(path)' "${EXT_REVISION}" \
  | "${compose[@]}" --profile amazon-mock-tools run --rm -T --no-deps amazon-mock-source-verify

"${compose[@]}" up --detach --no-build --pull never amazon-mock-seed
seed_status="$("${compose[@]}" wait amazon-mock-seed | tail -n 1)"
[[ "${seed_status}" == "0" ]] || fail "amazon-mock-seed exited with ${seed_status}"

trap - EXIT INT TERM
printf 'TEST / MOCK fixture ready (no real Amazon): https://flow-steward.localhost:%s/extensions/flowsteward.amazon-sp-api-test-mock\n' "${MANUAL_PORT}"
printf 'Logins: the oauth-operator / oauth-admin test users of Core'"'"'s OAuth browser seed.\n'
printf 'Trust the per-run CA first: %q ca\n' "$0"
printf 'Cleanup: %q down\n' "$0"
