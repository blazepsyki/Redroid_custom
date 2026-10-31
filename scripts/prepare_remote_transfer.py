#!/usr/bin/env python3
import argparse
import gzip
import io
import json
import tarfile
from pathlib import Path

from phase0 import ROOT, require, write_json
from rootfs_metadata import file_hash

parser = argparse.ArgumentParser(description='원격 시험 전송 파일과 무결성 목록 준비')
parser.add_argument('--package-state', default='build-logs/package-state.json')
parser.add_argument('--directory', default='dist/remote-transfer')
args = parser.parse_args()
package_path = ROOT / args.package_state
package = json.loads(package_path.read_text(encoding='utf-8'))
host_info = json.loads((ROOT / 'build-logs/remote-preflight.json').read_text(encoding='utf-8'))
require(package['status'] == 'packaged', '검증한 패키지가 필요합니다.')
destination = ROOT / args.directory
require(destination.resolve().is_relative_to((ROOT / 'dist').resolve()), '전송 산출물은 dist 아래에 저장해야 합니다.')
docker_archive = destination / 'redroid.docker.tar'
require(docker_archive.is_file(), 'docker save로 만든 호환 archive가 필요합니다.')
# 원격 호스트의 서비스 폴더입니다. 다른 서비스의 /nas/data/<서비스>와 같은 위치를 사용합니다.
# 패키지는 packages/<ID>, Android /data와 잠금은 서비스 폴더 바로 아래(test-data/, locks/)에 둡니다.
remote_base = Path('/nas/data/redroid-custom')
remote_root = remote_base / 'packages' / package['package_id']
files = {}
for name in ('phase0.py', 'runtime.py', 'common.sh', '08-deploy-test.sh',
             'package_image.py', 'build_identity.py', 'rootfs_metadata.py', 'remote_preflight.py', 'remote_collect.py', 'remote_coldboot.py',
             'remote_keyboard.py', '09-verify.sh', 'verify.py'):
    files[f'scripts/{name}'] = (ROOT / 'scripts' / name).read_bytes()
files['config/source-lock.json'] = (ROOT / 'config/source-lock.json').read_bytes()
files['manifests/redroid.xml'] = (ROOT / 'manifests/redroid.xml').read_bytes()
files['build-logs/package-state.json'] = package_path.read_bytes()
files['rootfs-metadata.json'] = (ROOT / package['dist_dir'] / 'rootfs-metadata.json').read_bytes()
files['tools/rootfs-probe'] = (Path(package['work_dir']) / 'rootfs-probe').read_bytes()
files['config/build.env'] = (
    f'REDROID_CACHE_DIR="{remote_base}"\n'
    f'REDROID_SOURCE_DIR="{remote_root}/source-placeholder"\n'
    'REDROID_TEST_GPU_MODE=guest\n'
).encode()
archive_path = destination / 'control.tar.gz'
with archive_path.open('wb') as raw:
    with gzip.GzipFile(fileobj=raw, mode='wb', filename='', mtime=0) as compressed:
        with tarfile.open(fileobj=compressed, mode='w|', format=tarfile.USTAR_FORMAT) as archive:
            for name, content in sorted(files.items()):
                header = tarfile.TarInfo(name)
                header.size = len(content)
                header.mode = 0o755 if name.endswith('.sh') or name == 'tools/rootfs-probe' else 0o644
                archive.addfile(header, io.BytesIO(content))
record = dict(hostname=host_info['hostname'], remote_root=str(remote_root), package_id=package['package_id'],
              image_tag=package['image_tag'], manifest_digest=package['image_id'],
              config_digest=package['oci']['config_digest'], diff_id=package['oci']['diff_id'],
              docker_archive_sha256=file_hash(docker_archive), control_archive_sha256=file_hash(archive_path))
write_json(destination / 'transfer.json', record)
(destination / 'SHA256SUMS').write_text(''.join(f'{file_hash(destination / name)}  {name}\n'
    for name in ('redroid.docker.tar', 'control.tar.gz', 'transfer.json')), encoding='utf-8')
print(json.dumps(record, ensure_ascii=False, indent=2))
