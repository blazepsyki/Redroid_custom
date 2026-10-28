#!/usr/bin/env python3
"""NAS 시험 패키지 폴더에서 arm64 profile의 반복 콜드 부팅과 데이터 유지 시험을 수행합니다.

각 회차는 새 /data로 부팅하고 기능을 검사한 뒤 이 시험이 만든 컨테이너만 제거합니다.
마지막 회차의 컨테이너는 같은 /data로 재시작해 앱 데이터 유지를 확인합니다.
"""
import argparse
import json
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

CONTAINER = 'redroid-custom-arm64-test'
ENDPOINT = '127.0.0.1:5561'
ADB = ['adb', '-s', ENDPOINT]
FIREFOX = 'org.mozilla.firefox'
ONBOARDING = ('CANCEL', 'Continue', 'Not now', 'NOT NOW', 'Skip', 'Maybe later', 'Start browsing', 'Got it')


def run(args, timeout=60, check=False):
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if check and result.returncode:
        raise RuntimeError(f'명령 실패: {args}\n{result.stderr}')
    return result


def shell(*args, timeout=60):
    return run(ADB + ['shell', *args], timeout=timeout).stdout.strip()


def ui_texts():
    run(ADB + ['shell', 'uiautomator', 'dump', '/sdcard/ui.xml'])
    xml = run(ADB + ['shell', 'cat', '/sdcard/ui.xml']).stdout
    nodes = []
    for node in re.findall(r'<node [^>]*>', xml):
        text = re.search(r'text="([^"]*)"', node)
        description = re.search(r'content-desc="([^"]*)"', node)
        if description and description.group(1) and not (text and text.group(1)):
            text = description
        bounds = re.search(r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', node)
        if text and bounds:
            x1, y1, x2, y2 = map(int, bounds.groups())
            nodes.append((text.group(1), (x1 + x2) // 2, (y1 + y2) // 2))
    return nodes


def tap_labels(labels, rounds=6):
    tapped = []
    for _ in range(rounds):
        hit = next((node for node in ui_texts() if node[0] in labels), None)
        if not hit:
            break
        run(ADB + ['shell', 'input', 'tap', str(hit[1]), str(hit[2])])
        tapped.append(hit[0])
        time.sleep(3)
    return tapped


def screenshot(path):
    with path.open('wb') as stream:
        subprocess.run(ADB + ['exec-out', 'screencap', '-p'], stdout=stream, timeout=30)


def container_info():
    return json.loads(run(['docker', 'inspect', CONTAINER], check=True).stdout)[0]


def remove_own_container():
    item = container_info()
    if item['Config']['Labels'].get('io.custom-redroid.test') != 'true':
        raise RuntimeError('시험 label이 없는 컨테이너는 제거하지 않습니다.')
    run(['docker', 'stop', '-t', '20', CONTAINER], timeout=120, check=True)
    run(['docker', 'rm', CONTAINER], check=True)


def firefox_check(root, label, install):
    result = {}
    if install:
        output = run(ADB + ['install', str(root / 'apks/fenix-159.0.multi.android-arm64-v8a.apk')], timeout=300)
        result['install'] = 'Success' in output.stdout
    else:
        result['install'] = FIREFOX in shell('pm', 'list', 'packages', FIREFOX)
    result['primary_cpu_abi'] = re.findall(r'primaryCpuAbi=(\S+)', shell('dumpsys', 'package', FIREFOX))[:1]
    run(['adb', '-s', ENDPOINT, 'logcat', '-c'])
    start = shell('am', 'start', '-W', '-a', 'android.intent.action.VIEW', '-d', 'https://example.com', FIREFOX, timeout=120)
    result['activity'] = 'Status: ok' in start or 'Complete' in start
    time.sleep(12)
    result['onboarding_tapped'] = tap_labels(ONBOARDING)
    if result['onboarding_tapped']:
        shell('am', 'start', '-a', 'android.intent.action.VIEW', '-d', 'https://example.com', FIREFOX)
    time.sleep(15)
    result['onboarding_after'] = tap_labels(('CANCEL', 'Not now', 'NOT NOW', 'Got it'))
    texts = [node[0] for node in ui_texts()]
    result['url_bar'] = any('example.com' in text for text in texts)
    result['page_text'] = any('documentation examples' in text for text in texts)
    pid = shell('pidof', FIREFOX)
    time.sleep(20)
    result['process_kept'] = bool(pid) and shell('pidof', FIREFOX) == pid
    log = run(['adb', '-s', ENDPOINT, 'logcat', '-d']).stdout
    result['berberis_initialized'] = 'Initialized Berberis (aarch64)' in log
    result['libmozglue_loaded'] = bool(re.search(r'lib/arm64/libmozglue\.so .*: ok', log))
    screenshot(root / f'build-logs/{label}-firefox.png')
    # Gecko가 접근성 트리에 노출한 본문 텍스트로 렌더링을 판정합니다. 화면은 스크린샷으로 따로 남깁니다.
    result['page_render'] = result['page_text']
    return result


def gralloc_load(iterations):
    passed = 0
    for _ in range(iterations):
        output = shell('/vendor/bin/custom_gralloc_format_test', timeout=60)
        cases = [json.loads(line) for line in output.splitlines() if line.startswith('{')]
        passed += len(cases) == 5 and all(case['pass'] for case in cases)
    return dict(iterations=iterations, passed=passed)


def binfmt_check(root):
    run(ADB + ['push', str(root / 'apks/hello_arm64'), '/data/local/tmp/hello_arm64'], check=True)
    shell('chmod', '755', '/data/local/tmp/hello_arm64')
    direct = run(ADB + ['shell', '/data/local/tmp/hello_arm64'])
    dynamic = run(ADB + ['shell', '/system/bin/arm64/linker64', '--help'])
    return dict(static_exec_output=direct.stdout.strip(), static_exec_code=direct.returncode,
                dynamic_linker_code=dynamic.returncode, dynamic_linker_output=(dynamic.stdout + dynamic.stderr).strip()[:200])


def inspect_boot(root, label, install_firefox, gralloc_iterations):
    state = json.loads((root / 'build-logs/runtime-arm64-state.json').read_text())
    item = container_info()
    report = dict(label=label, checked_at=datetime.now(timezone.utc).isoformat(), status=state.get('status'),
                  container_id=item['Id'], started_at=item['State']['StartedAt'], data_dir=state.get('data_dir'),
                  system_server=shell('pidof', 'system_server'), boot_completed=shell('getprop', 'sys.boot_completed'))
    run(ADB + ['root'])
    time.sleep(3)
    run(['adb', 'connect', ENDPOINT])
    report['allocator_pid'] = shell('pidof', 'android.hardware.graphics.allocator@2.0-service')
    report['p010_rejections'] = shell('logcat', '-d').count('unsupported allocation format: 0x36')
    report['microg_preinstalled'] = {pkg: shell('pm', 'path', pkg) for pkg in ('com.google.android.gms', 'com.android.vending')}
    # 설치 시점에 부여된 권한만 모읍니다. privileged 권한은 allowlist에서 허용한 것만 있어야 합니다.
    vending = shell('dumpsys', 'package', 'com.android.vending')
    report['play_store'] = dict(
        version=re.findall(r'versionName=(.+)', vending)[:1],
        granted=sorted(set(re.findall(r'^\s+([\w.]+): granted=true', vending, re.M)) &
                       {'android.permission.INSTALL_PACKAGES', 'android.permission.DELETE_PACKAGES',
                        'android.permission.PACKAGE_VERIFICATION_AGENT', 'android.permission.WRITE_SECURE_SETTINGS',
                        'android.permission.REBOOT', 'android.permission.MANAGE_USERS'}))
    # sysconfig component-override는 사용자 설정이 아니라 패키지 분석 단계에서 적용되므로, 서비스 조회 결과로 판정합니다.
    report['play_store']['vpa_service_disabled'] = 'No services found' in shell(
        'pm', 'query-services', '--brief', '-n', 'com.android.vending/com.google.android.finsky.setup.VpaService')
    shell('am', 'start', '-W', '-n', 'com.android.vending/.AssetBrowserActivity', timeout=120)
    time.sleep(15)
    top = re.findall(r'topResumedActivity=.* (\S+/\S+) ', shell('dumpsys', 'activity', 'activities'))
    report['play_store']['launch_top_activity'] = top[:1]
    report['play_store']['launched'] = bool(top) and top[0].startswith('com.android.vending/')
    screenshot(root / f'build-logs/{label}-playstore.png')
    shell('input', 'keyevent', 'HOME')
    report['gralloc_load'] = gralloc_load(gralloc_iterations)
    report['firefox'] = firefox_check(root, label, install_firefox)
    report['binfmt'] = binfmt_check(root)
    report['allocator_pid_after'] = shell('pidof', 'android.hardware.graphics.allocator@2.0-service')
    crash = shell('logcat', '-d', '-b', 'crash')
    report['crash_lines'] = len(crash.splitlines()) if crash else 0
    report['crash_processes'] = sorted(set(re.findall(r'>>> (\S+) <<<', crash)) |
                                       set(re.findall(r'AndroidRuntime: Process: ([\w.:]+)', crash)))
    report['crash_causes'] = sorted(set(re.findall(r'AndroidRuntime: Caused by: (.+)', crash)))[:10]
    report['memory_events'] = run(['docker', 'exec', CONTAINER, 'cat', '/sys/fs/cgroup/memory.events']).stdout.strip()
    report['memory_peak'] = run(['docker', 'exec', CONTAINER, 'cat', '/sys/fs/cgroup/memory.peak']).stdout.strip()
    report['gpu_kernel_errors'] = run(['journalctl', '-k', '--no-pager', '--since', item['State']['StartedAt'][:19].replace('T', ' '),
                                       '--utc', '--grep', r'i915.*(hang|reset|error|timeout)|GPU HANG']).stdout.strip()
    # 일치하는 기록이 없을 때 journalctl이 출력하는 안내 문구는 오류가 아닙니다.
    if report['gpu_kernel_errors'] == '-- No entries --':
        report['gpu_kernel_errors'] = ''
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', help='NAS 시험 패키지 폴더')
    parser.add_argument('--boots', type=int, default=3)
    parser.add_argument('--gralloc-iterations', type=int, default=30)
    args = parser.parse_args()
    root = Path(args.root).resolve()
    reports = []
    for index in range(1, args.boots + 1):
        if run(['docker', 'container', 'inspect', CONTAINER]).returncode == 0:
            remove_own_container()
        started = time.monotonic()
        deploy = run(['bash', str(root / 'scripts/08-deploy-test.sh'), '--profile', 'arm64'], timeout=600)
        elapsed = round(time.monotonic() - started, 1)
        if deploy.returncode:
            reports.append(dict(label=f'coldboot-{index}', status='failed', output=deploy.stdout[-2000:] + deploy.stderr[-2000:]))
            break
        report = inspect_boot(root, f'coldboot-{index}', True, args.gralloc_iterations)
        report['deploy_to_boot_seconds'] = elapsed
        reports.append(report)
        print(json.dumps(dict(label=report['label'], boot=elapsed, firefox=report['firefox']['page_render'],
                              crash=report['crash_lines']), ensure_ascii=False), flush=True)
    if reports and reports[-1].get('status') == 'booted':
        run(['docker', 'restart', '-t', '20', CONTAINER], timeout=180, check=True)
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            run(['adb', 'connect', ENDPOINT])
            if shell('getprop', 'sys.boot_completed') == '1':
                break
            time.sleep(5)
        report = inspect_boot(root, 'restart-same-data', False, 5)
        report['firefox_profile_exists'] = shell('ls', '-d', f'/data/data/{FIREFOX}/files') != ''
        reports.append(report)
        print(json.dumps(dict(label=report['label'], firefox=report['firefox']['page_render'],
                              installed=report['firefox']['install'], crash=report['crash_lines']), ensure_ascii=False), flush=True)
    (root / 'build-logs/coldboot-report.json').write_text(json.dumps(reports, ensure_ascii=False, indent=2) + '\n')


if __name__ == '__main__':
    main()
