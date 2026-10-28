#!/usr/bin/env python3
"""최신 custom 패키지의 provenance·SBOM·라이선스 목록·검증 결과를 모아 최종 보고서를 만듭니다.

결과는 dist/report-<package_id>/(SBOM, NOTICE, 제3자 구성요소 목록, SHA256SUMS)와 docs/reports/final.md입니다.
입력 기록은 읽기만 하며, 시험하지 않은 항목은 NOT TESTED로 남깁니다.
"""
import argparse
import json
import os
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

from phase0 import PhaseError, ROOT, require, sha256
from build_identity import BUILD_IDENTITY


def load(relative):
    return json.loads((ROOT / relative).read_text(encoding='utf-8'))


def result(flag):
    return 'PASS' if flag else 'FAIL'


def negative_test(runtime_dir, specified, image_id, official_signer):
    path = ROOT / specified if specified is not None else runtime_dir / 'microg-signature-test.json'
    if specified is None and not path.exists():
        return dict(status='NOT TESTED', detail='현재 이미지의 부정 시험 기록 없음', path=None, data=None)
    require(path.is_file(), f'부정 시험 기록 파일이 없습니다: {path}')
    data = path.read_bytes()
    record = json.loads(data.decode('utf-8'))
    require(isinstance(record, dict) and type(record.get('schema_version')) is int
            and record['schema_version'] == 1, '부정 시험 기록의 schema_version은 1이어야 합니다.')
    require(record.get('image_id') == image_id, '부정 시험 기록의 이미지 ID가 최신 패키지와 다릅니다.')
    values = {}
    for name in ('positive_selfcheck', 'negative_resigned_gmscore', 'restored_official_selfcheck'):
        section = record.get(name)
        require(isinstance(section, dict), f'부정 시험 기록에 {name} 결과가 없습니다.')
        for field in ('system_spoofs_signature', 'services_correct_signature'):
            require(type(section.get(field)) is bool, f'{name}.{field}는 boolean이어야 합니다.')
        values[name] = section
    negative = values['negative_resigned_gmscore']
    for field in ('signer_sha256', 'apk_sha256'):
        require(isinstance(negative.get(field), str)
                and re.fullmatch(r'[0-9a-f]{64}', negative[field]), f'부정 시험의 {field} 형식이 다릅니다.')
    require(negative['signer_sha256'] != official_signer, '부정 시험 APK가 공식 microG 인증서로 서명되어 있습니다.')
    controls = all(values[name][field] for name in ('positive_selfcheck', 'restored_official_selfcheck')
                   for field in ('system_spoofs_signature', 'services_correct_signature'))
    rejected = not negative['system_spoofs_signature'] and not negative['services_correct_signature']
    return dict(status=result(controls and rejected), detail=f'증거 JSON SHA-256 `{sha256(data)}`',
                path=path, data=data)


def third_party(microg, bridge, apps=None):
    rows = ['| 구성요소 | 버전·출처 | 라이선스 | 재배포 |', '|---|---|---|---|',
            '| AOSP·redroid 소스 빌드 결과 | `android-15.0.0_r36`, redroid local_manifests·patches | 모듈별(Apache-2.0 등), `NOTICE.xml.gz`와 SBOM 참고 | 각 라이선스 조건에 따름 |',
            f'| ARM64 Translation(`libndk_translation` 등 {len(bridge["files"])}개 파일) | Google SDK `{bridge["package"]["path"]}` r{bridge["package"]["revision"]} | {bridge["package"]["license"]} | 금지. 로컬 빌드 전용 |']
    for app in microg['apps']:
        if app['signer'] == 'microg':
            rows.append(f'| microG Services(`{app["package"]}`) | {app["version_name"]}, 공식 서명 `{app["signers"][0][:8]}…` | Apache-2.0 | 가능. 이 이미지는 개인용 |')
        else:
            rows.append(f'| Google Play 스토어(`{app["package"]}`) | {app["version_name"]}, Google 서명 계보 `{app["lineage"][0][:8]}…` | Google 독점 | 금지. 로컬 빌드 전용 |')
    for app in (apps or {}).get('apps', []):
        rows.append(f'| 기본 설치 앱(`{app["package"]}`) | {app["version_name"]}, 사용자 제공 APK | {app["license"]} | 해당 라이선스 조건에 따름. APK는 Git 제외 |')
    rows.append('| ARM64 Firefox(시험용, 이미지 미포함) | `fenix-159.0.multi.android-arm64-v8a.apk` | MPL-2.0 | 이미지에 넣지 않음 |')
    return rows


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--runtime-dir', required=True, help='최신 이미지의 실행 검증 결과 폴더')
    parser.add_argument('--negative-test', help='현재 이미지의 microG 정상·재서명·복원 시험 JSON. 생략하면 검증 결과 폴더의 microg-signature-test.json을 사용합니다.')
    parser.add_argument('--dry-run', action='store_true', help='입력 기록만 검사하고 파일을 쓰지 않습니다.')
    parser.add_argument('--force', action='store_true', help='기존 report 폴더가 있어도 다시 만듭니다.')
    args = parser.parse_args(argv)
    try:
        package = load('build-logs/custom-package-state.json')
        build = load('build-logs/custom-build-state.json')
        sbom_state = load('build-logs/custom-sbom-state.json')
        lock = load('config/source-lock.json')
        gralloc = load('config/gralloc-source-lock.json')
        bridge = load('config/nativebridge-lock.json')
        microg = load('config/microg-apk-lock.json')
        apps_lock = load('config/apps-lock.json') if (ROOT / 'config/apps-lock.json').is_file() else None
        builder = load('config/builder-lock.json')
        spoof = load('config/microg-source-lock.json')
        runtime_dir = ROOT / args.runtime_dir
        negative = negative_test(runtime_dir, args.negative_test, package['image_id'], spoof['real_certificate_sha256'])
        coldboot = json.loads((runtime_dir / 'coldboot-report.json').read_text(encoding='utf-8'))
        verify = json.loads(sorted(runtime_dir.glob('verify-*.json'))[-1].read_text(encoding='utf-8'))
        selfcheck = (runtime_dir / 'microg-selfcheck.txt').read_text(encoding='utf-8')
        require(package['status'] == 'packaged' and build['status'] == 'built', '패키징된 최신 빌드가 필요합니다.')
        require(package['custom_plan_sha256'] == build['custom_plan_sha256'] == sbom_state['custom_plan_sha256'],
                '패키지·빌드·SBOM의 custom plan이 서로 다릅니다.')
        require(sbom_state['status'] == 'sbom_generated', 'SBOM 생성 기록이 없습니다. 06-build.sh --custom --sbom을 먼저 실행해 주세요.')
        require(verify['image_id'] == package['image_id'], '실행 검증 결과가 최신 이미지와 다릅니다.')
        source = Path(os.environ.get('REDROID_SOURCE_DIR', str(Path.home() / 'work/redroid-aosp15')))
        sbom = source / sbom_state['sbom']['path']
        notice = source / 'out/target/product' / lock['target']['product'] / 'system/etc/NOTICE.xml.gz'
        require(sbom.is_file() and sha256(sbom.read_bytes()) == sbom_state['sbom']['sha256'], f'SBOM이 기록과 다릅니다: {sbom}')
        require(notice.is_file(), f'NOTICE.xml.gz가 없습니다: {notice}')
    except (PhaseError, OSError, ValueError, KeyError, IndexError) as error:
        print(f'실패: {error}', file=sys.stderr)
        return 1

    lines = selfcheck.splitlines()
    def checked(label):
        index = next(i for i, line in enumerate(lines) if line.startswith(repr(label)))
        return 'checked=true' in lines[index + 1]
    spoof_ok = checked('System spoofs signature:')
    services_ok = checked('microG Services has correct signature:')
    vending_ok = checked('microG Companion / Play Store has correct signature:')
    checks = verify['checks']
    firefox = [boot['firefox'] for boot in coldboot]
    gralloc_ok = all(boot['gralloc_load']['passed'] == boot['gralloc_load']['iterations'] and boot['p010_rejections'] > 0
                     and boot['allocator_pid'] == boot['allocator_pid_after'] for boot in coldboot)
    apps = {app['package']: app for app in microg['apps']}
    identity = build.get('build_identity', {})
    identity_status = ('PASS' if all(identity.get(key) == value for key, value in BUILD_IDENTITY.items())
                       and package.get('build_identity') == identity else 'NOT TESTED')
    report_dir = ROOT / 'dist' / f'report-{package["package_id"]}'
    if args.dry_run:
        print(json.dumps(dict(report_dir=str(report_dir), sbom=str(sbom), image_id=package['image_id'],
                              negative_test=negative['status'],
                              negative_test_source=str(negative['path']) if negative['path'] else None), ensure_ascii=False))
        return 0
    require(args.force or not report_dir.exists(), f'이미 있습니다. 다시 만들려면 --force: {report_dir}')
    report_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(sbom, report_dir / 'sbom.spdx.json')
    shutil.copyfile(notice, report_dir / 'NOTICE.xml.gz')
    negative_copy = report_dir / 'microg-signature-test.json'
    if negative['data'] is not None:
        negative_copy.write_bytes(negative['data'])
    else:
        negative_copy.unlink(missing_ok=True)
    (report_dir / 'third-party.md').write_bytes(('\n'.join([
        f'# 제3자 구성요소와 재배포 조건 ({package["package_id"]})', '',
        'AOSP 모듈 전체 목록과 라이선스는 `sbom.spdx.json`(SPDX, `m sbom`)과 `NOTICE.xml.gz`에 있습니다. '
        '아래는 AOSP 밖에서 들여온 바이너리입니다. 독점 바이너리가 들어 있으므로 이 이미지는 개인용이며 공개 레지스트리에 올리지 않습니다.', '',
        *third_party(microg, bridge, apps_lock), '']) + '\n').encode())

    stamp = datetime.now(timezone.utc).isoformat()
    final = f'''# Custom redroid Android 15 Build Report

`scripts/10-collect-report.sh`가 {stamp}에 기록 파일에서 생성했습니다. 근거 파일은 각 항목 옆에 적었습니다.

- Base AOSP: `{lock['aosp']['tag']}` / manifest commit `{lock['aosp']['manifest_commit']}` / build ID `{lock['aosp']['build_id']}`
- redroid manifest/patch commits: local_manifests `{lock['redroid_manifest']['commit']}`, redroid-patches `{lock['redroid_patches']['commit']}`({len(lock['redroid_patches']['patches'])}개 패치)
- Product: `{lock['target']['lunch']}`
- Builder: Docker {builder['docker_engine']}, builder `{builder['builder_id'][:19]}…`
- 공개용 빌드 식별정보 검사: {identity_status} (사용자 `redroid`, 호스트 `redroid-builder`, 번호 `redroid.a15r36`)
- Gralloc source commit: gbm_gralloc `{gralloc['source']['commit']}` / patch: {', '.join(f"`{Path(p['file']).name}`" for p in gralloc['patches'])} / tests: {result(gralloc_ok)}
- ARM64 translator source: Android SDK `{bridge['package']['path']}` r{bridge['package']['revision']} / archive SHA-256 `{bridge['archive_sha256'][:16]}…`, {len(bridge['files'])}개 파일 해시는 `config/nativebridge-lock.json` / runtime: {result(checks['arm64']['pass'] and all(f['page_render'] for f in firefox))}
- microG APK versions/cert hashes: GmsCore {apps['com.google.android.gms']['version_name']} `{apps['com.google.android.gms']['signers'][0][:16]}…`, Play 스토어 {apps['com.android.vending']['version_name']} 계보 `{apps['com.android.vending']['lineage'][0][:16]}…`→`{apps['com.android.vending']['signers'][0][:16]}…`
- Docker image ID + tag + SHA: `{package['image_id']}` / `{package['image_tag']}` / package `{package['package_id']}`, `{package['dist_dir']}/SHA256SUMS`
- GPU: `{checks['gpu']['gles'].removeprefix('GLES: ')}` / Mesa renderer: {result(checks['gpu']['pass'])}
- Firefox ARM64: install / native library / page load: {result(all(f['install'] for f in firefox))} / {result(all(f['libmozglue_loaded'] for f in firefox))} / {result(all(f['page_render'] for f in firefox))}
- microG Self-Check: spoof / services / vending(Play Store): {result(spoof_ok)} / {result(services_ok)} / {result(vending_ok)}
- Unsupported signer negative tests: {negative['status']} ({negative['detail']})
- Android framework 계측 테스트(FrameworksServicesTests): NOT TESTED
- Gralloc crash regression: {result(gralloc_ok and checks['crash']['pass'])} (P010 요청 거부, allocator PID 유지, crash 0)
- 24~72h stability: NOT TESTED
- Regressions / follow-up issues: Google 계정 로그인 유지·FCM 푸시 수신·Play 스토어 앱 설치 NOT TESTED. Play 스토어는 microG 기기 등록을 켜야 서버 요청이 성공함
- Redistribution policy / licenses: 개인용 이미지. ARM64 번역기와 Play 스토어는 재배포 금지. `dist/report-{package['package_id']}/third-party.md`, `sbom.spdx.json`(패키지 {sbom_state['sbom']['packages']}개), `NOTICE.xml.gz`
- 이전 이미지와 재배포: [패키지 가져오기와 이미지 교체](../../README.md#패키지-가져오기와-이미지-교체). 이전 이미지가 없으면 필요한 입력과 설정을 복원해 다시 빌드

## 최신 이미지 실행 검증 결과

선택한 실행 검증 기록의 콜드 부팅 {sum(1 for b in coldboot if b['label'].startswith('coldboot'))}회와 같은 데이터 재시작, `09-verify.sh` 결과입니다. 원본 기록과 개인 환경의 경로는 공개 문서에 포함하지 않습니다.

| 항목 | 결과 |
|---|---|
''' + ''.join(f'| {name} | {value} |\n' for name, value in verify['result'].items()) + f'''
위 판정은 지정한 실행 검증 결과와 현재 이미지의 부정 시험 기록을 기준으로 합니다. 이전 이미지의 시험 결과는 자동으로 합산하지 않습니다. 이전 검증 경과는 [최종 검증 보고서](final-validation.md), 사용 절차는 [README](../../README.md)에 있습니다.
'''
    (ROOT / 'docs/reports/final.md').write_bytes(final.encode())
    names = sorted(path.name for path in report_dir.iterdir() if path.name != 'SHA256SUMS')
    (report_dir / 'SHA256SUMS').write_bytes(''.join(f'{sha256((report_dir / name).read_bytes())}  {name}\n' for name in names).encode())
    print(f'보고서: {ROOT / "docs/reports/final.md"}\n산출물: {report_dir}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
