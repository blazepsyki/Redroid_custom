#!/usr/bin/env python3
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


def run(args, timeout=20):
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    return dict(exit_code=result.returncode, stdout=result.stdout.strip(), stderr=result.stderr.strip())


root = Path(sys.argv[1]).resolve()
if not root.is_relative_to(Path('/data/redroid-custom/packages')):
    raise RuntimeError('시험 경로가 예상과 다릅니다.')
profile = sys.argv[2] if len(sys.argv) > 2 else 'guest'
if profile not in ('guest', 'gpu', 'fixed', 'arm64'):
    raise RuntimeError('지원하지 않는 수집 profile입니다.')
suffix = '' if profile == 'guest' else f'-{profile}'
prefix = f'remote{suffix}'
state = json.loads((root / f'build-logs/runtime{suffix}-state.json').read_text())
container = state.get('container_name', 'redroid-custom-test')
image = json.loads(subprocess.check_output(['docker', 'inspect', container], text=True))[0]
if image['Id'] != state['container_id'] or image['Image'] != state['image_id']:
    raise RuntimeError('실행 중인 시험 인스턴스가 기록과 다릅니다.')
report = dict(checked_at=datetime.now(timezone.utc).isoformat(), runtime=state,
              container=dict(id=image['Id'], image=image['Image'], state=image['State'],
                             memory_limit=image['HostConfig']['Memory'], nano_cpus=image['HostConfig']['NanoCpus'],
                             restart_policy=image['HostConfig']['RestartPolicy'], ports=image['HostConfig']['PortBindings'],
                             mounts=image['Mounts']))
report['boot_completed'] = run(['docker', 'exec', container, 'getprop', 'sys.boot_completed'])
report['binder_devices'] = run(['docker', 'exec', container, 'ls', '-l', '/dev/binder', '/dev/hwbinder', '/dev/vndbinder'])
report['system_server'] = run(['docker', 'exec', container, 'pidof', 'system_server'])
renderer = run(['docker', 'exec', container, 'dumpsys', 'SurfaceFlinger'])
(root / f'build-logs/{prefix}-surfaceflinger.txt').write_text(renderer['stdout'] + '\n')
report['renderer'] = [line.strip() for line in renderer['stdout'].splitlines() if 'GLES:' in line]
report['graphics_properties'] = {name: run(['docker', 'exec', container, 'getprop', name])['stdout'] for name in
    ('ro.hardware.egl', 'ro.hardware.gralloc', 'gralloc.gbm.device', 'ro.boot.redroid_gpu_mode',
     'ro.product.cpu.abilist', 'ro.dalvik.vm.native.bridge', 'ro.custom.microg_spoof')}
report['gralloc_sha256'] = run(['docker', 'exec', container, 'sha256sum', '/vendor/lib64/hw/gralloc.gbm.so'])
crash = run(['docker', 'exec', container, 'logcat', '-d', '-b', 'crash', '-v', 'threadtime'])
(root / f'build-logs/{prefix}-crash.log').write_text(crash['stdout'] + '\n')
report['crash_log'] = dict(exit_code=crash['exit_code'], lines=len(crash['stdout'].splitlines()),
    relevant_lines=[line for line in crash['stdout'].splitlines()
                    if any(token in line for token in ('SIGFPE', 'FPE_INTDIV', 'Fatal signal', 'FATAL EXCEPTION', 'gralloc'))])
report['stats'] = run(['docker', 'stats', '--no-stream', '--format', '{{.Name}} CPU={{.CPUPerc}} Mem={{.MemUsage}}', container])
report['memory_events'] = run(['docker', 'exec', container, 'cat', '/sys/fs/cgroup/memory.events'])
report['memory_peak'] = run(['docker', 'exec', container, 'cat', '/sys/fs/cgroup/memory.peak'])
report['host_gpu_kernel_log'] = run(['journalctl', '-k', '--no-pager', '-n', '100', '--grep', 'i915|GPU HANG'])
report['containers'] = run(['docker', 'ps', '--format', '{{.Names}}\t{{.Image}}\t{{.Status}}'])
screenshot = root / f'build-logs/{prefix}-home.png'
with screenshot.open('wb') as stream:
    result = subprocess.run(['adb', '-s', state.get('adb_endpoint', '127.0.0.1:5558'), 'exec-out', 'screencap', '-p'],
                            stdout=stream, stderr=subprocess.PIPE, timeout=30)
report['screenshot'] = dict(exit_code=result.returncode, path=str(screenshot), size_bytes=screenshot.stat().st_size)
(root / f'build-logs/{prefix}-validation.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
print(json.dumps(report, ensure_ascii=False, indent=2))
