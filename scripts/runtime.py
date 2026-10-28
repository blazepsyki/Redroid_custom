#!/usr/bin/env python3
import argparse
import gzip
import json
import os
import platform
import shutil
import stat
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from phase0 import Phase0, PhaseError, ROOT, command, require, write_json

CONTAINER = 'redroid-custom-test'
ADB_ENDPOINT = '127.0.0.1:5558'
PROFILES = {
    'guest': (CONTAINER, ADB_ENDPOINT),
    'gpu': ('redroid-custom-gpu-test', '127.0.0.1:5559'),
    'fixed': ('redroid-custom-fixed-test', '127.0.0.1:5560'),
    'arm64': ('redroid-custom-arm64-test', '127.0.0.1:5561'),
}


def validate_image(package, image):
    identities = {package['image_id'], package['oci']['config_digest']}
    require(image['Id'] in identities, 'Docker 이미지 ID가 패키징 기록과 다릅니다.')
    require(image['RootFS']['Layers'] == [package['oci']['diff_id']], 'Docker rootfs가 패키징 기록과 다릅니다.')
    require(image['Config']['Entrypoint'] == ['/init', 'androidboot.hardware=redroid'], 'Docker 진입점이 다릅니다.')
    require(image['Config'].get('Labels', {}).get('io.custom-redroid.build-run-id') == package['build_run_id'],
            'Docker 이미지의 빌드 실행 기록이 다릅니다.')


def binder_support(config, filesystems, devices):
    if '# CONFIG_ANDROID_BINDER_IPC is not set' in config:
        return False, '현재 커널의 CONFIG_ANDROID_BINDER_IPC가 비활성화되어 있습니다.'
    ready = any(line.split()[-1] == 'binder' for line in filesystems.splitlines() if line.split())
    if ready or all(devices):
        return True, 'Binderfs 또는 Binder 장치를 확인했습니다.'
    return False, 'Binderfs와 필요한 Binder 장치를 확인할 수 없습니다. 커널 및 모듈을 별도로 준비해야 합니다.'


def preflight(package, profile='guest'):
    require(platform.system() == 'Linux', 'Linux/WSL에서 실행해 주세요.')
    require(profile in PROFILES, '지원하지 않는 시험 profile입니다.')
    container_name, adb_endpoint = PROFILES[profile]
    report = dict(checked_at=datetime.now(timezone.utc).isoformat(), kernel=platform.release(),
                  image_tag=package['image_tag'], image_id=package['image_id'], binder_ready=False,
                  boot_test_allowed=False, profile=profile, container_name=container_name, adb_endpoint=adb_endpoint,
                  gpu_mode='host' if profile in ('gpu', 'fixed', 'arm64') else os.environ.get('REDROID_TEST_GPU_MODE', 'guest'))
    require(package['status'] == 'packaged', '패키징과 정적 검증이 먼저 완료되어야 합니다.')
    image = json.loads(command(['docker', 'image', 'inspect', package['image_tag']]))[0]
    validate_image(package, image)
    report['image_id'] = image['Id']
    config_path = Path('/proc/config.gz')
    config = ''
    if config_path.is_file():
        try:
            config = gzip.decompress(config_path.read_bytes()).decode()
            report['kernel_config_source'] = str(config_path)
        except OSError as error:
            report['kernel_config_error'] = str(error)
    if not config:
        config_path = Path('/boot') / f'config-{platform.release()}'
        if config_path.is_file():
            try:
                config = config_path.read_text()
                report['kernel_config_source'] = str(config_path)
            except OSError as error:
                report['kernel_config_error'] = str(error)
    filesystems = Path('/proc/filesystems').read_text()
    devices = []
    for name in ('binder', 'hwbinder', 'vndbinder'):
        path = Path('/dev') / name
        devices.append(path.exists() and stat.S_ISCHR(path.stat().st_mode))
    ready, reason = binder_support(config, filesystems, devices)
    report.update(binder_ready=ready, binder_reason=reason,
                  binder_config_enabled=('CONFIG_ANDROID_BINDER_IPC=y' in config or 'CONFIG_ANDROID_BINDER_IPC=m' in config),
                  binder_config_explicitly_disabled=('# CONFIG_ANDROID_BINDER_IPC is not set' in config),
                  binder_devices=dict(zip(('binder', 'hwbinder', 'vndbinder'), devices)),
                  dri_render_node=Path('/dev/dri/renderD128').exists())
    if shutil.which('modprobe'):
        module = subprocess.run(['modprobe', '-n', '-v', 'binder_linux'], capture_output=True, text=True)
        report['binder_module_available'] = module.returncode == 0
        report['binder_module_probe'] = (module.stdout + module.stderr).strip()
    source = Path(os.environ.get('REDROID_SOURCE_DIR', str(Path.home() / 'work/redroid-aosp15')))
    built_adb = source / 'out/host/linux-x86/bin/adb'
    adb = shutil.which('adb') or (str(built_adb) if built_adb.is_file() else None)
    report['adb'] = adb
    require(report['gpu_mode'] in ('guest', 'host'), 'GPU 모드는 guest 또는 host여야 합니다.')
    if report['gpu_mode'] == 'host':
        ready = ready and report['dri_render_node']
    report['boot_test_allowed'] = bool(ready and adb)
    return report


def deploy(package, report, args):
    require(report['boot_test_allowed'], report['binder_reason'] if not report['binder_ready']
            else 'ADB 또는 요청한 GPU 장치가 없습니다.')
    container_name = report.get('container_name', CONTAINER)
    adb_endpoint = report.get('adb_endpoint', ADB_ENDPOINT)
    cache = Path(os.environ.get('REDROID_CACHE_DIR', str(Path.home() / '.cache/custom-redroid'))).resolve()
    existing = subprocess.run(['docker', 'container', 'inspect', container_name], capture_output=True, text=True)
    if existing.returncode == 0:
        item = json.loads(existing.stdout)[0]
        require(args.force and item['Config'].get('Labels', {}).get('io.custom-redroid.test') == 'true'
                and item['Image'] == report['image_id'] and item['State']['Running'],
                '기존 컨테이너는 자동 삭제·교체하지 않습니다. 대상과 데이터를 먼저 확인해 주세요.')
        data_mounts = [mount for mount in item['Mounts'] if mount['Destination'] == '/data']
        require(len(data_mounts) == 1 and data_mounts[0]['Type'] == 'bind'
                and Path(data_mounts[0]['Source']).resolve().is_relative_to(cache / 'test-data'),
                '기존 시험 컨테이너의 데이터 경로가 예상과 다릅니다.')
        report.update(container_id=item['Id'], data_dir=data_mounts[0]['Source'])
        print('동일한 시험 이미지의 실행 중인 컨테이너를 다시 검사합니다.')
    else:
        run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        data = cache / 'test-data' / run_id
        data.mkdir(parents=True, exist_ok=False)
        require(data.is_relative_to(cache / 'test-data'), '시험 데이터 경로가 예상과 다릅니다.')
        docker_args = ['docker', 'run', '--detach', '--name', container_name, '--privileged', '--restart', 'no',
                       '--cpus', '1.5', '--memory', '8g', '--publish', f'{adb_endpoint}:5555',
                       '--mount', f'type=bind,source={data},target=/data',
                       '--label', 'io.custom-redroid.test=true',
                       '--label', f'io.custom-redroid.profile={report.get("profile", "guest")}',
                       '--label', 'com.centurylinklabs.watchtower.enable=false',
                       '--log-driver', 'json-file', '--log-opt', 'max-size=10m', '--log-opt', 'max-file=3']
        if report['gpu_mode'] == 'host':
            docker_args.extend(['--mount', 'type=bind,source=/dev/dri,target=/dev/dri'])
        docker_args.extend([report['image_id'], 'androidboot.use_memfd=1',
                            f'androidboot.redroid_gpu_mode={report["gpu_mode"]}',
                            'androidboot.redroid_width=720', 'androidboot.redroid_height=1280',
                            'androidboot.redroid_dpi=240', 'androidboot.redroid_fps=30'])
        if report['gpu_mode'] == 'host':
            docker_args.append('androidboot.redroid_gpu_node=/dev/dri/renderD128')
        if args.dry_run:
            print(json.dumps(dict(command=docker_args, data_dir=str(data)), ensure_ascii=False, indent=2))
            return
        report['data_dir'] = str(data)
        report['container_id'] = command(docker_args)
    adb = report['adb']
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        subprocess.run([adb, 'connect', adb_endpoint], capture_output=True)
        boot = subprocess.run([adb, '-s', adb_endpoint, 'shell', 'getprop', 'sys.boot_completed'], capture_output=True, text=True)
        if boot.returncode == 0 and boot.stdout.strip() == '1':
            server = command([adb, '-s', adb_endpoint, 'shell', 'pidof', 'system_server'])
            fingerprint = command([adb, '-s', adb_endpoint, 'shell', 'getprop', 'ro.build.fingerprint'])
            build_id = command([adb, '-s', adb_endpoint, 'shell', 'getprop', 'ro.build.id'])
            require(server and build_id == 'BP1A.250505.005.D1', '부팅된 제품 정보가 예상과 다릅니다.')
            report.update(status='booted', system_server=server, fingerprint=fingerprint,
                          build_id=build_id, runtime_verified=True)
            return
        running = command(['docker', 'inspect', container_name, '--format', '{{.State.Running}}'])
        require(running == 'true', '테스트 컨테이너가 종료되었습니다. docker logs를 확인해 주세요.')
        time.sleep(5)
    raise PhaseError('300초 안에 Android 부팅 완료를 확인하지 못했습니다.')


def main():
    parser = argparse.ArgumentParser(description='별도 redroid 시험 인스턴스의 실행 조건 및 부팅 검증')
    parser.add_argument('--preflight-only', action='store_true', help='호스트 실행 조건만 검사합니다.')
    parser.add_argument('--profile', choices=tuple(PROFILES), default='guest', help='guest=5558, gpu=5559, fixed=5560, arm64=5561의 독립된 인스턴스를 사용합니다.')
    parser.add_argument('--dry-run', action='store_true', help='컨테이너를 생성하지 않습니다.')
    parser.add_argument('--force', action='store_true', help='동일 이미지의 실행 중인 시험 컨테이너를 다시 검사합니다. 삭제하지 않습니다.')
    args = parser.parse_args()
    try:
        package = json.loads((ROOT / 'build-logs/package-state.json').read_text(encoding='utf-8'))
        report = preflight(package, args.profile)
        suffix = '' if args.profile == 'guest' else f'-{args.profile}'
        report.update(status='ready' if report['boot_test_allowed'] else 'prerequisites_missing', runtime_verified=False)
        if not args.dry_run:
            write_json(ROOT / f'build-logs/runtime{suffix}-preflight.json', report)
        print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
        if args.preflight_only or args.dry_run:
            return 0 if report['boot_test_allowed'] else 3
        with Phase0(args).exclusive():
            try:
                deploy(package, report, args)
            except BaseException as error:
                report.update(status='failed', error=str(error))
                raise
            finally:
                write_json(ROOT / f'build-logs/runtime{suffix}-state.json', report)
    except (PhaseError, OSError, ValueError) as error:
        print(f'실패: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
