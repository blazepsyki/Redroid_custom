#!/usr/bin/env python3
"""실행 호스트의 scrcpy UHID 장치 하나를 시험 컨테이너에 연결합니다.

실행 호스트에서 사용합니다. 자동으로 모든 입력 장치를 노출하지 않으며,
--list로 확인한 현재 연결의 event 장치를 --event로 명시해야 합니다.
"""
import argparse
import json
import platform
import re
import subprocess
import sys
from pathlib import Path

from phase0 import PhaseError, require


def event_info(event, sysfs=Path('/sys/class/input')):
    require(re.fullmatch(r'event[0-9]+', event), 'event 장치 이름이 잘못되었습니다.')
    folder = sysfs / event
    require((folder / 'device/name').read_text().strip() == 'scrcpy', '선택한 장치가 scrcpy UHID 장치가 아닙니다.')
    numbers = (folder / 'dev').read_text().strip().split(':')
    require(len(numbers) == 2 and all(value.isdigit() for value in numbers), '장치 번호를 읽을 수 없습니다.')
    major, minor = map(int, numbers)
    require(major == 13, 'Linux input 장치가 아닙니다.')
    return dict(event=event, major=major, minor=minor)


def validate_container(image):
    require(image['State']['Running'] and (image['Config'].get('Labels') or {}).get('io.custom-redroid.test') == 'true',
            '이 프로젝트의 실행 중인 시험 컨테이너만 수정할 수 있습니다.')
    target = Path('/dev/input')
    require(not any(target.is_relative_to(Path(item['Destination']))
                    or Path(item['Destination']).is_relative_to(target) for item in image['Mounts']),
            '/dev/input이 호스트에서 마운트되어 있습니다. 호스트 장치 권한은 변경하지 않습니다.')


def attach(container, event):
    info = event_info(event)
    image = json.loads(subprocess.check_output(['docker', 'inspect', container], text=True))[0]
    validate_container(image)
    # Create the node with its final group and mode before EventHub observes IN_CREATE.
    script = '''set -e
node="/dev/input/$1"
test ! -L /dev/input
test ! -L "$node"
test ! -e "$node" || test -c "$node"
umask 007
mkdir -p /dev/input
rm -f -- "$node"
mknod -m 0660 "$node" c "$2" "$3"
'''
    subprocess.run(['docker', 'exec', '--user', '0:1004', container, 'sh', '-c', script,
                    'attach-input', info['event'], str(info['major']), str(info['minor'])], check=True)
    return dict(info, container=container, status='attached')


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--list', action='store_true', help='실행 호스트의 scrcpy event 장치를 나열합니다.')
    group.add_argument('--event', help='현재 연결에서 생성된 event 장치 하나를 명시합니다.')
    parser.add_argument('--container', default='redroid-custom-arm64-test')
    args = parser.parse_args()
    try:
        require(platform.system() == 'Linux', 'Android 컨테이너가 실행되는 Linux 호스트에서 사용해 주세요.')
        if args.list:
            output = []
            for folder in sorted(Path('/sys/class/input').glob('event*')):
                if (folder / 'device/name').read_text().strip() == 'scrcpy':
                    output.append(event_info(folder.name))
        else:
            output = attach(args.container, args.event)
        print(json.dumps(output, ensure_ascii=False, indent=2))
    except (PhaseError, OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        print(f'실패: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
