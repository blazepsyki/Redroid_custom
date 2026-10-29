#!/usr/bin/env python3
import argparse
import json
import os
import shlex
import sys
from datetime import datetime, timezone
from pathlib import Path

from phase0 import Phase0, PhaseError, ROOT, require, sha256, write_json
from build_identity import verify_product


def parse_vars(path):
    result = {}
    for line in path.read_text(encoding='utf-8').splitlines():
        if '=' not in line:
            continue
        name, value = line.split('=', 1)
        tokens = shlex.split(value)
        require(len(tokens) <= 1, f'잘못된 빌드 속성입니다: {name}')
        result[name] = tokens[0] if tokens else ''
    return result


def run(args):
    phase = Phase0(args)
    phase.preflight()
    require(phase.lock['official_patch_status'] == 'complete', '공식 패치 적용이 완료되어야 합니다.')
    require(phase.state_path.is_file(), '실제 소스의 공식 패치 적용 기록이 없습니다.')
    state = json.loads(phase.state_path.read_text(encoding='utf-8'))
    require(state['patch_repository_commit'] == phase.lock['redroid_patches']['commit'], '공식 패치 저장소 SHA가 다릅니다.')
    phase.verify_patch_state(state)
    applied = sum(len(project['applied']) for project in state['projects'].values())
    require(applied == len(phase.lock['redroid_patches']['patches']), '공식 패치 적용 기록이 완전하지 않습니다.')
    custom_state = None
    if getattr(args, 'custom', False):
        from custom import verify_applied
        custom_state = verify_applied(phase)
    else:
        require(not (phase.source / '.repo/custom-redroid/custom-state.json').exists(),
                'custom source가 적용되어 있습니다. --custom으로 해당 기록을 검증해 주세요.')
        phase.verify_snapshot(state)
    jobs = int(os.environ.get('REDROID_BUILD_JOBS', '4'))
    require(1 <= jobs <= os.cpu_count(), '빌드 병렬도는 할당된 CPU 수 이내로 지정해 주세요.')
    target = phase.lock['target']
    mode = 'configure' if args.configure_only else 'build'
    if custom_state:
        mode = ('custom-modules' if getattr(args, 'modules', False) else
                'custom-sbom' if getattr(args, 'sbom', False) else f'custom-{mode}')
    run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    command = ['bash', ROOT / 'scripts/builder.sh', '--aosp-sandbox', 'bash',
               '/control-scripts/build-in-container.sh', target['lunch'], str(jobs), mode, run_id]
    if args.dry_run:
        print(f'예정: {mode}, 병렬도 {jobs}\n{shlex.join(map(str, command))}')
        return

    log_name = f'aosp-{mode}-{run_id}.log'
    # SBOM 생성은 제품 빌드 기록을 덮어쓰지 않도록 별도 상태 파일에 남깁니다.
    result_path = ROOT / ('build-logs/custom-sbom-state.json' if mode == 'custom-sbom' else
                          'build-logs/custom-build-state.json' if custom_state else 'build-logs/build-state.json')
    result = dict(run_id=run_id, mode=mode, status='running', started_at=datetime.now(timezone.utc).isoformat(),
                  finished_at=None, jobs=jobs, target=target, source_manifest_sha256=phase.lock['sync']['manifest_sha256'],
                  official_patch_record_sha256=sha256(phase.state_path.read_bytes()),
                  builder_id=json.loads((ROOT / 'config/builder-lock.json').read_text())['builder_id'],
                  log=f'build-logs/{log_name}', docker_seccomp='unconfined', aosp_nsjail=None,
                  runtime_verified=False)
    if custom_state:
        result['custom_plan_sha256'] = custom_state['plan_sha256']
        result['features'] = custom_state['features']
    write_json(result_path, result)
    try:
        phase.logged(command, log_name)
        log_text = (ROOT / result['log']).read_text(encoding='utf-8', errors='replace')
        require('Build sandboxing disabled due to nsjail error.' not in log_text,
                'nsjail이 비활성화되었습니다. 빌드 로그를 확인해 주세요.')
        metadata = phase.source / 'out/custom-redroid' / run_id
        properties = parse_vars(metadata / 'target-vars.txt')
        properties['TARGET_RELEASE'] = (metadata / 'target-release.txt').read_text().strip()
        expected = dict(TARGET_PRODUCT=target['product'], TARGET_BUILD_VARIANT=target['variant'],
                        TARGET_RELEASE=target['release'], PLATFORM_VERSION='15', PLATFORM_SDK_VERSION='35',
                        BUILD_ID=phase.lock['aosp']['build_id'])
        require(all(properties.get(key) == value for key, value in expected.items()),
                '실제 빌드 속성이 고정한 Android 15 대상과 다릅니다.')
        if custom_state:
            for key in ('with_gralloc_fix', 'with_microg_signature_spoofing', 'with_arm64_translation'):
                require(properties.get(key.upper()) == str(custom_state['features'][key]).lower(),
                        f'실제 빌드 feature가 다릅니다: {key}')
            if not custom_state['features']['with_arm64_translation']:
                require(properties.get('TARGET_CPU_ABI_LIST') == 'x86_64', '번역 없는 빌드가 ARM ABI를 광고합니다.')
            else:
                require(properties.get('TARGET_CPU_ABI_LIST') == 'x86_64,arm64-v8a', '번역 빌드의 ABI 목록이 예상과 다릅니다.')
        result['properties'] = properties
        result['aosp_nsjail'] = True
        if mode in ('build', 'custom-build', 'custom-modules'):
            relative = properties['PRODUCT_OUT']
            product = (phase.source / relative).resolve()
            require(product.is_relative_to(phase.source / 'out'), '제품 출력 경로가 out 밖을 가리킵니다.')
            artifacts = []
            names = ('system.img', 'vendor.img')
            if mode == 'custom-modules':
                names = []
                if custom_state['features']['with_gralloc_fix']:
                    names.append('vendor/lib64/hw/gralloc.gbm.so')
                if custom_state['features']['with_validation_tools']:
                    names.append('vendor/bin/custom_gralloc_format_test')
                if custom_state['features'].get('with_uhid_input'):
                    names.append('vendor/bin/custom_uhid_inputd')
                if custom_state['features']['with_microg_signature_spoofing']:
                    names.append('system/framework/services.jar')
            if custom_state and custom_state['features']['with_arm64_translation']:
                names = [*names, 'system/lib64/libndk_translation.so', 'system/lib64/libberberis_exec_region.so',
                         'system/bin/arm64/linker64']
                if mode != 'custom-modules':
                    names.append('system/lib64/arm64/libc.so')
            if custom_state and custom_state['features']['with_microg']:
                names = list(names)
                for app in json.loads((ROOT / 'config/microg-apk-lock.json').read_text(encoding='utf-8'))['apps']:
                    folder = 'priv-app' if app['privileged'] else 'app'
                    names.append(f'system/{folder}/{app["module"]}/{app["module"]}.apk')
                    if app['privileged']:
                        names.append(f'system/etc/permissions/privapp-permissions-{app["package"]}.xml')
                    if app.get('disabled_components'):
                        names.append(f'system/etc/sysconfig/component-overrides-{app["package"]}.xml')
                    if app.get('allow_in_power_save'):
                        names.append(f'system/etc/sysconfig/power-save-{app["package"]}.xml')
                    installed = product / 'system' / folder / app['module'] / f'{app["module"]}.apk'
                    require(installed.is_file() and sha256(installed.read_bytes()) == app['sha256'],
                            f'설치된 microG APK가 공식 원본과 다릅니다: {app["module"]}')
                link = product / 'system/lib64/libnb.so'
                require(link.is_symlink() and link.readlink().as_posix() == 'libndk_translation.so',
                        'libnb.so 링크가 없거나 대상이 다릅니다.')
            if custom_state:
                from apps_import import verify_apks
                for app in verify_apks()['apps']:
                    name = f'system/app/{app["module"]}/{app["module"]}.apk'
                    installed = product / name
                    require(installed.is_file() and sha256(installed.read_bytes()) == app['sha256'],
                            f'기본 설치 APK가 원본과 다릅니다: {app["module"]}')
                    names = [*names, name]
                    for native in app['native_files']:
                        name = f'system/app/{app["module"]}/lib/x86_64/{native["member"].rsplit("/", 1)[-1]}'
                        installed = product / name
                        require(installed.is_file() and sha256(installed.read_bytes()) == native['sha256'],
                                f'기본 설치 앱의 네이티브 라이브러리가 원본과 다릅니다: {app["module"]}')
                        names = [*names, name]
            for name in names:
                image = product / name
                require(image.is_file() and image.stat().st_size > 0, f'산출물이 없습니다: {image}')
                artifacts.append(dict(path=str(image.relative_to(phase.source)), size_bytes=image.stat().st_size))
            result['artifacts'] = artifacts
            if mode in ('build', 'custom-build'):
                result['build_identity'] = verify_product(product)
        if mode == 'custom-sbom':
            sbom = phase.source / 'out/soong/sbom' / target['product'] / 'sbom.spdx.json'
            document = json.loads(sbom.read_text(encoding='utf-8'))
            require(document.get('spdxVersion', '').startswith('SPDX-') and document.get('packages'),
                    f'SPDX SBOM을 읽을 수 없습니다: {sbom}')
            result['sbom'] = dict(path=str(sbom.relative_to(phase.source)), sha256=sha256(sbom.read_bytes()),
                                  packages=len(document['packages']), files=len(document.get('files', [])))
        result['status'] = ('configured' if mode.endswith('configure') else
                            'modules_built' if mode == 'custom-modules' else
                            'sbom_generated' if mode == 'custom-sbom' else 'built')
    except BaseException as error:
        result['status'] = 'failed'
        result['error'] = str(error)
        raise
    finally:
        result['finished_at'] = datetime.now(timezone.utc).isoformat()
        write_json(result_path, result)
        write_json(ROOT / f'build-logs/{mode}-{run_id}.json', result)
    print(f'{mode} 단계 완료: {result_path}')


def main():
    parser = argparse.ArgumentParser(description='Phase 0 순정 redroid의 대상 확인 및 전체 빌드')
    parser.add_argument('--configure-only', action='store_true', help='lunch와 제품 속성만 검사합니다.')
    parser.add_argument('--custom', action='store_true', help='custom source와 feature 기록을 검사해 빌드합니다.')
    parser.add_argument('--modules', action='store_true', help='--custom에서 변경된 모듈만 먼저 컴파일합니다.')
    parser.add_argument('--sbom', action='store_true', help='--custom 제품 빌드 뒤 AOSP SPDX SBOM(m sbom)을 생성합니다.')
    parser.add_argument('--dry-run', action='store_true', help='검사와 예정 명령 출력만 수행합니다.')
    parser.add_argument('--force', action='store_true', help='이전 결과와 관계없이 증분 빌드를 실행합니다. 출력물을 삭제하지 않습니다.')
    args = parser.parse_args()
    try:
        require(not args.modules or (args.custom and not args.configure_only), '--modules는 --custom과 함께 사용해 주세요.')
        require(not args.sbom or (args.custom and not args.configure_only and not args.modules),
                '--sbom은 --custom 단독으로 사용해 주세요.')
        if args.dry_run:
            run(args)
        else:
            with Phase0(args).exclusive():
                run(args)
    except (PhaseError, OSError, ValueError) as error:
        print(f'실패: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
