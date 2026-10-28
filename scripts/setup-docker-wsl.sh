#!/usr/bin/env bash
set -Eeuo pipefail

usage() {
    cat <<'EOF'
사용법: bash setup-docker-wsl.sh --user USER [--dry-run] [--force]

Ubuntu에 공식 Docker Engine, Buildx, Compose와 호스트 보조 도구를 설치합니다.
root로 실행해야 합니다. --user 계정을 docker 그룹에 추가합니다.
--dry-run은 검사와 예정 작업 출력만 수행합니다.
--force는 기존 Docker apt 설정을 백업한 뒤 교체할 때만 사용합니다.
기존 컨테이너, 이미지, 볼륨 및 충돌 패키지는 삭제하지 않습니다.
EOF
}

target_user=''
dry_run=false
force=false
while (($#)); do
    case "$1" in
        --user)
            [[ $# -ge 2 ]] || { usage >&2; exit 2; }
            target_user=$2
            shift 2
            ;;
        --dry-run) dry_run=true; shift ;;
        --force) force=true; shift ;;
        --help|-h) usage; exit 0 ;;
        *) usage >&2; exit 2 ;;
    esac
done

[[ $EUID -eq 0 ]] || { echo 'root로 실행해 주세요.' >&2; exit 1; }
[[ -n $target_user && $target_user != root ]] || { usage >&2; exit 2; }
id "$target_user" >/dev/null
source /etc/os-release
[[ $ID == ubuntu ]] || { echo 'Ubuntu 전용 설치 스크립트입니다.' >&2; exit 1; }
case "$VERSION_ID" in
    22.04|24.04|26.04) ;;
    *) echo "검증 대상이 아닌 Ubuntu 버전입니다: $VERSION_ID" >&2; exit 1 ;;
esac
[[ $(dpkg --print-architecture) == amd64 ]] || { echo 'amd64 환경이 필요합니다.' >&2; exit 1; }
[[ $(ps -p 1 -o comm=) == systemd ]] || { echo 'systemd가 필요합니다.' >&2; exit 1; }

for package in docker.io docker-compose docker-compose-v2 docker-doc docker-buildx podman-docker containerd runc; do
    status=$(dpkg-query -W -f='${Status}' "$package" 2>/dev/null || true)
    if [[ $status == 'install ok installed' ]]; then
        echo "충돌 가능성이 있는 패키지를 확인해 주세요: $package" >&2
        exit 1
    fi
done

source_path=/etc/apt/sources.list.d/docker.sources
key_path=/etc/apt/keyrings/docker.asc
if [[ -e /etc/apt/sources.list.d/docker.list ]]; then
    echo '기존 docker.list를 직접 검토해 주세요. 자동으로 변경하지 않습니다.' >&2
    exit 1
fi
expected_source=$(cat <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: ${UBUNTU_CODENAME:-$VERSION_CODENAME}
Components: stable
Architectures: amd64
Signed-By: $key_path
EOF
)
if [[ -e $source_path ]] && [[ $(cat "$source_path") != "$expected_source" ]] && ! $force; then
    echo '기존 docker.sources가 다릅니다. 내용을 검토한 뒤 --force로 백업·교체할 수 있습니다.' >&2
    exit 1
fi

if $dry_run; then
    printf '대상: Ubuntu %s, 사용자 %s\n' "$VERSION_ID" "$target_user"
    echo '예정: 공식 apt 저장소 등록, Docker Engine/Buildx/Compose 설치 및 서비스 시작'
    echo '예정: git, git-lfs, python3, repo, jq, rsync 설치 및 docker 그룹 등록'
    exit 0
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends ca-certificates curl git git-lfs python3 repo jq rsync
install -m 0755 -d /etc/apt/keyrings
key_temp=$(mktemp)
trap 'rm -f -- "$key_temp"' EXIT
curl --fail --show-error --silent --location --retry 3 \
    https://download.docker.com/linux/ubuntu/gpg -o "$key_temp"
grep -q 'BEGIN PGP PUBLIC KEY BLOCK' "$key_temp"
install -m 0644 "$key_temp" "$key_path"
if [[ -e $source_path ]] && [[ $(cat "$source_path") != "$expected_source" ]]; then
    cp -a -- "$source_path" "${source_path}.backup-$(date -u +%Y%m%dT%H%M%SZ)"
fi
printf '%s\n' "$expected_source" > "$source_path"
apt-get update
apt-get install -y --no-install-recommends \
    docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
systemctl enable --now docker
usermod -aG docker "$target_user"
docker version
docker buildx version
docker compose version
echo '설치가 완료되었습니다. 새 WSL 세션에서 docker 그룹을 확인해 주세요.'
