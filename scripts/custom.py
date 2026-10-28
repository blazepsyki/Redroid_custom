#!/usr/bin/env python3
import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from phase0 import Phase0, PhaseError, ROOT, clean_project, command, git, require, sha256, write_json

FEATURES = {
    'with_gralloc_fix': 'WITH_GRALLOC_FIX',
    'with_microg_signature_spoofing': 'WITH_MICROG_SIGNATURE_SPOOFING',
    'with_arm64_translation': 'WITH_ARM64_TRANSLATION',
    'with_microg': 'WITH_MICROG',
    'with_validation_tools': 'WITH_VALIDATION_TOOLS',
    'with_uhid_input': 'WITH_UHID_INPUT',
}


SOURCE_BUILT_BRIDGE_PATHS = ('lib64/arm64/', 'bin/arm64/', 'lib64/libberberis_exec_region.so')


def read_features():
    features = json.loads((ROOT / 'config/features.json').read_text(encoding='utf-8'))
    require(set(features) == set(FEATURES) and all(type(value) is bool for value in features.values()),
            'feature 키와 boolean 값을 확인해 주세요.')
    require(not features['with_arm64_translation'] or (ROOT / 'config/nativebridge-lock.json').is_file(),
            'Native Bridge 파일 기록이 없습니다. nativebridge_import.py로 먼저 추출해 주세요.')
    require(not features['with_microg'] or (ROOT / 'config/microg-apk-lock.json').is_file(),
            'microG APK 기록이 없습니다. microg_import.py로 먼저 검사해 주세요.')
    require(not features['with_microg'] or features['with_microg_signature_spoofing'],
            'microG 기본 탑재에는 signature spoofing 패치가 필요합니다.')
    return features


def microg_files():
    from microg_import import INPUTS, verify_apks
    lock = verify_apks()
    output = {}
    blueprint = []
    modules = []
    for app in lock['apps']:
        module = app['module']
        output[f'vendor/custom/microg/{module}.apk'] = (INPUTS / app['file']).read_bytes()
        # Soong의 dex·JNI 비압축 재작성은 v2 서명을 깨뜨리므로 공식 APK를 바이트 그대로 설치합니다.
        blueprint += ['android_app_import {', f'    name: "{module}",', f'    apk: "{module}.apk",',
                      '    preprocessed: true,',
                      *(['    skip_preprocessed_apk_checks: true,'] if app['skip_preprocessed_apk_checks'] else []),
                      f'    privileged: {str(app["privileged"]).lower()},',
                      '    dex_preopt: {', '        enabled: false,', '    },', '}', '']
        modules.append(module)
        if app['privileged']:
            # 요청한 privileged 권한을 모두 명시해 허용 목록 밖의 권한이 log 모드에서 자동 부여되지 않게 합니다.
            allowed = app['allowed_privileged_permissions']
            denied = [name for name in app['requested_privileged_permissions'] if name not in allowed]
            xml = ['<?xml version="1.0" encoding="utf-8"?>', '<permissions>',
                   f'    <privapp-permissions package="{app["package"]}">']
            xml += [f'        <permission name="{name}"/>' for name in allowed]
            xml += [f'        <deny-permission name="{name}"/>' for name in denied]
            xml += ['    </privapp-permissions>', '</permissions>', '']
            name = f'privapp-permissions-{app["package"]}.xml'
            output[f'vendor/custom/microg/{name}'] = '\n'.join(xml).encode()
            blueprint += ['prebuilt_etc {', f'    name: "custom_microg_{module}_privapp",', f'    src: "{name}",',
                          '    sub_dir: "permissions",', f'    filename: "{name}",', '}', '']
            modules.append(f'custom_microg_{module}_privapp')
        if app.get('disabled_components'):
            xml = ['<?xml version="1.0" encoding="utf-8"?>', '<config>', f'    <component-override package="{app["package"]}">']
            xml += [f'        <component class="{name}" enabled="false"/>' for name in app['disabled_components']]
            xml += ['    </component-override>', '</config>', '']
            name = f'component-overrides-{app["package"]}.xml'
            output[f'vendor/custom/microg/{name}'] = '\n'.join(xml).encode()
            blueprint += ['prebuilt_etc {', f'    name: "custom_microg_{module}_components",', f'    src: "{name}",',
                          '    sub_dir: "sysconfig",', f'    filename: "{name}",', '}', '']
            modules.append(f'custom_microg_{module}_components')
    output['vendor/custom/microg/Android.bp'] = '\n'.join(blueprint).encode()
    output['vendor/custom/microg/microg.mk'] = (
        '# allowlist의 허용·거부를 적용합니다. 목록에 없는 다른 앱의 권한은 log만 남기고 유지됩니다.\n'
        '# GSI board 설정이 system_ext에 disable을 두므로, 나중에 읽히는 product 파티션에서 덮어씁니다.\n'
        'PRODUCT_PRODUCT_PROPERTIES += ro.control_privapp_permissions=log\n\n'
        'PRODUCT_PACKAGES += \\\n' + ''.join(f'    {module} \\\n' for module in modules) + '\n').encode()
    return output


def app_files():
    from apps_import import INPUTS, verify_apks
    output = {}
    modules = []
    blueprint = []
    native_makefile = ['LOCAL_PATH := $(call my-dir)', '']
    for app in verify_apks()['apps']:
        module = app['module']
        output[f'vendor/custom/apps/{module}.apk'] = (INPUTS / app['file']).read_bytes()
        modules.append(module)
        blueprint += ['android_app_import {', f'    name: "{module}",', f'    apk: "{module}.apk",',
                      '    preprocessed: true,',
                      *(['    skip_preprocessed_apk_checks: true,'] if app['skip_preprocessed_apk_checks'] else []),
                      '    privileged: false,', '    dex_preopt: { enabled: false },', '}', '']
        if app['native_files']:
            with zipfile.ZipFile(INPUTS / app['file']) as archive:
                for native in app['native_files']:
                    member = native['member']
                    require(member.startswith('lib/x86_64/') and len(member.split('/')) == 3,
                            '네이티브 라이브러리 경로가 잘못되었습니다.')
                    data = archive.read(member)
                    require(sha256(data) == native['sha256'], 'APK 내부 라이브러리 해시가 기록과 다릅니다.')
                    name = member.rsplit('/', 1)[-1]
                    local = f'native/{module}/{name}'
                    output[f'vendor/custom/apps/{local}'] = data
                    native_module = module + '_Jni_' + ''.join(c if c.isalnum() else '_' for c in name)
                    modules.append(native_module)
                    native_makefile += ['include $(CLEAR_VARS)', f'LOCAL_MODULE := {native_module}',
                                        'LOCAL_MODULE_CLASS := ETC', f'LOCAL_MODULE_PATH := $(TARGET_OUT)/app/{module}/lib/x86_64',
                                        f'LOCAL_MODULE_STEM := {name}', f'LOCAL_SRC_FILES := {local}',
                                        'include $(BUILD_PREBUILT)', '']
    output['vendor/custom/apps/Android.bp'] = '\n'.join(blueprint).encode()
    output['vendor/custom/apps/Android.mk'] = '\n'.join(native_makefile).encode()
    output['vendor/custom/apps/apps.mk'] = ('PRODUCT_PACKAGES += \\\n' + ''.join(
        f'    {module} \\\n' for module in modules) + '\n').encode()
    return output


def nativebridge_files():
    from nativebridge_import import verify_files
    lock = json.loads((ROOT / 'config/nativebridge-lock.json').read_text(encoding='utf-8'))
    base = verify_files(lock)
    output = {}
    makefile = ['LOCAL_PATH := $(call my-dir)', '']
    modules = []
    # arm64 guest 라이브러리·linker와 libberberis_exec_region은 AOSP가 native_bridge 변형으로 직접 빌드합니다.
    # SDK에서는 소스가 없는 번역기와 proxy, 설정 파일만 prebuilt로 설치합니다.
    for item in lock['files']:
        relative = item['path']
        if relative.startswith(SOURCE_BUILT_BRIDGE_PATHS):
            continue
        output[f'vendor/custom/nativebridge/files/{relative}'] = (base / relative).read_bytes()
        module = 'custom_nb_' + ''.join(c if c.isalnum() else '_' for c in relative)
        directory, name = relative.rsplit('/', 1)
        makefile += ['include $(CLEAR_VARS)', f'LOCAL_MODULE := {module}', 'LOCAL_MODULE_CLASS := ETC',
                     f'LOCAL_MODULE_PATH := $(TARGET_OUT)/{directory}', f'LOCAL_MODULE_STEM := {name}',
                     f'LOCAL_SRC_FILES := files/{relative}', 'LOCAL_LICENSE_KINDS := legacy_proprietary',
                     'LOCAL_LICENSE_CONDITIONS := proprietary by_exception_only', 'include $(BUILD_PREBUILT)', '']
        modules.append(module)
    blueprint = []
    for index, (link, target) in enumerate(sorted(lock['symlinks'].items())):
        module = f'custom_nb_symlink_{index}'
        blueprint += ['install_symlink {', f'    name: "{module}",', f'    installed_location: "{link}",',
                      f'    symlink_target: "{target}",', '}', '']
        modules.append(module)
    output['vendor/custom/nativebridge/Android.mk'] = '\n'.join(makefile).encode()
    output['vendor/custom/nativebridge/Android.bp'] = '\n'.join(blueprint).encode()
    output['vendor/custom/nativebridge/nativebridge.mk'] = (
        'include frameworks/libs/native_bridge_support/native_bridge_support.mk\n\n'
        '# guest libc는 별도 soong_namespace에 있어 namespace를 추가해야 빌드됩니다.\n'
        'PRODUCT_SOONG_NAMESPACES += frameworks/libs/native_bridge_support/android_api/libc\n\n'
        'PRODUCT_PACKAGES += $(NATIVE_BRIDGE_PRODUCT_PACKAGES) libberberis_exec_region\n\n'
        'PRODUCT_PACKAGES += \\\n' + ''.join(f'    {module} \\\n' for module in modules) + '\n').encode()
    return output


def checked_destination(source, relative):
    path = source / relative
    require(path.resolve().is_relative_to(source), f'소스 밖의 경로입니다: {relative}')
    current = path
    while current != source:
        require(not current.is_symlink(), f'생성 경로에 symlink가 있습니다: {relative}')
        current = current.parent
    return path


def prepare_plan(phase):
    features = read_features()
    output = {}
    originals = {}
    patch_spec = json.loads((ROOT / 'config/custom-patches.json').read_text())
    by_project = {}
    for item in patch_spec['patches']:
        require(sha256((ROOT / item['patch']).read_bytes()) == item['sha256'], f'패치 해시가 다릅니다: {item["patch"]}')
        by_project.setdefault(item['project'], []).append(item)
    for project, patches in by_project.items():
        with tempfile.TemporaryDirectory(prefix='redroid-custom-patch-') as directory:
            staging = Path(directory)
            for item in patches:
                data = subprocess.check_output(['git', '-C', str(phase.source / project), 'show', f'HEAD:{item["path"]}'])
                require(sha256(data) == item['before_sha256'], f'패치 기준 파일이 다릅니다: {project}/{item["path"]}')
                target = staging / item['path']
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
                originals[f'{project}/{item["path"]}'] = item['before_sha256']
            for item in patches:
                command(['git', 'apply', ROOT / item['patch']], cwd=staging)
            for item in patches:
                data = (staging / item['path']).read_bytes()
                require(sha256(data) == item['after_sha256'], '패치 적용 결과가 기록과 다릅니다.')
                output[f'{project}/{item["path"]}'] = data

    gralloc = json.loads((ROOT / 'config/gralloc-source-lock.json').read_text())
    upstream = phase.cache / 'upstream/gbm_gralloc-redroid'
    if not upstream.exists():
        require(not phase.args.dry_run, 'dry-run에서는 source를 다운로드하지 않습니다. 실제 적용 단계에서 source를 준비해 주세요.')
        upstream.parent.mkdir(parents=True, exist_ok=True)
        command(['git', 'clone', gralloc['source']['url'], upstream])
        command(['git', '-C', upstream, 'checkout', '--detach', gralloc['source']['commit']])
    require(git(upstream, 'rev-parse', 'HEAD') == gralloc['source']['commit'], 'Gralloc source SHA가 다릅니다.')
    clean_project(upstream)
    with tempfile.TemporaryDirectory(prefix='redroid-gralloc-patch-') as directory:
        staging = Path(directory)
        for name in ('gralloc.cpp', 'gralloc_gbm.cpp', 'gralloc_drm.h', 'gralloc_gbm_priv.h'):
            (staging / name).write_bytes(subprocess.check_output(['git', '-C', str(upstream), 'show', f'HEAD:{name}']))
        for item in gralloc['patches']:
            require(sha256((ROOT / item['file']).read_bytes()) == item['sha256'], 'Gralloc patch 해시가 다릅니다.')
            command(['git', 'apply', ROOT / item['file']], cwd=staging)
        for path in staging.iterdir():
            output[f'vendor/custom/gralloc/source/{path.name}'] = path.read_bytes()

    for relative in ('gralloc/Android.mk', 'gralloc/gralloc_format_test.cpp', 'custom.mk'):
        output[f'vendor/custom/{relative}'] = (ROOT / 'vendor/custom' / relative).read_bytes()
    if features['with_uhid_input']:
        # Soong parses every Android.bp, so the module is copied only when the feature is enabled.
        for name in ('Android.bp', 'uhid_inputd.c', 'custom_uhid_inputd.rc'):
            output[f'vendor/custom/input/{name}'] = (ROOT / 'vendor/custom/input' / name).read_bytes()
    for name in ('MicrogCertificatePolicy.java', 'MicrogSignaturePolicy.java'):
        output[f'frameworks/base/services/core/java/com/android/server/pm/{name}'] = (
            ROOT / 'vendor/custom/framework' / name).read_bytes()
    output['frameworks/base/services/tests/servicestests/src/com/android/server/pm/MicrogSignaturePolicyTest.java'] = (
        ROOT / 'vendor/custom/framework-tests/MicrogSignaturePolicyTest.java').read_bytes()
    if features['with_arm64_translation']:
        output.update(nativebridge_files())
    if features['with_microg']:
        output.update(microg_files())
    output.update(app_files())
    output['vendor/custom/features.mk'] = ''.join(
        f'{variable} := {str(features[key]).lower()}\n' for key, variable in FEATURES.items()).encode()
    output['product.mk'] = b'$(call inherit-product, vendor/custom/custom.mk)\n'
    hashes = {name: sha256(data) for name, data in sorted(output.items())}
    digest = sha256(json.dumps(dict(features=features, files=hashes), sort_keys=True).encode())
    return dict(features=features, files=hashes, originals=originals, plan_sha256=digest,
                gralloc_source=gralloc['source'], patches=patch_spec['patches']), output


def stale_names(plan, previous):
    """이전 계획이 vendor/custom에 만들었고 새 계획에는 없는 파일입니다."""
    old = (previous or {}).get('files', {})
    return {name: digest for name, digest in old.items()
            if name.startswith('vendor/custom/') and name not in plan['files']}


def stale_files(phase, plan, previous):
    """이전 기록과 해시가 같아 지워도 되는 stale 파일만 돌려주고, 수정된 파일은 덮어쓰지 않도록 중단합니다."""
    result = []
    for name, digest in stale_names(plan, previous).items():
        path = checked_destination(phase.source, name)
        if path.exists():
            require(path.is_file() and sha256(path.read_bytes()) == digest, f'기존 수정 파일을 지우지 않습니다: {name}')
            result.append(path)
    return result


def verify_tree(phase, plan, previous=None, final=True):
    official = json.loads(phase.state_path.read_text())
    require(phase.lock['official_patch_status'] == 'complete', '공식 패치를 먼저 완료해야 합니다.')
    phase.verify_patch_state(official)
    hashes = plan['files']
    previous = previous or {}
    for name, expected in hashes.items():
        path = checked_destination(phase.source, name)
        if final:
            require(path.is_file() and sha256(path.read_bytes()) == expected, f'custom 파일이 기록과 다릅니다: {name}')
        elif path.exists():
            allowed = {expected, plan['originals'].get(name), previous.get('files', {}).get(name)}
            require(path.is_file() and sha256(path.read_bytes()) in allowed, f'기존 수정 파일을 덮어쓰지 않습니다: {name}')
    if not final:
        stale_files(phase, plan, previous)
    custom_root = phase.source / 'vendor/custom'
    if custom_root.exists():
        for path in custom_root.rglob('*'):
            if path.is_file() or path.is_symlink():
                name = path.relative_to(phase.source).as_posix()
                require(name in hashes or (not final and name in stale_names(plan, previous)),
                        f'등록되지 않은 custom 파일입니다: {path}')
    for relative, base in phase.snapshot().items():
        path = phase.source / relative
        expected_head = official['projects'].get(relative, {}).get('head', base)
        require(git(path, 'rev-parse', 'HEAD') == expected_head, f'기준 커밋이 다릅니다: {relative}')
        registered = {name[len(relative) + 1:] for name in hashes if name.startswith(relative + '/')}
        if not registered:
            clean_project(path)
            continue
        git_dir = Path(git(path, 'rev-parse', '--absolute-git-dir'))
        for operation in ('rebase-apply', 'rebase-merge', 'MERGE_HEAD', 'CHERRY_PICK_HEAD'):
            require(not (git_dir / operation).exists(), f'진행 중인 Git 작업이 있습니다: {relative}')
        raw_status = subprocess.check_output(['git', '-C', str(path), 'status', '--porcelain', '--untracked-files=all'], text=True)
        changed = {line[3:] for line in raw_status.splitlines()}
        require(changed <= registered, f'등록되지 않은 source 변경이 있습니다: {relative}: {changed - registered}')


def verify_applied(phase):
    state_path = phase.source / '.repo/custom-redroid/custom-state.json'
    require(state_path.is_file(), 'custom 적용 기록이 없습니다.')
    state = json.loads(state_path.read_text())
    plan, _ = prepare_plan(phase)
    require(state['status'] == 'applied' and state['plan_sha256'] == plan['plan_sha256'],
            '현재 source 설정과 적용 기록이 다릅니다. 03-apply-custom.sh를 먼저 실행해 주세요.')
    verify_tree(phase, plan)
    return state


def apply(phase):
    plan, files = prepare_plan(phase)
    state_path = phase.source / '.repo/custom-redroid/custom-state.json'
    previous = json.loads(state_path.read_text()) if state_path.is_file() else None
    if previous and previous['plan_sha256'] != plan['plan_sha256']:
        require(phase.args.force, 'custom 설정이 바뀌었습니다. 기존 파일 검증 후 --force로 갱신할 수 있습니다.')
    verify_tree(phase, plan, previous, final=False)
    if phase.args.dry_run:
        print(json.dumps(dict(features=plan['features'], files=list(files), plan_sha256=plan['plan_sha256']), indent=2))
        return
    state = dict(plan, status='applying', recorded_at=datetime.now(timezone.utc).isoformat())
    write_json(state_path, state)
    for path in stale_files(phase, plan, previous):
        path.unlink()
    for relative, data in files.items():
        path = checked_destination(phase.source, relative)
        if path.is_file() and path.read_bytes() == data:
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    verify_tree(phase, plan)
    state['status'] = 'applied'
    write_json(state_path, state)
    write_json(ROOT / 'config/custom-lock.json', state)
    print(f'custom source 적용 완료: {len(files)}개 파일, plan {state["plan_sha256"]}')


def main():
    parser = argparse.ArgumentParser(description='custom source 적용과 소유 파일·기준 SHA 검증')
    parser.add_argument('stage', choices=('apply', 'verify'))
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--force', action='store_true')
    args = parser.parse_args()
    try:
        phase = Phase0(args)
        with phase.exclusive():
            if args.stage == 'apply':
                apply(phase)
            else:
                state = verify_applied(phase)
                print(f'custom 검증 완료: {state["plan_sha256"]}')
    except (PhaseError, OSError, ValueError) as error:
        print(f'실패: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
