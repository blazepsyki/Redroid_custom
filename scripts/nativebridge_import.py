#!/usr/bin/env python3
"""Google SDK API 35 시스템 이미지에서 Native Bridge 파일을 읽기 전용으로 추출하고 해시를 기록합니다.

추출한 바이너리는 Google 독점 파일이므로 로컬 cache에만 두고 Git이나 dist에 넣지 않습니다.
binfmt_misc 등록(rc, 핸들러)은 호스트 커널 전역에 영향을 줄 수 있어 제외합니다.
등록이 가리키는 program runner만 포함하므로, 호스트에 이미 등록된 경우에만 ARM ELF 직접 실행이 동작합니다.
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from phase0 import PhaseError, ROOT, require, write_json

PACKAGE = dict(path='system-images;android-35;google_apis;x86_64', revision=9, archive='x86_64-35_r09.zip',
               url='https://dl.google.com/android/repository/sys-img/google_apis/x86_64-35_r09.zip',
               size=1738815903, sha1='0103e6dab21290c4b9d16550a3ce99476f884eef', license='android-sdk-license')
LOCK = ROOT / 'config/nativebridge-lock.json'
DEBUGFS = shutil.which('debugfs') or '/usr/sbin/debugfs'
REQUIRED = ('lib64/libndk_translation.so', 'lib64/libberberis_exec_region.so',
            'lib64/arm64/libc.so', 'lib64/arm64/ld-android.so', 'bin/arm64/linker64', 'etc/ld.config.arm64.txt')
DIRECTORIES = ('lib64/arm64', 'bin/arm64')
SINGLE = ('lib64/libndk_translation.so', 'lib64/libberberis_exec_region.so',
          'etc/ld.config.arm64.txt', 'etc/cpuinfo.arm64.txt', 'bin/ndk_translation_program_runner_binfmt_misc_arm64')
PROXY = re.compile(r'libndk_translation_proxy_[A-Za-z0-9_]+\.so')
EXCLUDED = ('etc/init/ndk_translation.rc', 'etc/binfmt_misc/')


def cache_dir():
    return Path(os.environ.get('REDROID_CACHE_DIR', str(Path.home() / '.cache/custom-redroid'))).resolve() / 'nativebridge'


def files_dir():
    return cache_dir() / f'files-r{PACKAGE["revision"]}'


def digest(path, algorithm='sha256'):
    value = hashlib.new(algorithm)
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def debugfs(image, request):
    result = subprocess.run([DEBUGFS, '-R', request, str(image)], capture_output=True, text=True)
    require(result.returncode == 0, f'debugfs 실패: {request}\n{result.stderr}')
    return result.stdout


def listing(image, directory):
    entries = {}
    for line in debugfs(image, f'ls -p /system/{directory}').splitlines():
        fields = line.strip().split('/')
        if len(fields) < 7 or fields[5] in ('', '.', '..'):
            continue
        entries[fields[5]] = int(fields[2], 8)
    return entries


def extract(system_image, output):
    selected = {}
    lib64 = listing(system_image, 'lib64')
    for name, mode in lib64.items():
        if PROXY.fullmatch(name):
            selected[f'lib64/{name}'] = mode
    for relative in SINGLE:
        parent, name = relative.rsplit('/', 1)
        entries = lib64 if parent == 'lib64' else listing(system_image, parent)
        require(name in entries, f'필수 파일이 없습니다: /system/{relative}')
        selected[relative] = entries[name]
    for directory in DIRECTORIES:
        entries = listing(system_image, directory)
        require(entries, f'디렉터리가 비어 있습니다: /system/{directory}')
        for name, mode in entries.items():
            selected[f'{directory}/{name}'] = mode
    for relative in REQUIRED:
        require(relative in selected, f'필수 파일이 없습니다: /system/{relative}')
    files = []
    for relative, mode in sorted(selected.items()):
        require(mode & 0o170000 == 0o100000, f'일반 파일이 아닙니다: /system/{relative}')
        require(not any(relative.startswith(item) or relative == item for item in EXCLUDED), f'제외 대상입니다: {relative}')
        target = output / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        debugfs(system_image, f'dump /system/{relative} {target}')
        require(target.is_file(), f'추출 실패: {relative}')
        files.append(dict(path=relative, mode=f'{mode & 0o7777:04o}', size=target.stat().st_size, sha256=digest(target)))
    return files


def verify_files(lock=None):
    lock = lock or json.loads(LOCK.read_text(encoding='utf-8'))
    base = files_dir()
    for item in lock['files']:
        path = base / item['path']
        require(path.is_file() and digest(path) == item['sha256'], f'Native Bridge 파일이 기록과 다릅니다: {item["path"]}')
    return base


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', nargs='?', help=f'{PACKAGE["archive"]} 경로')
    parser.add_argument('--verify', action='store_true', help='기록된 해시로 로컬 파일만 검사합니다.')
    args = parser.parse_args()
    try:
        if args.verify:
            print(f'Native Bridge 파일 검증 완료: {verify_files()}')
            return 0
        archive = Path(args.archive or cache_dir() / PACKAGE['archive']).resolve()
        require(archive.is_file() and archive.stat().st_size == PACKAGE['size'], f'SDK archive 크기가 다릅니다: {archive}')
        require(digest(archive, 'sha1') == PACKAGE['sha1'], 'SDK archive SHA-1이 저장소 목록과 다릅니다.')
        output = files_dir()
        require(not output.exists(), f'기존 추출 결과는 덮어쓰지 않습니다: {output}')
        scripts = Path(__file__).resolve().parent
        with tempfile.TemporaryDirectory(prefix='nativebridge-', dir=cache_dir()) as directory:
            work = Path(directory)
            with zipfile.ZipFile(archive) as bundle:
                member = bundle.getinfo('x86_64/system.img')
                with bundle.open(member) as source, (work / 'disk.img').open('xb') as target:
                    shutil.copyfileobj(source, target, 4 * 1024 * 1024)
                properties = bundle.read('x86_64/source.properties').decode()
            located = subprocess.check_output([sys.executable, '-I', scripts / 'sdk-locate-filesystem.py', work / 'disk.img'], text=True)
            kind, offset, length = located.split()
            require(kind == 'super', f'예상한 super 구조가 아닙니다: {kind}')
            subprocess.run([sys.executable, '-I', scripts / 'sdk-unpack-super.py', work / 'disk.img', offset, length, work / 'lp'], check=True)
            system_image = work / 'lp/system.img'
            build_prop = debugfs(system_image, 'cat /system/build.prop')
            staging = work / 'files'
            files = extract(system_image, staging)
            output.parent.mkdir(parents=True, exist_ok=True)
            staging.rename(output)
        lock = dict(package=PACKAGE, archive_sha256=digest(archive), source_properties=properties.strip().splitlines(),
                    source_fingerprint=re.search(r'^ro\.system\.build\.fingerprint=(.*)$', build_prop, re.M).group(1),
                    source_native_bridge=re.search(r'^ro\.dalvik\.vm\.native\.bridge=(.*)$', build_prop, re.M).group(1),
                    excluded=list(EXCLUDED), install_root='system', symlinks={'lib64/libnb.so': 'libndk_translation.so'},
                    files=files, extracted_at=datetime.now(timezone.utc).isoformat(),
                    redistribution='forbidden; local build only')
        write_json(LOCK, lock)
        print(f'Native Bridge 파일 {len(files)}개 추출: {output}')
    except (PhaseError, OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        print(f'실패: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
