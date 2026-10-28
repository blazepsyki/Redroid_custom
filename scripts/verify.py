#!/usr/bin/env python3
"""실행 중인 시험 인스턴스의 GPU·ARM64·microG·Play 스토어·crash·OOM 상태를 항목별로 검사합니다.

컨테이너를 만들거나 지우지 않고 읽기만 합니다. 실패한 항목은 종료 코드의 비트로 알립니다.
boot=1, gpu=2, arm64=4, microg=8, crash=16, oom=32
"""
import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from runtime import PROFILES

FLAGS = dict(boot=1, gpu=2, arm64=4, microg=8, crash=16, oom=32)
GMS = 'com.google.android.gms'
VENDING = 'com.android.vending'
VPA_SERVICE = f'{VENDING}/com.google.android.finsky.setup.VpaService'
# Play 스토어에 허용하지 않은 권한 중, 부여되면 위험이 큰 것만 확인합니다.
FORBIDDEN_VENDING = ('android.permission.PACKAGE_VERIFICATION_AGENT', 'android.permission.WRITE_SECURE_SETTINGS',
                     'android.permission.REBOOT', 'android.permission.STATUS_BAR')


def run(args, timeout=60):
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    return result.stdout.strip()


def granted(dump):
    return set(re.findall(r'^\s+([\w.]+): granted=true', dump, re.M))


def verify(container, endpoint):
    adb = ['adb', '-s', endpoint]
    shell = lambda *args: run(adb + ['shell', *args])
    run(['adb', 'connect', endpoint])
    item = json.loads(run(['docker', 'inspect', container]))[0]
    checks = {}

    boot = dict(boot_completed=shell('getprop', 'sys.boot_completed'), system_server=shell('pidof', 'system_server'),
                fingerprint=shell('getprop', 'ro.build.fingerprint'))
    boot['pass'] = boot['boot_completed'] == '1' and bool(boot['system_server'])
    checks['boot'] = boot

    gles = next((line for line in shell('dumpsys', 'SurfaceFlinger').splitlines() if line.startswith('GLES:')), '')
    gpu = dict(gles=gles, egl=shell('getprop', 'ro.hardware.egl'), gralloc=shell('getprop', 'ro.hardware.gralloc'),
               kernel_errors=run(['journalctl', '-k', '--no-pager', '--utc', '--since',
                                  item['State']['StartedAt'][:19].replace('T', ' '),
                                  '--grep', r'i915.*(hang|reset|error|timeout)|GPU HANG']))
    if gpu['kernel_errors'] == '-- No entries --':
        gpu['kernel_errors'] = ''
    # llvmpipe·SwiftShader는 host GPU 성공으로 보지 않습니다.
    gpu['pass'] = 'Intel' in gles and not re.search(r'llvmpipe|SwiftShader', gles) and not gpu['kernel_errors']
    checks['gpu'] = gpu

    arm64 = dict(abilist=shell('getprop', 'ro.product.cpu.abilist'), native_bridge=shell('getprop', 'ro.dalvik.vm.native.bridge'),
                 translator=shell('ls', '/system/lib64/libndk_translation.so'))
    arm64['pass'] = ('arm64-v8a' in arm64['abilist'].split(',') and arm64['native_bridge'] not in ('', '0')
                     and arm64['translator'] == '/system/lib64/libndk_translation.so')
    checks['arm64'] = arm64

    vending = shell('dumpsys', 'package', VENDING)
    microg = dict(gms_path=shell('pm', 'path', GMS), vending_paths=re.findall(r'codePath=(\S+)', vending),
                  vending_versions=re.findall(r'versionName=(.+)', vending),
                  privapp_mode=shell('getprop', 'ro.control_privapp_permissions'),
                  vpa_service_disabled='No services found' in shell('pm', 'query-services', '--brief', '-n', VPA_SERVICE),
                  vending_forbidden_granted=sorted(granted(vending) & set(FORBIDDEN_VENDING)))
    microg['pass'] = (microg['gms_path'] == 'package:/system/priv-app/GmsCore/GmsCore.apk'
                      and '/system/priv-app/Phonesky' in microg['vending_paths'] and microg['privapp_mode'] == 'log'
                      and microg['vpa_service_disabled'] and not microg['vending_forbidden_granted'])
    checks['microg'] = microg

    log = run(adb + ['logcat', '-d', '-b', 'crash'])
    crash = dict(lines=len(log.splitlines()) if log else 0,
                 processes=sorted(set(re.findall(r'>>> (\S+) <<<', log)) | set(re.findall(r'AndroidRuntime: Process: ([\w.:]+)', log))),
                 gralloc_sigfpe=len(re.findall(r'(?i)gralloc|allocator|SIGFPE|FPE_INTDIV', log)))
    crash['pass'] = crash['lines'] == 0
    checks['crash'] = crash

    events = run(['docker', 'exec', container, 'cat', '/sys/fs/cgroup/memory.events'])
    oom = dict(events=dict(line.split() for line in events.splitlines() if line.strip()),
               peak=run(['docker', 'exec', container, 'cat', '/sys/fs/cgroup/memory.peak']),
               memory_limit=item['HostConfig']['Memory'])
    oom['pass'] = oom['events'].get('oom_kill') == '0' and oom['events'].get('oom') == '0'
    checks['oom'] = oom
    return item, checks


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--profile', default='arm64', choices=sorted(PROFILES))
    parser.add_argument('--output', help='결과 JSON 경로. 기본은 build-logs/verify-<profile>-<시각>.json')
    parser.add_argument('--dry-run', action='store_true', help='검사할 컨테이너와 ADB 주소만 출력합니다.')
    args = parser.parse_args()
    container, endpoint = PROFILES[args.profile]
    if args.dry_run:
        print(json.dumps(dict(container=container, endpoint=endpoint, flags=FLAGS), ensure_ascii=False))
        return 0
    try:
        item, checks = verify(container, endpoint)
    except (subprocess.SubprocessError, OSError, ValueError, IndexError) as error:
        print(f'실패: 시험 인스턴스를 읽을 수 없습니다: {error}', file=sys.stderr)
        return 64
    report = dict(checked_at=datetime.now(timezone.utc).isoformat(), profile=args.profile, container=container,
                  container_id=item['Id'], image_id=item['Image'], checks=checks,
                  result={name: 'PASS' if value['pass'] else 'FAIL' for name, value in checks.items()})
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    output = Path(args.output) if args.output else Path(__file__).resolve().parent.parent / f'build-logs/verify-{args.profile}-{stamp}.json'
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report['result'], ensure_ascii=False))
    print(f'기록: {output}')
    return sum(FLAGS[name] for name, value in checks.items() if not value['pass'])


if __name__ == '__main__':
    sys.exit(main())
