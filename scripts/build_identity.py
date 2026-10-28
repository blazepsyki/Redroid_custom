#!/usr/bin/env python3
import tarfile
from pathlib import PurePosixPath

from phase0 import require

BUILD_IDENTITY = dict(username='redroid', hostname='redroid-builder', number='redroid.a15r36')


def verify_properties(files):
    primary = files.get('system/build.prop', files.get('system/etc/build.prop'))
    require(primary is not None, '빌드 식별정보를 검사할 system build.prop가 없습니다.')
    parsed = {}
    for name, data in files.items():
        values = {}
        for line in data.decode('utf-8').splitlines():
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                values[key.strip()] = value.strip()
        parsed[name] = values
    primary_name = 'system/build.prop' if 'system/build.prop' in parsed else 'system/etc/build.prop'
    expected = {
        'ro.build.user': BUILD_IDENTITY['username'],
        'ro.build.host': BUILD_IDENTITY['hostname'],
        'ro.build.version.incremental': BUILD_IDENTITY['number'],
    }
    require(all(parsed[primary_name].get(key) == value for key, value in expected.items()),
            '빌드 사용자·호스트·번호가 고정된 공개용 식별값과 다릅니다. 다시 빌드해 주세요.')
    require(BUILD_IDENTITY['number'] in parsed[primary_name].get('ro.build.display.id', '').split(),
            '표시용 빌드 번호에 공개용 식별값이 없습니다.')
    fingerprints = 0
    for name, values in parsed.items():
        for key, value in values.items():
            if key.endswith('.build.version.incremental'):
                require(value == BUILD_IDENTITY['number'], f'파티션 빌드 번호가 공개용 값과 다릅니다: {name}: {key}')
            elif key.endswith('.build.fingerprint'):
                require(value.rsplit(':', 1)[0].rsplit('/', 1)[-1] == BUILD_IDENTITY['number'],
                        f'파티션 fingerprint가 공개용 값과 다릅니다: {name}: {key}')
                fingerprints += 1
            elif key in expected:
                require(value == expected[key], f'빌드 식별값이 공개용 값과 다릅니다: {name}: {key}')
            elif key == 'ro.build.display.id':
                require(BUILD_IDENTITY['number'] in value.split(),
                        f'표시용 빌드 번호가 공개용 값과 다릅니다: {name}: {key}')
    require(fingerprints > 0, '검증할 파티션 fingerprint가 없습니다.')
    return dict(BUILD_IDENTITY, checked_files=sorted(files))


def verify_product(product):
    files = {}
    for partition in ('system', 'vendor', 'system_dlkm'):
        for path in sorted((product / partition).rglob('build.prop')):
            if path.is_file():
                files[path.relative_to(product).as_posix()] = path.read_bytes()
    return verify_properties(files)


def verify_archive(path):
    files = {}
    with tarfile.open(path, 'r|*') as archive:
        for item in archive:
            name = PurePosixPath(item.name).as_posix()
            if item.isfile() and PurePosixPath(name).name == 'build.prop':
                require(name not in files, 'build.prop 경로가 중복됩니다.')
                files[name] = archive.extractfile(item).read()
    return verify_properties(files)
