#!/usr/bin/env python3
import argparse
import gzip
import hashlib
import io
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from phase0 import Phase0, PhaseError, ROOT, command, require, sha256, write_json
from rootfs_metadata import file_hash, verify_tar
from build_identity import verify_archive, verify_product

OCI_CONFIG = 'application/vnd.oci.image.config.v1+json'
OCI_MANIFEST = 'application/vnd.oci.image.manifest.v1+json'
OCI_LAYER = 'application/vnd.oci.image.layer.v1.tar+gzip'
ENTRYPOINT = ['/init', 'androidboot.hardware=redroid']
# Docker 이미지 참조(name:tag). 레지스트리 host:port 접두사를 허용하고 tag는 반드시 적습니다.
IMAGE_REFERENCE = re.compile(r'[a-z0-9]+(?:[._-][a-z0-9]+)*(?::[0-9]+)?(?:/[a-z0-9]+(?:[._-][a-z0-9]+)*)*:[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}')


def full_reference(tag):
    """Docker가 짧은 이름을 해석하는 규칙대로 레지스트리를 붙입니다(name → docker.io/library/name)."""
    first = tag.split('/', 1)[0]
    if '/' in tag and ('.' in first or ':' in first or first == 'localhost'):
        return tag
    return f'docker.io/{tag}' if '/' in tag else f'docker.io/library/{tag}'


def image_tag(build, custom_state):
    """Resolve custom image tags with the packaging date in Korea (YYMMDD)."""
    if custom_state:
        configured = os.environ.get('REDROID_IMAGE_TAG', '').strip() or 'gth1919/psyki:redroid-custom-15.0.0-64only-{date}'
        date = datetime.now(timezone(timedelta(hours=9))).strftime('%y%m%d')
        tag = configured.replace('{date}', date)
        require(IMAGE_REFERENCE.fullmatch(tag), f'REDROID_IMAGE_TAG는 name:tag 형식이어야 합니다: {tag}')
        return tag
    return f'redroid-local:a15-r36-vanilla-{build["official_patch_record_sha256"][:12]}'


def json_bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()


def descriptor(data, media_type):
    return dict(mediaType=media_type, digest=f'sha256:{sha256(data)}', size=len(data))


def compress_rootfs(source, destination):
    digest = hashlib.sha256()
    with source.open('rb') as stream, destination.open('xb') as target:
        with gzip.GzipFile(fileobj=target, mode='wb', filename='', mtime=0, compresslevel=3) as compressor:
            for block in iter(lambda: stream.read(4 * 1024 * 1024), b''):
                digest.update(block)
                compressor.write(block)
    return digest.hexdigest()


def create_oci(destination, layer, diff_id, build, labels, tag):
    config = dict(created=build['finished_at'], architecture='amd64', os='linux',
                  config=dict(Entrypoint=ENTRYPOINT, WorkingDir='/', Env=['PATH=/system/bin:/system/xbin'],
                              ExposedPorts={'5555/tcp': {}}, Labels=labels),
                  rootfs=dict(type='layers', diff_ids=[f'sha256:{diff_id}']),
                  history=[dict(created=build['finished_at'], created_by='custom-redroid 07-package.sh')])
    config_data = json_bytes(config)
    config_desc = descriptor(config_data, OCI_CONFIG)
    layer_desc = dict(mediaType=OCI_LAYER, digest=f'sha256:{file_hash(layer)}', size=layer.stat().st_size)
    manifest_data = json_bytes(dict(schemaVersion=2, mediaType=OCI_MANIFEST, config=config_desc, layers=[layer_desc]))
    manifest_desc = descriptor(manifest_data, OCI_MANIFEST)
    index_desc = dict(manifest_desc, annotations={'org.opencontainers.image.ref.name': full_reference(tag)},
                      platform=dict(architecture='amd64', os='linux'))
    index_data = json_bytes(dict(schemaVersion=2, mediaType='application/vnd.oci.image.index.v1+json', manifests=[index_desc]))
    epoch = int(datetime.fromisoformat(build['finished_at']).timestamp())
    with tarfile.open(destination, 'x', format=tarfile.USTAR_FORMAT) as archive:
        def add_data(name, data):
            header = tarfile.TarInfo(name)
            header.size = len(data)
            header.mode = 0o644
            header.mtime = epoch
            archive.addfile(header, io.BytesIO(data))
        add_data('oci-layout', json_bytes(dict(imageLayoutVersion='1.0.0')))
        add_data('index.json', index_data)
        add_data(f'blobs/sha256/{config_desc["digest"].split(":")[1]}', config_data)
        add_data(f'blobs/sha256/{manifest_desc["digest"].split(":")[1]}', manifest_data)
        header = tarfile.TarInfo(f'blobs/sha256/{layer_desc["digest"].split(":")[1]}')
        header.mode = 0o644
        header.size = layer.stat().st_size
        header.mtime = epoch
        with layer.open('rb') as stream:
            archive.addfile(header, stream)
    return dict(config_digest=config_desc['digest'], manifest_digest=manifest_desc['digest'], layer=layer_desc,
                diff_id=f'sha256:{diff_id}')


def verify_oci(path):
    with tarfile.open(path, 'r:') as archive:
        names = archive.getnames()
        require(len(names) == len(set(names)), 'OCI archive에 중복 경로가 있습니다.')
        layout = json.load(archive.extractfile('oci-layout'))
        require(layout == dict(imageLayoutVersion='1.0.0'), 'OCI layout 버전이 다릅니다.')
        index = json.load(archive.extractfile('index.json'))
        require(index['schemaVersion'] == 2 and len(index['manifests']) == 1, '단일 OCI manifest가 필요합니다.')
        def read_blob(item):
            require(item['digest'].startswith('sha256:'), 'OCI digest 알고리즘이 다릅니다.')
            name = f'blobs/sha256/{item["digest"].split(":")[1]}'
            member = archive.getmember(name)
            require(member.size == item['size'], 'OCI blob 크기가 다릅니다.')
            digest = hashlib.sha256()
            content = bytearray() if item['mediaType'] != OCI_LAYER else None
            with archive.extractfile(member) as stream:
                for block in iter(lambda: stream.read(4 * 1024 * 1024), b''):
                    digest.update(block)
                    if content is not None:
                        content.extend(block)
            require(f'sha256:{digest.hexdigest()}' == item['digest'], 'OCI blob 해시가 다릅니다.')
            return json.loads(content) if content is not None else name
        manifest = read_blob(index['manifests'][0])
        require(manifest['mediaType'] == OCI_MANIFEST and len(manifest['layers']) == 1, 'OCI manifest 구성이 다릅니다.')
        config = read_blob(manifest['config'])
        require(config['architecture'] == 'amd64' and config['os'] == 'linux', 'OCI 플랫폼이 다릅니다.')
        require(config['config']['Entrypoint'] == ENTRYPOINT, 'OCI 진입점이 다릅니다.')
        layer_name = read_blob(manifest['layers'][0])
        digest = hashlib.sha256()
        with gzip.GzipFile(fileobj=archive.extractfile(layer_name), mode='rb') as stream:
            for block in iter(lambda: stream.read(4 * 1024 * 1024), b''):
                digest.update(block)
        require(config['rootfs']['diff_ids'] == [f'sha256:{digest.hexdigest()}'], 'OCI diff ID가 다릅니다.')
    return config


def inspect_docker_metadata(tag, probe, metadata):
    paths = [f'/{name}' if name != '.' else '/' for name in metadata['critical']]
    args = ['docker', 'run', '--rm', '--read-only', '--network', 'none', '--cap-drop', 'ALL',
            '--entrypoint', '/.custom-redroid-probe',
            '--mount', f'type=bind,source={probe},target=/.custom-redroid-probe,readonly', tag, *paths]
    output = command(args)
    observations = []
    for line in output.splitlines():
        path, uid, gid, mode, link, selinux, capability = line.split('\t')
        name = path.lstrip('/') or '.'
        item = metadata['entries'][name]
        observed_mode = int(mode, 8)
        require((int(uid), int(gid)) == (item['uid'], item['gid']), f'Docker의 파일 소유자가 다릅니다: {name}')
        require(stat.S_IFMT(observed_mode) == item['type'], f'Docker의 파일 유형이 다릅니다: {name}')
        if item['type'] == stat.S_IFLNK:
            require(link == item['link'], f'Docker의 링크 대상이 다릅니다: {name}')
        else:
            require(stat.S_IMODE(observed_mode) == item['mode'], f'Docker의 파일 모드가 다릅니다: {name}')
        expected_caps = item['xattrs'].get('security.capability', '-')
        require(capability == expected_caps, f'Docker의 capability가 다릅니다: {name}')
        observations.append(dict(path=name, uid=int(uid), gid=int(gid), mode=stat.S_IMODE(observed_mode),
                                 selinux=selinux, expected_selinux=item['xattrs'].get('security.selinux', '-'),
                                 capability=capability, symlink_mode_normalized=(item['type'] == stat.S_IFLNK
                                     and stat.S_IMODE(observed_mode) != item['mode'])))
    require(len(observations) == len(paths), 'Docker 메타데이터 검사 결과가 누락되었습니다.')
    return observations


def package(args):
    phase = Phase0(args)
    is_custom = getattr(args, 'custom', False)
    build_path = ROOT / ('build-logs/custom-build-state.json' if is_custom else 'build-logs/build-state.json')
    build = json.loads(build_path.read_text(encoding='utf-8'))
    require(build['status'] == 'built' and build['mode'] in ('build', 'custom-build'), '성공한 전체 빌드 기록이 필요합니다.')
    require(build['source_manifest_sha256'] == phase.lock['sync']['manifest_sha256'], '소스 manifest가 빌드 기록과 다릅니다.')
    require(sha256(phase.state_path.read_bytes()) == build['official_patch_record_sha256'], '공식 패치 기록이 빌드 시점과 다릅니다.')
    phase.preflight()
    patch_state = json.loads(phase.state_path.read_text(encoding='utf-8'))
    phase.verify_patch_state(patch_state)
    custom_state = None
    if is_custom:
        from custom import verify_applied
        custom_state = verify_applied(phase)
        require(build['custom_plan_sha256'] == custom_state['plan_sha256'], 'custom 빌드와 source 적용 기록이 다릅니다.')
    else:
        phase.verify_snapshot(patch_state)
    product = (phase.source / build['properties']['PRODUCT_OUT']).resolve()
    require(product.is_relative_to((phase.source / 'out').resolve()), '산출물 경로가 out 밖입니다.')
    identity = verify_product(product)
    require(build.get('build_identity') == identity, '공개용 빌드 식별정보의 검증 기록이 없습니다. 제품을 다시 빌드해 주세요.')
    partitions = ('system', 'vendor', 'system_dlkm')
    hashes = {}
    for name in partitions:
        path = product / f'{name}.img'
        require(path.is_file() and path.stat().st_size > 0, f'필수 이미지가 없습니다: {path}')
        with path.open('rb') as stream:
            require(stream.read(4) != bytes.fromhex('3aff26ed'), f'raw ext4만 지원합니다. sparse 이미지는 먼저 조사해야 합니다: {name}')
            stream.seek(1080)
            require(stream.read(2) == b'\x53\xef', f'raw ext4 이미지가 아닙니다: {name}')
        hashes[name] = dict(size_bytes=path.stat().st_size, sha256=file_hash(path))
    for artifact in build['artifacts']:
        require((phase.source / artifact['path']).stat().st_size == artifact['size_bytes'], '이미지 크기가 빌드 기록과 다릅니다.')
    tag = image_tag(build, custom_state)
    if args.dry_run:
        print(json.dumps(dict(tag=tag, partitions=hashes, planned=['읽기 전용 조립', '전체 tar 메타데이터 검사',
            'OCI archive 생성·검증', 'Docker load', '정적 컨테이너 메타데이터 검사']), ensure_ascii=False, indent=2))
        return
    state_path = ROOT / ('build-logs/custom-package-state.json' if is_custom else 'build-logs/package-state.json')
    if state_path.exists() and not args.force:
        previous = json.loads(state_path.read_text())
        # 태그를 바꿨거나 같은 태그를 이후 빌드가 덮어썼으면 재사용하지 않고 새로 패키징합니다.
        current = subprocess.run(['docker', 'image', 'inspect', tag, '--format', '{{.Id}}'], capture_output=True, text=True)
        if (previous.get('status') == 'packaged' and previous.get('build_run_id') == build['run_id']
                and previous.get('image_tag') == tag and current.stdout.strip() == previous['image_id']):
            for name, digest in previous['artifact_sha256'].items():
                require(file_hash(ROOT / previous['dist_dir'] / name) == digest, f'기존 패키지 해시가 다릅니다: {name}')
            print(f'이미 검증한 패키지가 있습니다: {previous["dist_dir"]}')
            return
    package_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    work = phase.cache / 'packages' / package_id
    variant = 'custom' if is_custom else 'vanilla'
    dist = ROOT / 'dist' / f'a15-r36-{variant}-{package_id}'
    work.mkdir(parents=True, exist_ok=False)
    dist.mkdir(parents=True, exist_ok=False)
    result = dict(schema_version=1, package_id=package_id, build_run_id=build['run_id'], status='running',
                  started_at=datetime.now(timezone.utc).isoformat(), finished_at=None, image_tag=tag,
                  dist_dir=str(dist.relative_to(ROOT)), work_dir=str(work), partitions=hashes, runtime_verified=False,
                  image_id=None, artifact_sha256={})
    result['build_identity'] = identity
    result['variant'] = variant
    if custom_state:
        result.update(features=custom_state['features'], custom_plan_sha256=custom_state['plan_sha256'])
    write_json(state_path, result)
    try:
        image = os.environ.get('REDROID_BUILDER_IMAGE', 'redroid-builder:a15-ubuntu22.04')
        phase.logged(['docker', 'run', '--rm', '--privileged', '--network', 'none',
                      '--mount', f'type=bind,source={product},target=/images,readonly',
                      '--mount', f'type=bind,source={ROOT / "scripts"},target=/control-scripts,readonly',
                      '--mount', f'type=bind,source={work},target=/work', image, 'bash',
                      '/control-scripts/package-rootfs.sh', str(os.getuid()), str(os.getgid())],
                     f'package-{package_id}.log')
        metadata = json.loads((work / 'rootfs-metadata.json').read_text(encoding='utf-8'))
        result['metadata_entry_count'] = metadata['entry_count']
        layer = dist / 'rootfs.tar.gz'
        diff_id = compress_rootfs(work / 'rootfs.tar', layer)
        verify_tar(layer, metadata)
        require(verify_archive(layer) == identity, '이미지 archive의 빌드 식별정보가 제품 출력과 다릅니다.')
        labels = {'org.opencontainers.image.title': 'Custom redroid Android 15 순정 이미지',
                  'io.custom-redroid.aosp-tag': phase.lock['aosp']['tag'],
                  'io.custom-redroid.source-manifest-sha256': build['source_manifest_sha256'],
                  'io.custom-redroid.official-patches-sha256': build['official_patch_record_sha256'],
                  'io.custom-redroid.build-run-id': build['run_id'], 'io.custom-redroid.variant': variant,
                  'io.custom-redroid.redistribution': 'local-only'}
        if custom_state:
            labels['io.custom-redroid.custom-plan-sha256'] = custom_state['plan_sha256']
            labels['io.custom-redroid.features'] = json.dumps(custom_state['features'], sort_keys=True)
        oci_path = dist / 'redroid.oci.tar'
        result['oci'] = create_oci(oci_path, layer, diff_id, build, labels, tag)
        verify_oci(oci_path)
        # 같은 태그가 있으면 최신 빌드로 덮어씁니다. 이전 이미지는 지우지 않고 ID만 기록합니다.
        existing = subprocess.run(['docker', 'image', 'inspect', tag], capture_output=True, text=True)
        if existing.returncode == 0:
            replaced = json.loads(existing.stdout)[0]
            result['replaced_image'] = dict(id=replaced['Id'], build_run_id=replaced['Config'].get('Labels', {}).get(
                'io.custom-redroid.build-run-id'))
            print(f'같은 태그의 이전 이미지를 최신 빌드로 덮어씁니다: {tag} ({replaced["Id"]})', flush=True)
        phase.logged(['docker', 'image', 'load', '--input', oci_path], f'package-{package_id}.log')
        imported = json.loads(command(['docker', 'image', 'inspect', tag]))[0]
        require(imported['Os'] == 'linux' and imported['Architecture'] == 'amd64', 'Docker 플랫폼이 다릅니다.')
        require(imported['Config']['Entrypoint'] == ENTRYPOINT, 'Docker 진입점이 다릅니다.')
        require(all(imported['Config']['Labels'].get(key) == value for key, value in labels.items()), 'Docker label이 다릅니다.')
        require(imported['RootFS']['Layers'] == [f'sha256:{diff_id}'], 'Docker rootfs diff ID가 다릅니다.')
        observations = inspect_docker_metadata(tag, work / 'rootfs-probe', metadata)
        write_json(dist / 'docker-metadata.json', observations)
        result['image_id'] = imported['Id']
        result['docker_selinux_xattrs_preserved'] = all(item['selinux'] == item['expected_selinux'] for item in observations)
        result['docker_selinux_xattr_differences'] = [item['path'] for item in observations
                                                     if item['selinux'] != item['expected_selinux']]
        result['docker_symlink_modes_normalized'] = any(item['symlink_mode_normalized'] for item in observations)
        require(all(file_hash(product / f'{name}.img') == item['sha256'] for name, item in hashes.items()),
                '패키징 전후 원본 이미지 해시가 다릅니다.')
        shutil.copyfile(work / 'rootfs-metadata.json', dist / 'rootfs-metadata.json')
        for source in (ROOT / 'config/source-lock.json', ROOT / 'config/source-manifest.xml',
                       ROOT / 'config/builder-lock.json', ROOT / 'build-logs/official-patches.json', build_path):
            shutil.copyfile(source, dist / source.name)
        if custom_state:
            for name in ('custom-lock.json', 'features.json', 'gralloc-source-lock.json', 'microg-source-lock.json', 'apps-lock.json'):
                shutil.copyfile(ROOT / 'config' / name, dist / name)
        write_json(dist / 'image-inputs.json', hashes)
        result['status'] = 'packaged'
        write_json(dist / 'package-report.json', result)
        write_report(dist, result)
        for path in sorted(dist.iterdir()):
            if path.is_file() and path.name != 'SHA256SUMS':
                result['artifact_sha256'][path.name] = file_hash(path)
        (dist / 'SHA256SUMS').write_text(''.join(f'{digest}  {name}\n' for name, digest in result['artifact_sha256'].items()))
    except BaseException as error:
        result['status'] = 'failed'
        result['error'] = str(error)
        raise
    finally:
        result['finished_at'] = datetime.now(timezone.utc).isoformat()
        write_json(state_path, result)
        write_json(work / 'package-state.json', result)
    print(f'패키징 완료: {tag}\n산출물: {dist}')


def write_report(dist, result):
    content = f'''# redroid 패키징 결과

- 상태: 패키징 및 정적 검사 완료. Android 부팅은 미검증입니다.
- Docker 태그: `{result['image_tag']}`
- Docker 이미지 ID: `{result['image_id']}`
- 빌드 실행: `{result['build_run_id']}`
- 빌드 식별정보 검사: 사용자 `{result['build_identity']['username']}`, 호스트 `{result['build_identity']['hostname']}`, 번호 `{result['build_identity']['number']}`
- 변형: `{result.get('variant', 'vanilla')}`
- OCI manifest: `{result['oci']['manifest_digest']}`
- 검증한 rootfs 경로: {result['metadata_entry_count']}개
- 압축 전후 tar의 전체 소유자·모드·심볼릭 링크·xattr 검사: 통과
- Docker의 필수 경로·소유자·일반 파일 모드·capability 검사: 통과
- Docker snapshot의 SELinux xattr 유지: {result['docker_selinux_xattrs_preserved']}
- Docker snapshot의 SELinux xattr 차이 경로: {result['docker_selinux_xattr_differences']}
- Docker snapshot의 symlink mode 정규화 발생: {result['docker_symlink_modes_normalized']}

OCI layer와 rootfs tar에는 원본 이미지의 메타데이터를 보존했습니다. Docker snapshot에서 발생하는 차이는 `docker-metadata.json`을 확인해 주세요. 이 검사는 Android init 또는 system_server 실행 시험이 아닙니다.

`SHA256SUMS`로 파일을 검사할 수 있습니다. `redroid.oci.tar`는 OCI image layout archive이며 `rootfs.tar.gz`는 rootfs layer입니다. Docker 29의 containerd image store에서 `docker load -i redroid.oci.tar`로 가져오기를 검증했습니다. 다른 버전의 Docker에서는 OCI archive 지원 여부를 별도로 확인해야 합니다.

패키지 생성만으로 Android 부팅과 기능별 검증을 통과했다고 판정하지 않습니다. Binder를 지원하는 Linux 환경에서 별도 데이터 디렉터리와 localhost ADB를 사용하는 독립된 테스트가 필요합니다.
'''
    (dist / 'build-report.md').write_text(content, encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description='순정 Android 15 이미지의 rootfs·OCI 패키징 및 정적 검증')
    parser.add_argument('--dry-run', action='store_true', help='입력 검사와 예정 작업만 출력합니다.')
    parser.add_argument('--custom', action='store_true', help='custom 전체 빌드 기록과 feature를 검증해 별도 태그로 패키징합니다.')
    parser.add_argument('--force', action='store_true', help='기존 산출물을 보존하고 새 디렉터리에 다시 패키징합니다.')
    args = parser.parse_args()
    try:
        if args.dry_run:
            package(args)
        else:
            with Phase0(args).exclusive():
                package(args)
    except (PhaseError, RuntimeError, OSError, ValueError, tarfile.TarError) as error:
        print(f'실패: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
