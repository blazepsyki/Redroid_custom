#!/usr/bin/env python3
"""microG APK와 Play 스토어 APK의 패키지·버전·서명을 검사하고 config/microg-apk-lock.json에 고정합니다.

서명은 Android SDK apksigner로 검사합니다. GmsCore는 공식 microG 인증서, Play 스토어는 서명 계보가
Google 원래 인증서에서 시작해야 하며, 다르면 기록하지 않습니다.
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from phase0 import PhaseError, ROOT, require, write_json

LOCK = ROOT / 'config/microg-apk-lock.json'
INPUTS = ROOT / 'inputs/microg'
# GmsCore는 위치 제공자·FCM에, Play 스토어는 앱 설치에 privileged 권한이 필요해 둘 다 priv-app입니다.
# microG Companion(FakeStore)은 Play 스토어와 패키지 이름이 같아 함께 둘 수 없으므로 Play 스토어를 넣습니다.
# 두 원본은 dex·JNI가 압축되어 있어 Soong의 preprocessed 검사를 건너뛰어야 원본 서명을 유지합니다.
APPS = {
    'com.google.android.gms': dict(module='GmsCore', privileged=True, skip_preprocessed_apk_checks=True, signer='microg',
                                   disabled_components=[]),
    # VpaService(기본 앱 자동 설치)는 Android 15에서 systemExempted 포그라운드 서비스 조건(정확한 알람 권한)을
    # 만족하지 못해 Play 스토어를 반복 종료시키므로, sysconfig의 component-override로 처음부터 끕니다.
    'com.android.vending': dict(module='Phonesky', privileged=True, skip_preprocessed_apk_checks=True, signer='google',
                                disabled_components=['com.google.android.finsky.setup.VpaService']),
}
ALLOWED_PRIVILEGED = {
    'com.google.android.gms': [
        'android.permission.CHANGE_DEVICE_IDLE_TEMP_WHITELIST',
        'android.permission.INSTALL_LOCATION_PROVIDER',
        'android.permission.START_ACTIVITIES_FROM_BACKGROUND',
        'android.permission.UPDATE_APP_OPS_STATS',
    ],
    # 앱 설치·삭제, 설치 전 공간 확보, microG 계정 조회만 허용합니다. MANAGE_USERS가 없으면 시작할 때
    # 사용자 목록 조회에서, INTERACT_ACROSS_USERS가 없으면 첫 부팅 작업의 공유 라이브러리 조회에서 종료됩니다.
    # PACKAGE_VERIFICATION_AGENT 등은 거부해 Play 스토어가 다른 설치 경로를 검증·차단하지 않게 합니다.
    'com.android.vending': [
        'android.permission.CLEAR_APP_CACHE',
        'android.permission.DELETE_PACKAGES',
        'android.permission.GET_ACCOUNTS_PRIVILEGED',
        'android.permission.INSTALL_PACKAGES',
        'android.permission.INTERACT_ACROSS_USERS',
        'android.permission.MANAGE_USERS',
        'com.android.permission.USE_INSTALLER_V2',
    ],
}


def tools():
    source = Path(os.environ.get('REDROID_SOURCE_DIR', str(Path.home() / 'work/redroid-aosp15')))
    java = source / 'prebuilts/jdk/jdk21/linux-x86/bin/java'
    apksigner = source / 'prebuilts/sdk/tools/linux/lib/apksigner.jar'
    aapt2 = source / 'out/host/linux-x86/bin/aapt2'
    for path in (java, apksigner, aapt2):
        require(path.is_file(), f'도구가 없습니다: {path}')
    return java, apksigner, aapt2


def digest(path):
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def inspect(path):
    java, apksigner, aapt2 = tools()
    verify = subprocess.run([java, '-jar', apksigner, 'verify', '--verbose', '--print-certs', path],
                            capture_output=True, text=True)
    require(verify.returncode == 0, f'apksigner 검증 실패: {path.name}\n{verify.stderr}')
    signers = re.findall(r'^Signer #\d+ certificate SHA-256 digest: ([0-9a-f]{64})$', verify.stdout, re.M)
    # v3 키 교체 계보입니다. 계보가 없는 APK는 빈 목록입니다.
    lineage = subprocess.run([java, '-jar', apksigner, 'lineage', '--in', path, '--print-certs'],
                             capture_output=True, text=True)
    lineage = re.findall(r'^Signer #\d+ in lineage certificate SHA-256 digest: ([0-9a-f]{64})$', lineage.stdout, re.M)
    schemes = sorted(set(re.findall(r'^Verified using (v[\d.]+) scheme \(.*\): true$', verify.stdout, re.M)))
    badging = subprocess.check_output([aapt2, 'dump', 'badging', path], text=True)
    package = re.search(r"^package: name='([^']+)' versionCode='(\d+)' versionName='([^']+)'", badging, re.M)
    require(package, f'패키지 정보를 읽을 수 없습니다: {path.name}')
    permissions = subprocess.check_output([aapt2, 'dump', 'permissions', path], text=True)
    requested = sorted(set(re.findall(r"^uses-permission: name='([^']+)'", permissions, re.M)))
    return dict(package=package.group(1), version_code=int(package.group(2)), version_name=package.group(3),
                signers=signers, lineage=lineage, schemes=schemes, requested_permissions=requested)


def platform_privileged_permissions():
    """실제 빌드한 framework-res.apk에서 signature|privileged 권한 이름을 읽습니다."""
    _, _, aapt2 = tools()
    source = Path(os.environ.get('REDROID_SOURCE_DIR', str(Path.home() / 'work/redroid-aosp15')))
    framework = source / 'out/target/product/redroid_x86_64_only/system/framework/framework-res.apk'
    require(framework.is_file(), f'framework-res.apk가 없습니다. 먼저 제품을 빌드해 주세요: {framework}')
    tree = subprocess.check_output([aapt2, 'dump', 'xmltree', framework, '--file', 'AndroidManifest.xml'], text=True)
    result = set()
    for block in re.split(r'\n\s*E: ', tree):
        if not block.startswith('permission '):
            continue
        name = re.search(r'android:name\(0x01010003\)="([^"]+)"', block)
        level = re.search(r'android:protectionLevel\(0x01010009\)=(0x[0-9a-f]+)', block)
        if name and level and int(level.group(1), 16) & 0xf == 2 and int(level.group(1), 16) & 0x10:
            result.add(name.group(1))
    require(len(result) > 100, 'framework의 privileged 권한 목록을 읽지 못했습니다.')
    return result


def signer_ok(info, kind):
    """microG는 공식 인증서 하나, Google은 현재 서명이 Google 원래 인증서이거나 그 인증서에서 시작한 계보여야 합니다."""
    source = json.loads((ROOT / 'config/microg-source-lock.json').read_text(encoding='utf-8'))
    if kind == 'microg':
        return info['signers'] == [source['real_certificate_sha256']]
    google = source['compatibility_certificate_sha256']
    lineage = info.get('lineage') or []
    return len(info['signers']) == 1 and (info['signers'] == [google] or
                                          (lineage[:1] == [google] and lineage[-1:] == info['signers']))


def verify_apks(lock=None):
    lock = lock or json.loads(LOCK.read_text(encoding='utf-8'))
    for item in lock['apps']:
        path = INPUTS / item['file']
        require(path.is_file() and digest(path) == item['sha256'], f'APK가 기록과 다릅니다: {item["file"]}')
        require(signer_ok(item, item['signer']), f'기대한 서명이 아닙니다: {item["file"]}')
    return lock


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verify', action='store_true', help='기록된 해시와 서명만 검사합니다.')
    args = parser.parse_args()
    try:
        if args.verify:
            verify_apks()
            print('APK 검증 완료')
            return 0
        official = json.loads((ROOT / 'config/microg-source-lock.json').read_text(encoding='utf-8'))['real_certificate_sha256']
        apps = []
        found = set()
        privileged = platform_privileged_permissions()
        for path in sorted(INPUTS.glob('*.apk')):
            info = inspect(path)
            require(info['package'] in APPS, f'예상하지 않은 패키지입니다: {path.name} ({info["package"]})')
            require(info['package'] not in found, f'같은 패키지의 APK가 여러 개입니다: {info["package"]}')
            require(signer_ok(info, APPS[info['package']]['signer']), f'기대한 서명과 다릅니다. 패키징을 중단합니다: {path.name}')
            found.add(info['package'])
            requested_privileged = [name for name in info['requested_permissions'] if name in privileged]
            allowed = ALLOWED_PRIVILEGED.get(info['package'], [])
            require(set(allowed) <= set(requested_privileged), f'요청하지 않은 권한을 허용하려 합니다: {info["package"]}')
            apps.append(dict(file=path.name, sha256=digest(path), size=path.stat().st_size, **APPS[info['package']], **info,
                             requested_privileged_permissions=requested_privileged,
                             allowed_privileged_permissions=allowed))
        require(found == set(APPS), f'필요한 APK가 없습니다: {sorted(set(APPS) - found)}')
        write_json(LOCK, dict(apps=apps, official_certificate_sha256=official, privapp_permissions_mode='log',
                              recorded_at=datetime.now(timezone.utc).isoformat()))
        print(f'APK {len(apps)}개 기록: {LOCK}')
    except (PhaseError, OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        print(f'실패: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
