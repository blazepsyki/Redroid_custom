#!/usr/bin/env python3
import json
import os
import platform
import shutil
import socket
import stat
import subprocess
from datetime import datetime, timezone
from pathlib import Path


def run(args):
    process = subprocess.run(args, capture_output=True, text=True, timeout=30)
    return dict(exit_code=process.returncode, stdout=process.stdout.strip(), stderr=process.stderr.strip())


report = dict(checked_at=datetime.now(timezone.utc).isoformat(), hostname=socket.gethostname(),
              kernel=platform.release(), user=os.environ.get('USER'), uid=os.getuid(),
              architecture=platform.machine(), cpus=os.cpu_count())
report['os_release'] = Path('/etc/os-release').read_text()
report['memory'] = {line.split(':')[0]: line.split(':', 1)[1].strip()
                    for line in Path('/proc/meminfo').read_text().splitlines()
                    if line.split(':')[0] in ('MemTotal', 'MemAvailable', 'SwapTotal', 'SwapFree')}
report['cpu_model'] = next((line.split(':', 1)[1].strip() for line in Path('/proc/cpuinfo').read_text().splitlines()
                            if line.startswith('model name')), None)
disk = shutil.disk_usage(Path.home())
report['home_disk'] = dict(path=str(Path.home()), total_bytes=disk.total, free_bytes=disk.free)
report['sudo_noninteractive'] = run(['sudo', '-n', 'true'])['exit_code'] == 0
report['binder_filesystem'] = [line for line in Path('/proc/filesystems').read_text().splitlines() if 'binder' in line]
report['binder_module_loaded'] = Path('/sys/module/binder_linux').exists()
report['binder_module_probe'] = run(['modprobe', '-n', '-v', 'binder_linux'])
report['binder_devices'] = {}
for name in ('binder', 'hwbinder', 'vndbinder', 'dri/renderD128'):
    path = Path('/dev') / name
    report['binder_devices'][str(path)] = dict(exists=path.exists(),
        character_device=path.exists() and stat.S_ISCHR(path.stat().st_mode))
kernel_config = Path('/boot') / f'config-{platform.release()}'
if kernel_config.is_file():
    try:
        report['binder_config'] = [line for line in kernel_config.read_text().splitlines() if 'ANDROID_BINDER' in line]
    except PermissionError:
        report['binder_config'] = '읽기 권한 없음'
report['adb'] = shutil.which('adb')
report['docker_version'] = run(['docker', 'version', '--format', '{{json .Server}}'])
report['docker_info'] = run(['docker', 'info', '--format',
    '{"version":"{{.ServerVersion}}","driver":"{{.Driver}}","cgroup":"{{.CgroupVersion}}","cpus":{{.NCPU}},"memory":{{.MemTotal}},"memory_limit":{{.MemoryLimit}}}'])
report['containers'] = run(['docker', 'ps', '-a', '--format', '{{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}'])
report['docker_usage'] = run(['docker', 'system', 'df'])
report['test_container'] = run(['docker', 'inspect', 'redroid-custom-test', '--format',
    '{"id":"{{.Id}}","image":"{{.Image}}","state":"{{.State.Status}}"}'])
report['ports'] = [line for line in run(['ss', '-H', '-ltn'])['stdout'].splitlines()
                   if ':5558 ' in line or ':5556 ' in line]
report['existing_redroid_mounts'] = run(['docker', 'inspect', 'redroid-arm64-test', '--format', '{{json .Mounts}}'])
print(json.dumps(report, ensure_ascii=False, indent=2))
