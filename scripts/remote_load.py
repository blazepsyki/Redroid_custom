#!/usr/bin/env python3
import hashlib
import json
import os
import subprocess
import sys
import tarfile
from pathlib import Path, PurePosixPath


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def digest(path):
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


require(len(sys.argv) == 4, '시험 경로와 두 archive의 예상 SHA-256이 필요합니다.')
root = Path(sys.argv[1]).resolve()
require(root.is_relative_to(Path('/nas/data/redroid-custom/packages')), '원격 시험 경로가 예상과 다릅니다.')
owner = json.loads((root / 'remote-test-owner.json').read_text())
require(owner['uid'] == os.getuid() and owner['package_id'] == root.name, '시험 디렉터리 소유 정보가 다릅니다.')
for name, expected in zip(('redroid.docker.tar', 'control.tar.gz'), sys.argv[2:]):
    require(digest(root / name) == expected, f'전송한 파일의 해시가 다릅니다: {name}')
print('전송한 Docker archive와 제어 파일의 SHA-256을 확인했습니다.', flush=True)
with tarfile.open(root / 'control.tar.gz', 'r:gz') as archive:
    for member in archive:
        relative = PurePosixPath(member.name)
        require(member.isfile() and not relative.is_absolute() and '..' not in relative.parts,
                f'허용하지 않는 제어 파일 경로입니다: {member.name}')
        target = root / member.name
        require(target.resolve().is_relative_to(root) and not target.is_symlink(), '제어 파일 경로가 시험 폴더 밖입니다.')
        data = archive.extractfile(member).read()
        if target.exists():
            require(target.read_bytes() == data, f'기존 제어 파일 내용이 다릅니다: {member.name}')
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open('xb') as output:
                output.write(data)
            target.chmod(member.mode & 0o777)
sys.path.insert(0, str(root / 'scripts'))
from runtime import validate_image
from package_image import inspect_docker_metadata

package = json.loads((root / 'build-logs/package-state.json').read_text())
existing = subprocess.run(['docker', 'image', 'inspect', package['image_tag']], capture_output=True, text=True)
if existing.returncode == 0 and json.loads(existing.stdout)[0]['Id'] != package['image_id']:
    # 같은 태그의 이전 이미지는 최신 이미지로 덮어씁니다. 가져온 뒤 아래에서 ID와 rootfs를 다시 검사합니다.
    print(f'같은 태그의 이전 이미지를 덮어씁니다: {json.loads(existing.stdout)[0]["Id"]}', flush=True)
print('Docker 호환 archive를 가져옵니다.', flush=True)
with (root / 'build-logs/remote-image-load.log').open('w') as log:
    subprocess.run(['docker', 'image', 'load', '--input', str(root / 'redroid.docker.tar')],
                   stdout=log, stderr=subprocess.STDOUT, check=True, timeout=300)
image = json.loads(subprocess.check_output(['docker', 'image', 'inspect', package['image_tag']], text=True))[0]
validate_image(package, image)
metadata = json.loads((root / 'rootfs-metadata.json').read_text())
observations = inspect_docker_metadata(image['Id'], root / 'tools/rootfs-probe', metadata)
(root / 'build-logs/remote-docker-metadata.json').write_text(json.dumps(observations, indent=2) + '\n')
result = dict(image_tag=package['image_tag'], actual_image_id=image['Id'],
              expected_manifest_id=package['image_id'], expected_config_id=package['oci']['config_digest'],
              rootfs_diff_id=image['RootFS']['Layers'], metadata_checked=len(observations),
              selinux_difference_paths=[item['path'] for item in observations if item['selinux'] != item['expected_selinux']],
              status='loaded_and_inspected', runtime_verified=False)
(root / 'build-logs/remote-image-state.json').write_text(json.dumps(result, indent=2) + '\n')
print(json.dumps(result, ensure_ascii=False, indent=2))
