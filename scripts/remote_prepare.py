#!/usr/bin/env python3
import json
import os
import socket
import subprocess
import sys
from pathlib import Path

package_id, expected_host, image_tag, manifest_id, config_id = sys.argv[1:]
if socket.gethostname() != expected_host:
    raise RuntimeError('접속한 호스트가 사전 점검 기록과 다릅니다.')
if not package_id.isalnum():
    raise RuntimeError('패키지 ID가 잘못되었습니다.')
home = Path.home().resolve()
root = home / 'custom-redroid-tests' / package_id
if not root.resolve().is_relative_to(home) or root.is_symlink():
    raise RuntimeError('시험 작업 경로가 홈 디렉터리 밖입니다.')
image = subprocess.run(['docker', 'image', 'inspect', image_tag], capture_output=True, text=True)
replaced = None
if image.returncode == 0 and json.loads(image.stdout)[0]['Id'] not in {manifest_id, config_id}:
    # Loading the new archive replaces the tag while preserving the old image.
    replaced = json.loads(image.stdout)[0]['Id']
root.mkdir(parents=True, exist_ok=True)
marker = root / 'remote-test-owner.json'
owner = dict(package_id=package_id, hostname=expected_host, uid=os.getuid())
if marker.exists():
    if json.loads(marker.read_text()) != owner:
        raise RuntimeError('기존 시험 디렉터리의 소유 정보가 다릅니다.')
elif any(root.iterdir()):
    raise RuntimeError('소유 정보가 없는 기존 디렉터리는 사용하지 않습니다.')
marker.write_text(json.dumps(owner, indent=2) + '\n')
print(json.dumps(dict(remote_root=str(root), owner=owner, replaced_image_id=replaced), ensure_ascii=False, indent=2))
