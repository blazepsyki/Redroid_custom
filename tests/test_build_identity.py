import io
import os
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'scripts'))
from build_identity import BUILD_IDENTITY, verify_archive, verify_properties, verify_product
from phase0 import PhaseError


class BuildIdentityTests(unittest.TestCase):
    def properties(self):
        number = BUILD_IDENTITY['number']
        return dict({
            'ro.build.user': BUILD_IDENTITY['username'],
            'ro.build.host': BUILD_IDENTITY['hostname'],
            'ro.build.version.incremental': number,
            'ro.build.display.id': f'product-userdebug 15 BUILD {number} test-keys',
            'ro.system.build.fingerprint': f'vendor/product/device:15/BUILD/{number}:userdebug/test-keys',
        })

    def encode(self, values):
        return ''.join(f'{key}={value}\n' for key, value in values.items()).encode()

    def test_public_identity_is_verified_in_product_and_archive(self):
        with tempfile.TemporaryDirectory() as directory:
            product = Path(directory) / 'product'
            prop = product / 'system/build.prop'
            prop.parent.mkdir(parents=True)
            prop.write_bytes(self.encode(self.properties()))
            expected = dict(BUILD_IDENTITY, checked_files=['system/build.prop'])
            self.assertEqual(verify_product(product), expected)
            archive_path = Path(directory) / 'rootfs.tar.gz'
            with tarfile.open(archive_path, 'w:gz') as archive:
                archive.add(prop, arcname='./system/build.prop')
            self.assertEqual(verify_archive(archive_path), expected)

    def test_private_user_host_or_incremental_is_rejected(self):
        for key in ('ro.build.user', 'ro.build.host', 'ro.build.version.incremental'):
            values = dict(self.properties(), **{key: 'private-build-value'})
            with self.subTest(key=key), self.assertRaises(PhaseError):
                verify_properties({'system/build.prop': self.encode(values)})

    def test_partition_fingerprint_and_incremental_must_match(self):
        for key, value in (
            ('ro.vendor.build.fingerprint', 'vendor/product/device:15/BUILD/eng.private:userdebug/test-keys'),
            ('ro.vendor.build.version.incremental', 'eng.private'),
            ('ro.build.display.id', 'product-userdebug 15 BUILD eng.private test-keys'),
        ):
            files = {'system/build.prop': self.encode(self.properties()), 'vendor/build.prop': self.encode({key: value})}
            with self.subTest(key=key), self.assertRaises(PhaseError):
                verify_properties(files)

    def test_missing_primary_properties_or_fingerprint_is_rejected(self):
        values = self.properties()
        del values['ro.system.build.fingerprint']
        for files in ({}, {'system/build.prop': self.encode(values)}):
            with self.subTest(files=files), self.assertRaises(PhaseError):
                verify_properties(files)

    def test_stale_private_archive_is_rejected(self):
        values = self.properties()
        values['ro.build.user'] = 'private-user'
        data = self.encode(values)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'old.tar'
            with tarfile.open(path, 'w') as archive:
                item = tarfile.TarInfo('system/build.prop')
                item.size = len(data)
                archive.addfile(item, io.BytesIO(data))
            with self.assertRaises(PhaseError):
                verify_archive(path)

    def test_builder_overrides_inherited_private_metadata(self):
        project = Path(__file__).resolve().parent.parent
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'source').mkdir()
            (root / 'scripts').mkdir()
            (root / 'config').mkdir()
            (root / 'scripts/builder.sh').write_bytes((project / 'scripts/builder.sh').read_bytes())
            (root / 'scripts/common.sh').write_bytes((project / 'scripts/common.sh').read_bytes())
            (root / 'config/build.env').write_text(f'REDROID_SOURCE_DIR="{root / "source"}"\n')
            tools = root / 'tools'
            tools.mkdir()
            for name, script in {
                'findmnt': '#!/bin/sh\nprintf "ext4\\n"\n',
                'docker': '#!/bin/sh\nexit 0\n',
            }.items():
                path = tools / name
                path.write_text(script)
                path.chmod(0o755)
            environment = dict(os.environ, PATH=str(tools) + os.pathsep + os.environ['PATH'],
                               BUILD_USERNAME='private-user', BUILD_HOSTNAME='private-host', BUILD_NUMBER='private-number')
            result = subprocess.run(['bash', str(root / 'scripts/builder.sh'), '--dry-run'],
                                    env=environment, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            for key, field in (('BUILD_USERNAME', 'username'), ('BUILD_HOSTNAME', 'hostname'), ('BUILD_NUMBER', 'number')):
                self.assertIn(f'{key}={BUILD_IDENTITY[field]}', result.stdout)
            for private in ('private-user', 'private-host', 'private-number'):
                self.assertNotIn(private, result.stdout)


if __name__ == '__main__':
    unittest.main()
