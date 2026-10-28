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
    'app.lawnchair': dict(module='CustomLawnchair', signer='747c3645b357258b2e23e851e53c96747fe0add007e5ba2cd97e8c85572e4dc5',
                          license='Apache-2.0', source='https://github.com/LawnchairLauncher/lawnchair'),
    'org.fcitx.fcitx5.android': dict(module='CustomFcitx5', signer='e4db1e9edff13629d07de4bbf8165fe9bd8557ab55092672da8e40dbe484ecd7',
                                    license='LGPL-2.1 및 포함 엔진별 고지 참조', source='https://github.com/fcitx5-android/fcitx5-android'),
    'org.fcitx.fcitx5.android.plugin.hangul': dict(module='CustomFcitx5Hangul', signer='e4db1e9edff13629d07de4bbf8165fe9bd8557ab55092672da8e40dbe484ecd7',
                                                license='프로젝트 LICENSE 및 포함 엔진별 고지 참조', source='https://github.com/fcitx5-android/fcitx5-android/tree/master/plugin/hangul'),
}


def validate_app(app):
    expected = APPS.get(app['package'])
    require(expected is not None, '기본 설치 대상이 아닌 패키지입니다.')
    require(app['module'] == expected['module'] and app['signers'] == [expected['signer']],
            f'기본 설치 APK의 모듈 또는 서명이 다릅니다: {app["package"]}')
    require(app.get('privileged') is False, '추가 APK는 일반 시스템 앱으로 설치해야 합니다.')
    require(type(app.get('skip_preprocessed_apk_checks')) is bool, 'APK 압축·정렬 검사 기록이 없습니다.')
    require(type(app.get('min_sdk')) is int and app['min_sdk'] <= 35, 'Android 15에서 설치할 수 없는 APK입니다.')
    require(type(app.get('extract_native_libs')) is bool and isinstance(app.get('native_files'), list),
            'APK 네이티브 라이브러리 검사 기록이 없습니다.')


def verify_apks():
    lock = json.loads(LOCK.read_text(encoding='utf-8'))
    require(len(lock['apps']) == len(APPS) and {app['package'] for app in lock['apps']} == set(APPS),
            '기본 설치 APK 목록이 다릅니다.')
    for app in lock['apps']:
        validate_app(app)
        path = INPUTS / app['file']
        require(path.resolve().is_relative_to(INPUTS.resolve()) and not path.is_symlink(), 'APK 입력 경로가 잘못되었습니다.')
        require(path.is_file() and digest(path) == app['sha256'], f'APK가 기록과 다릅니다: {app["file"]}')
    validate_fcitx_pair(lock['apps'])
    return lock


def validate_fcitx_pair(apps):
    by_package = {app['package']: app for app in apps}
    core = by_package['org.fcitx.fcitx5.android']
    plugin = by_package['org.fcitx.fcitx5.android.plugin.hangul']
    require(core['signers'] == plugin['signers'], 'Fcitx5 본체와 Hangul 플러그인의 서명이 다릅니다.')
    require((core['version_code'], core['version_name']) == (plugin['version_code'], plugin['version_name']),
            'Fcitx5 본체와 Hangul 플러그인의 버전이 다릅니다.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verify', action='store_true', help='고정된 APK 파일만 검사합니다.')
    args = parser.parse_args()
    try:
        if args.verify:
            lock = verify_apks()
        else:
            apps = []
            for path in sorted(INPUTS.glob('*.apk')):
                info = inspect(path)
                expected = APPS.get(info['package'])
                require(expected is not None, f'예상하지 않은 APK입니다: {path.name}')
                with zipfile.ZipFile(path) as archive:
                    abis = sorted({name.split('/')[1] for name in archive.namelist()
                                   if name.startswith('lib/') and name.endswith('.so')})
                    compressed_jni = any(item.filename.startswith('lib/') and item.filename.endswith('.so')
                                         and item.compress_type != zipfile.ZIP_STORED for item in archive.infolist())
                _, _, aapt2 = tools()
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
                validate_app(app)
                apps.append(app)
            require(len(apps) == len(APPS) and {app['package'] for app in apps} == set(APPS),
                'Lawnchair, Fcitx5 본체, Hangul 플러그인 APK가 각각 하나씩 필요합니다.')
            validate_fcitx_pair(apps)
            lock = dict(schema_version=1, apps=apps, recorded_at=datetime.now(timezone.utc).isoformat())
            write_json(LOCK, lock)
        print(f'기본 설치 APK {len(lock["apps"])}개 검사 완료')
    except (PhaseError, OSError, ValueError, KeyError, zipfile.BadZipFile, subprocess.CalledProcessError) as error:
        print(f'실패: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
