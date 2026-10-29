#!/usr/bin/env python3
"""사용자가 제공한 기본 설치 APK의 패키지·서명·해시를 고정하고 검사합니다."""
import argparse
import hashlib
import json
import re
import subprocess
import sys
import zipfile
from datetime import datetime, timezone

from microg_import import digest, inspect, tools
from phase0 import PhaseError, ROOT, require, write_json

INPUTS = ROOT / 'inputs/apks'
LOCK = ROOT / 'config/apps-lock.json'
APPS = {
    'app.lawnchair': dict(module='CustomLawnchair', signers=['747c3645b357258b2e23e851e53c96747fe0add007e5ba2cd97e8c85572e4dc5'],
                          license='Apache-2.0', source='https://github.com/LawnchairLauncher/lawnchair', splits=False),
    # Play 스토어에서 받은 x86_64 base와 화면 밀도 분할 APK입니다. base가 밀도 분할을 필수로 요구하므로
    # 두 파일을 같은 시스템 앱 폴더에 둡니다. 서명은 Android 13 이상과 이전 버전용 키가 나뉜 v3.1 방식입니다.
    'com.google.android.inputmethod.latin': dict(
        module='CustomGboard', signers=['7ce83c1b71f3d572fed04c8d40c5cb10ff75e6d87d9df6fbd53f0468c2905053',
                                        'f0fd6c5b410f25cb25c3b53346c8972fae30f8ee7411df910480ad6b2d60db83'],
        license='Google 독점', source='Google Play 스토어', splits=True),
    # x86_64 APK를 씁니다. ARM64 번역 시험은 같은 서명의 ARM64 APK를 업데이트로 설치합니다.
    'org.mozilla.firefox': dict(module='CustomFirefox', signers=['a78b62a5165b4494b2fead9e76a280d22d937fee6251aece599446b2ea319b04'],
                                license='MPL-2.0', source='https://archive.mozilla.org/pub/fenix/releases/', splits=False),
}


def validate_app(app):
    expected = APPS.get(app['package'])
    require(expected is not None, '기본 설치 대상이 아닌 패키지입니다.')
    require(app['module'] == expected['module'] and app['signers'] == expected['signers'],
            f'기본 설치 APK의 모듈 또는 서명이 다릅니다: {app["package"]}')
    require(app.get('privileged') is False, '추가 APK는 일반 시스템 앱으로 설치해야 합니다.')
    require(type(app.get('skip_preprocessed_apk_checks')) is bool, 'APK 압축·정렬 검사 기록이 없습니다.')
    require(type(app.get('min_sdk')) is int and app['min_sdk'] <= 35, 'Android 15에서 설치할 수 없는 APK입니다.')
    require(type(app.get('extract_native_libs')) is bool and isinstance(app.get('native_files'), list),
            'APK 네이티브 라이브러리 검사 기록이 없습니다.')
    splits = app.get('splits', [])
    require(bool(splits) == expected['splits'], f'분할 APK 구성이 다릅니다: {app["package"]}')
    for split in splits:
        require(re.fullmatch(r'config\.[a-z0-9_]+', split['name']) is not None, '분할 APK 이름이 잘못되었습니다.')


def input_file(name, expected_sha256):
    path = INPUTS / name
    require(path.resolve().is_relative_to(INPUTS.resolve()) and not path.is_symlink(), 'APK 입력 경로가 잘못되었습니다.')
    require(path.is_file() and digest(path) == expected_sha256, f'APK가 기록과 다릅니다: {name}')


def verify_apks():
    lock = json.loads(LOCK.read_text(encoding='utf-8'))
    require(len(lock['apps']) == len(APPS) and {app['package'] for app in lock['apps']} == set(APPS),
            '기본 설치 APK 목록이 다릅니다.')
    for app in lock['apps']:
        validate_app(app)
        input_file(app['file'], app['sha256'])
        for split in app.get('splits', []):
            input_file(split['file'], split['sha256'])
    return lock


def split_name(aapt2, path):
    badging = subprocess.check_output([aapt2, 'dump', 'badging', path], text=True)
    found = re.search(r"^package: name='[^']+'.* split='([^']+)'", badging, re.M)
    return found.group(1) if found else None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verify', action='store_true', help='고정된 APK 파일만 검사합니다.')
    args = parser.parse_args()
    try:
        if args.verify:
            lock = verify_apks()
        else:
            _, _, aapt2 = tools()
            apps = []
            splits = {}
            for path in sorted(INPUTS.glob('*.apk')):
                info = inspect(path)
                expected = APPS.get(info['package'])
                require(expected is not None, f'예상하지 않은 APK입니다: {path.name}')
                split = split_name(aapt2, path)
                if split:
                    require(info['signers'] == expected['signers'], f'분할 APK의 서명이 다릅니다: {path.name}')
                    splits.setdefault(info['package'], []).append(
                        dict(name=split, file=path.name, sha256=digest(path), size=path.stat().st_size,
                             version_code=info['version_code']))
                    continue
                with zipfile.ZipFile(path) as archive:
                    abis = sorted({name.split('/')[1] for name in archive.namelist()
                                   if name.startswith('lib/') and name.endswith('.so')})
                    compressed_jni = any(item.filename.startswith('lib/') and item.filename.endswith('.so')
                                         and item.compress_type != zipfile.ZIP_STORED for item in archive.infolist())
                manifest = subprocess.check_output([aapt2, 'dump', 'xmltree', path, '--file', 'AndroidManifest.xml'], text=True)
                minimum = re.search(r'android:minSdkVersion\([^)]*\)=(0x[0-9a-fA-F]+|[0-9]+)', manifest)
                target = re.search(r'android:targetSdkVersion\([^)]*\)=(0x[0-9a-fA-F]+|[0-9]+)', manifest)
                require(minimum is not None and target is not None, 'APK의 SDK 요구 버전을 읽지 못했습니다.')
                extract = re.search(r'android:extractNativeLibs\([^)]*\)=(true|false)', manifest)
                extract_native_libs = extract is None or extract.group(1) == 'true'
                native_files = []
                if extract_native_libs:
                    with zipfile.ZipFile(path) as archive:
                        for name in sorted(archive.namelist()):
                            if not name.startswith('lib/x86_64/') or not name.endswith('.so'):
                                continue
                            require(len(name.split('/')) == 3, '예상하지 않은 네이티브 라이브러리 경로입니다.')
                            data = archive.read(name)
                            require(data[:5] == b'\x7fELF\x02' and data[5] == 1 and int.from_bytes(data[18:20], 'little') == 62,
                                    'x86_64 ELF 라이브러리가 아닙니다.')
                            native_files.append(dict(member=name, sha256=hashlib.sha256(data).hexdigest(), size=len(data)))
                zipalign = aapt2.parent / 'zipalign'
                require(zipalign.is_file(), 'zipalign이 없습니다. AOSP 호스트 도구를 먼저 빌드해 주세요.')
                aligned = subprocess.run([zipalign, '-c', '-p', '4', path], capture_output=True).returncode == 0
                require(not abis or 'x86_64' in abis, '기본 설치 APK에 x86_64 라이브러리가 없습니다.')
                app = dict(info, module=expected['module'], privileged=False, file=path.name,
                           sha256=digest(path), size=path.stat().st_size, abis=abis,
                           license=expected['license'], source=expected['source'], provenance='user-supplied',
                           skip_preprocessed_apk_checks=compressed_jni or not aligned,
                           min_sdk=int(minimum.group(1), 0), target_sdk=int(target.group(1), 0),
                           extract_native_libs=extract_native_libs, native_files=native_files)
                apps.append(app)
            for app in apps:
                app['splits'] = sorted(splits.pop(app['package'], []), key=lambda split: split['name'])
                require(all(split.pop('version_code') == app['version_code'] for split in app['splits']),
                        f'분할 APK와 base의 버전이 다릅니다: {app["package"]}')
                validate_app(app)
            require(not splits, f'base APK 없이 분할 APK만 있습니다: {sorted(splits)}')
            require(len(apps) == len(APPS) and {app['package'] for app in apps} == set(APPS),
                    'Lawnchair, Gboard, Firefox APK가 각각 하나씩 필요합니다.')
            lock = dict(schema_version=1, apps=apps, recorded_at=datetime.now(timezone.utc).isoformat())
            write_json(LOCK, lock)
        print(f'기본 설치 APK {len(lock["apps"])}개 검사 완료')
    except (PhaseError, OSError, ValueError, KeyError, zipfile.BadZipFile, subprocess.CalledProcessError) as error:
        print(f'실패: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
