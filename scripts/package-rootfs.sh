#!/usr/bin/env bash
set -Eeuo pipefail

[[ $# -eq 2 ]] || { echo '산출물 소유자 UID/GID가 필요합니다.' >&2; exit 2; }
[[ $1 =~ ^[0-9]+$ && $2 =~ ^[0-9]+$ ]] || exit 2
[[ ! -e /work/rootfs.tar ]] || { echo '기존 rootfs tar를 덮어쓰지 않습니다.' >&2; exit 1; }

mounts=()
loops=()
cleanup() {
    local status=$? i
    trap - EXIT
    for ((i=${#mounts[@]}-1; i>=0; i--)); do
        if ! umount -- "${mounts[$i]}"; then status=1; fi
    done
    for device in "${loops[@]}"; do
        if ! losetup -d "$device"; then status=1; fi
    done
    exit "$status"
}
trap cleanup EXIT

mkdir -p /mounts/root
for partition in system vendor system_dlkm; do
    [[ -f /images/$partition.img ]] || { echo "필수 이미지가 없습니다: $partition" >&2; exit 1; }
    device=$(losetup --find --show --read-only "/images/$partition.img")
    loops+=("$device")
    target=/mounts/root
    if [[ $partition != system ]]; then target+="/$partition"; fi
    [[ -d $target && ! -L $target ]] || { echo "마운트 경로가 디렉터리가 아닙니다: $target" >&2; exit 1; }
    mount -t ext4 -o ro,noload "$device" "$target"
    mounts+=("$target")
done

python3 /control-scripts/rootfs_metadata.py inventory /mounts/root /work
tar --create --file=/work/rootfs.tar --format=pax --sort=name \
    --numeric-owner --acls --selinux --xattrs --xattrs-include='*' \
    --pax-option=exthdr.name=%d/PaxHeaders/%f,delete=atime,delete=ctime \
    --exclude='./lost+found' --exclude='./vendor/lost+found' --exclude='./system_dlkm/lost+found' \
    --directory=/mounts/root .
python3 /control-scripts/rootfs_metadata.py verify /work/rootfs.tar /work/rootfs-metadata.json
cc -O2 -static -Wall -Wextra -Werror /control-scripts/rootfs-probe.c -o /work/rootfs-probe
chown "$1:$2" /work/rootfs.tar /work/rootfs-metadata.json /work/rootfs-probe
echo '읽기 전용 이미지의 rootfs 조립과 tar 메타데이터 검증을 완료했습니다.'
