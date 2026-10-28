#!/usr/bin/env python3
import hashlib
import json
import os
import stat
import sys
import tarfile
from pathlib import Path, PurePosixPath

CRITICAL = (
    '.', 'init', 'system', 'system/bin/init', 'system/bin/bootstrap/linker64',
    'system/framework/services.jar', 'system/bin/run-as', 'system/build.prop',
    'vendor', 'vendor/lib64/hw/gralloc.gbm.so', 'vendor/build.prop',
    'product', 'system/product', 'system_ext', 'system/system_ext', 'system_dlkm',
)
EXCLUDED = {'lost+found', 'vendor/lost+found', 'system_dlkm/lost+found'}


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def file_hash(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def inventory(root, destination):
    entries = {}
    pending = [root]
    while pending:
        path = pending.pop()
        name = path.relative_to(root).as_posix()
        if name in EXCLUDED:
            continue
        info = path.lstat()
        require(stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode),
                f'지원하지 않는 파일 유형입니다: {name}')
        attrs = {key: os.getxattr(path, key, follow_symlinks=False).hex()
                 for key in os.listxattr(path, follow_symlinks=False)}
        entry = dict(uid=info.st_uid, gid=info.st_gid, mode=stat.S_IMODE(info.st_mode),
                     type=stat.S_IFMT(info.st_mode), xattrs=attrs)
        if stat.S_ISLNK(info.st_mode):
            entry['link'] = os.readlink(path)
        elif stat.S_ISREG(info.st_mode):
            entry['size'] = info.st_size
            if name in CRITICAL:
                entry['sha256'] = file_hash(path)
        else:
            pending.extend(sorted(path.iterdir(), reverse=True))
        entries[name] = entry
    require(all(path in entries for path in CRITICAL), 'rootfs 필수 파일 또는 경로가 누락되었습니다.')
    require(entries['init'].get('link') == '/system/bin/init', '/init 링크 대상이 예상과 다릅니다.')
    require(entries['product'].get('link') == '/system/product', '/product 링크가 예상과 다릅니다.')
    require(entries['system_ext'].get('link') == '/system/system_ext', '/system_ext 링크가 예상과 다릅니다.')
    require(entries['system/bin/run-as']['xattrs'].get('security.capability'), 'run-as capability가 없습니다.')
    document = dict(schema_version=1, entry_count=len(entries), critical=list(CRITICAL), entries=entries)
    (destination / 'rootfs-metadata.json').write_text(json.dumps(document, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f'rootfs 경로 {len(entries)}개의 소유자·모드·링크·xattr를 기록했습니다.')


def normalize_name(name):
    path = PurePosixPath(name)
    require(not path.is_absolute() and '..' not in path.parts, f'안전하지 않은 tar 경로입니다: {name}')
    return path.as_posix()


def verify_tar(archive, metadata):
    expected = metadata['entries']
    seen = set()
    with tarfile.open(archive, 'r|*') as stream:
        for member in stream:
            name = normalize_name(member.name)
            require(name not in seen and name in expected, f'중복 또는 예상하지 못한 tar 경로입니다: {name}')
            seen.add(name)
            item = expected[name]
            require((member.uid, member.gid, member.mode) == (item['uid'], item['gid'], item['mode']),
                    f'tar 소유자 또는 모드가 다릅니다: {name}')
            if item['type'] == stat.S_IFLNK:
                require(member.issym() and member.linkname == item['link'], f'tar 심볼릭 링크가 다릅니다: {name}')
            elif item['type'] == stat.S_IFDIR:
                require(member.isdir(), f'tar 디렉터리 유형이 다릅니다: {name}')
            else:
                require(member.isfile() or member.islnk(), f'tar 파일 유형이 다릅니다: {name}')
                if member.islnk():
                    target = normalize_name(member.linkname)
                    require(target in expected and expected[target]['type'] == stat.S_IFREG,
                            f'tar hardlink 대상이 잘못되었습니다: {name}')
                else:
                    require(member.size == item['size'], f'tar 파일 크기가 다릅니다: {name}')
                if 'sha256' in item:
                    digest = hashlib.sha256()
                    payload = stream.extractfile(member)
                    require(payload is not None, f'tar 파일을 읽을 수 없습니다: {name}')
                    for block in iter(lambda: payload.read(4 * 1024 * 1024), b''):
                        digest.update(block)
                    require(digest.hexdigest() == item['sha256'], f'tar 파일 내용이 다릅니다: {name}')
            for key, value in item['xattrs'].items():
                raw = member.pax_headers.get(f'SCHILY.xattr.{key}')
                require(raw is not None and raw.encode('utf-8', 'surrogateescape').hex() == value,
                        f'tar xattr가 다릅니다: {name}: {key}')
    require(seen == set(expected), 'tar에서 일부 rootfs 경로가 누락되었습니다.')
    print(f'tar 경로 {len(seen)}개의 메타데이터와 필수 파일 해시를 검증했습니다.')
    return len(seen)


def main():
    if len(sys.argv) != 4:
        raise RuntimeError('inventory ROOT DEST 또는 verify TAR METADATA 인자가 필요합니다.')
    if sys.argv[1] == 'inventory':
        inventory(Path(sys.argv[2]), Path(sys.argv[3]))
    elif sys.argv[1] == 'verify':
        verify_tar(Path(sys.argv[2]), json.loads(Path(sys.argv[3]).read_text(encoding='utf-8')))
    else:
        raise RuntimeError('지원하지 않는 메타데이터 명령입니다.')


if __name__ == '__main__':
    main()
