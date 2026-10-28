#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"

usage() {
    cat <<'EOF'
사용법: bash scripts/builder.sh [--dry-run] [--force] [--aosp-sandbox] [COMMAND ...]

WSL ext4에 있는 소스 폴더를 builder 컨테이너의 /src에 연결합니다.
명령을 생략하면 대화형 Bash를 엽니다. 현재 사용자의 UID/GID로 실행합니다.
REDROID_SOURCE_DIR: 소스 경로. 기본값은 $HOME/work/redroid-aosp15입니다.
REDROID_BUILDER_IMAGE: 이미지. 기본값은 redroid-builder:a15-ubuntu22.04입니다.
--dry-run: 경로와 실행 명령만 출력합니다.
--force: 확인 절차를 생략하지 않는 호환 옵션입니다.
--aosp-sandbox: nsjail 실행을 위해 이 컨테이너의 Docker seccomp 제한을 해제합니다.
EOF
}

dry_run=false
aosp_sandbox=false
while (($#)); do
    case "$1" in
        --help|-h) usage; exit 0 ;;
        --dry-run) dry_run=true; shift ;;
        --force) shift ;;
        --aosp-sandbox) aosp_sandbox=true; shift ;;
        --) shift; break ;;
        *) break ;;
    esac
done

[[ $(uname -s) == Linux && $(uname -m) == x86_64 ]] || {
    echo 'x86_64 Linux/WSL 안에서 실행해 주세요.' >&2; exit 1;
}
[[ $EUID -ne 0 ]] || { echo '일반 사용자로 실행해 주세요.' >&2; exit 1; }
source_dir=${REDROID_SOURCE_DIR:-$HOME/work/redroid-aosp15}
builder_image=${REDROID_BUILDER_IMAGE:-redroid-builder:a15-ubuntu22.04}
[[ -d $source_dir ]] || { echo "소스 디렉터리가 없습니다: $source_dir" >&2; exit 1; }
source_dir=$(realpath -e -- "$source_dir")
filesystem=$(findmnt -n -o FSTYPE --target "$source_dir")
[[ $filesystem == ext4 ]] || {
    echo "소스 경로에는 ext4가 필요합니다: $source_dir ($filesystem)" >&2; exit 1;
}
builder_home="$HOME/.cache/redroid-builder/home"
if (($# == 0)); then
    set -- /bin/bash
fi
docker_args=(run --rm --init -i --user "$(id -u):$(id -g)"
    --env HOME=/builder-home
    --env BUILD_USERNAME=redroid
    --env BUILD_HOSTNAME=redroid-builder
    --env BUILD_NUMBER=redroid.a15r36
    --mount "type=bind,source=$source_dir,target=/src"
    --mount "type=bind,source=$builder_home,target=/builder-home"
    --mount "type=bind,source=$project_dir/scripts,target=/control-scripts,readonly"
    --mount "type=bind,source=$project_dir,target=/control,readonly"
    --workdir /src)
if $aosp_sandbox; then
    docker_args+=(--security-opt seccomp=unconfined)
fi
if [[ -t 0 && -t 1 ]]; then
    docker_args+=(-t)
fi
if $dry_run; then
    printf '소스: %s\n이미지: %s\n명령: docker ' "$source_dir" "$builder_image"
    printf '%q ' "${docker_args[@]}" "$builder_image" "$@"
    printf '\n'
    exit 0
fi
docker image inspect "$builder_image" >/dev/null
mkdir -p -- "$builder_home"
exec docker "${docker_args[@]}" "$builder_image" "$@"
