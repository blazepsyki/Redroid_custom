import argparse
import contextlib
import hashlib
import io
import json
import stat
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'scripts'))
import package_image
import runtime
from rootfs_metadata import verify_tar
from runtime import binder_support


class ArchiveMetadataTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='redroid-tar-test-')
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / 'rootfs.tar'
        self.capability = bytes.fromhex('01000002c0000000000000000000000000000000')
        self.metadata = dict(entries={'system/bin/run-as': dict(uid=0, gid=2000, mode=0o750,
            type=stat.S_IFREG, size=4, sha256=hashlib.sha256(b'test').hexdigest(),
            xattrs={'security.capability': self.capability.hex()})})

    def archive(self, name='system/bin/run-as', uid=0, capability=True, duplicate=False):
        with tarfile.open(self.path, 'w', format=tarfile.PAX_FORMAT) as archive:
            item = tarfile.TarInfo(name)
            item.uid = uid
            item.gid = 2000
            item.mode = 0o750
            item.size = 4
            if capability:
                item.pax_headers['SCHILY.xattr.security.capability'] = self.capability.decode('utf-8', 'surrogateescape')
            archive.addfile(item, io.BytesIO(b'test'))
            if duplicate:
                archive.addfile(item, io.BytesIO(b'test'))

    def test_binary_capability_and_file_hash_survive_pax(self):
        self.archive()
        self.assertEqual(verify_tar(self.path, self.metadata), 1)

    def test_missing_capability_is_rejected(self):
        self.archive(capability=False)
        with self.assertRaisesRegex(RuntimeError, 'xattr'):
            verify_tar(self.path, self.metadata)

    def test_changed_owner_is_rejected(self):
        self.archive(uid=1000)
        with self.assertRaisesRegex(RuntimeError, '소유자'):
            verify_tar(self.path, self.metadata)

    def test_traversal_and_duplicate_members_are_rejected(self):
        for name, duplicate in (('../escape', False), ('/absolute', False), ('system/bin/run-as', True)):
            with self.subTest(name=name, duplicate=duplicate):
                self.archive(name=name, duplicate=duplicate)
                with self.assertRaises(RuntimeError):
                    verify_tar(self.path, self.metadata)

    def test_hardlink_outside_archive_is_rejected(self):
        with tarfile.open(self.path, 'w', format=tarfile.PAX_FORMAT) as archive:
            item = tarfile.TarInfo('system/bin/run-as')
            item.uid, item.gid, item.mode = 0, 2000, 0o750
            item.type = tarfile.LNKTYPE
            item.linkname = '../outside'
            archive.addfile(item)
        with self.assertRaisesRegex(RuntimeError, '안전하지 않은'):
            verify_tar(self.path, self.metadata)


class OCITests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='redroid-oci-test-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        raw = self.root / 'rootfs.tar'
        with tarfile.open(raw, 'w') as archive:
            item = tarfile.TarInfo('init')
            item.type = tarfile.SYMTYPE
            item.linkname = '/system/bin/init'
            archive.addfile(item)
        layer = self.root / 'rootfs.tar.gz'
        diff_id = package_image.compress_rootfs(raw, layer)
        self.oci = self.root / 'redroid.oci.tar'
        self.identities = package_image.create_oci(self.oci, layer, diff_id,
            dict(finished_at='2026-10-27T12:53:34+00:00'), {'io.custom-redroid.variant': 'vanilla'},
            'redroid-local:a15-r36-vanilla-fixture')

    def test_layout_blobs_and_uncompressed_diff_id_are_valid(self):
        config = package_image.verify_oci(self.oci)
        self.assertEqual(config['rootfs']['diff_ids'], [self.identities['diff_id']])
        with tarfile.open(self.oci, 'r:') as archive:
            index = json.load(archive.extractfile('index.json'))
            self.assertEqual(index['manifests'][0]['annotations']['org.opencontainers.image.ref.name'],
                             'docker.io/library/redroid-local:a15-r36-vanilla-fixture')

    def test_changed_layer_blob_is_rejected(self):
        corrupted = self.root / 'corrupt.oci.tar'
        target = self.identities['layer']['digest'].split(':')[1]
        with tarfile.open(self.oci, 'r:') as source, tarfile.open(corrupted, 'w') as destination:
            for item in source:
                content = source.extractfile(item).read()
                if item.name.endswith(target):
                    content = bytes([content[0] ^ 1]) + content[1:]
                destination.addfile(item, io.BytesIO(content))
        with self.assertRaisesRegex(RuntimeError, '해시'):
            package_image.verify_oci(corrupted)


class BinderPreflightTests(unittest.TestCase):
    def test_explicitly_disabled_kernel_is_rejected(self):
        ready, _ = binder_support('# CONFIG_ANDROID_BINDER_IPC is not set', 'nodev\tbinder\n', [True] * 3)
        self.assertFalse(ready)

    def test_binderfs_and_legacy_devices_are_supported(self):
        self.assertTrue(binder_support('CONFIG_ANDROID_BINDER_IPC=y', 'nodev\tbinder\n', [False] * 3)[0])
        self.assertTrue(binder_support('CONFIG_ANDROID_BINDER_IPC=y', '', [True] * 3)[0])

    def test_unloaded_module_without_devices_is_rejected(self):
        self.assertFalse(binder_support('CONFIG_ANDROID_BINDER_IPC=m', 'nodev\tproc\n', [False] * 3)[0])

    def test_missing_binder_prevents_container_creation(self):
        report = dict(boot_test_allowed=False, binder_ready=False, binder_reason='fixture: Binder 없음')
        with patch.object(runtime.subprocess, 'run') as runner, self.assertRaises(RuntimeError):
            runtime.deploy({}, report, argparse.Namespace(force=False, dry_run=False))
        runner.assert_not_called()


class ImageTagTests(unittest.TestCase):
    build = dict(official_patch_record_sha256='a' * 64)
    custom = dict(plan_sha256='b' * 64)

    def test_default_tag_uses_custom_plan_hash(self):
        with patch.dict('os.environ', {'REDROID_IMAGE_TAG': ''}):
            self.assertEqual(package_image.image_tag(self.build, self.custom), 'redroid-local:a15-r36-custom-' + 'b' * 12)

    def test_configured_tag_is_used_without_hash(self):
        for tag in ('myname/redroid:a15-custom', 'registry.local:5000/team/redroid:latest'):
            with self.subTest(tag=tag), patch.dict('os.environ', {'REDROID_IMAGE_TAG': tag}):
                self.assertEqual(package_image.image_tag(self.build, self.custom), tag)

    def test_oci_reference_name_follows_docker_short_name_rules(self):
        for tag, expected in (('redroid-custom:15', 'docker.io/library/redroid-custom:15'),
                              ('myname/redroid:a15', 'docker.io/myname/redroid:a15'),
                              ('registry.local:5000/team/redroid:latest', 'registry.local:5000/team/redroid:latest')):
            with self.subTest(tag=tag):
                self.assertEqual(package_image.full_reference(tag), expected)

    def test_invalid_configured_tag_is_rejected(self):
        for tag in ('MyName/redroid:a15', 'redroid', 'redroid:', 'bad name:tag'):
            with self.subTest(tag=tag), patch.dict('os.environ', {'REDROID_IMAGE_TAG': tag}):
                with self.assertRaises(RuntimeError):
                    package_image.image_tag(self.build, self.custom)


class RuntimeImageIdentityTests(unittest.TestCase):
    def setUp(self):
        self.package = dict(image_id='sha256:manifest', build_run_id='fixture',
                            oci=dict(config_digest='sha256:config', diff_id='sha256:layer'))
        self.image = dict(Id='sha256:manifest', RootFS=dict(Layers=['sha256:layer']),
                          Config=dict(Entrypoint=['/init', 'androidboot.hardware=redroid'],
                                      Labels={'io.custom-redroid.build-run-id': 'fixture'}))

    def test_manifest_and_config_ids_are_accepted_for_different_image_stores(self):
        for identity in ('sha256:manifest', 'sha256:config'):
            self.image['Id'] = identity
            runtime.validate_image(self.package, self.image)

    def test_wrong_rootfs_is_rejected_even_with_expected_id(self):
        self.image['RootFS']['Layers'] = ['sha256:other-layer']
        with self.assertRaisesRegex(RuntimeError, 'rootfs'):
            runtime.validate_image(self.package, self.image)

    def test_unknown_image_id_is_rejected(self):
        self.image['Id'] = 'sha256:unrelated'
        with self.assertRaisesRegex(RuntimeError, '이미지 ID'):
            runtime.validate_image(self.package, self.image)


class RuntimeProfileTests(unittest.TestCase):
    def test_gpu_deploy_uses_separate_name_port_data_and_render_node(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(
                runtime.os.environ, REDROID_CACHE_DIR=directory):
            output = io.StringIO()
            report = dict(boot_test_allowed=True, binder_ready=True, image_id='sha256:fixture',
                          profile='gpu', container_name='redroid-custom-gpu-test',
                          adb_endpoint='127.0.0.1:5559', gpu_mode='host')
            with patch.object(runtime.subprocess, 'run', return_value=runtime.subprocess.CompletedProcess([], 1)), \
                    contextlib.redirect_stdout(output):
                runtime.deploy({}, report, argparse.Namespace(force=False, dry_run=True))
            record = json.loads(output.getvalue())
            self.assertIn('redroid-custom-gpu-test', record['command'])
            self.assertIn('127.0.0.1:5559:5555', record['command'])
            self.assertNotIn('127.0.0.1:5558:5555', record['command'])
            self.assertIn('androidboot.redroid_gpu_node=/dev/dri/renderD128', record['command'])
            self.assertIn('type=bind,source=/dev/dri,target=/dev/dri', record['command'])
            self.assertTrue(Path(record['data_dir']).is_relative_to(Path(directory) / 'test-data'))

    def test_force_rejects_unexpected_existing_data_path(self):
        existing = dict(Config=dict(Labels={'io.custom-redroid.test': 'true'}),
                        Image='sha256:fixture', State=dict(Running=True),
                        Mounts=[dict(Destination='/data', Type='bind', Source='/unrelated-user-data')])
        report = dict(boot_test_allowed=True, binder_ready=True, image_id='sha256:fixture', gpu_mode='guest')
        result = runtime.subprocess.CompletedProcess([], 0, stdout=json.dumps([existing]))
        with tempfile.TemporaryDirectory() as directory, patch.dict(runtime.os.environ, REDROID_CACHE_DIR=directory), \
                patch.object(runtime.subprocess, 'run', return_value=result) as runner, \
                self.assertRaisesRegex(RuntimeError, '데이터 경로'):
            runtime.deploy({}, report, argparse.Namespace(force=True, dry_run=False))
        self.assertEqual(runner.call_count, 1)

    def test_force_reuses_owned_data_and_preserves_container_identity(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(runtime.os.environ, REDROID_CACHE_DIR=directory):
            data = str(Path(directory) / 'test-data/fixture')
            existing = dict(Id='fixture-container', Config=dict(Labels={'io.custom-redroid.test': 'true'}),
                            Image='sha256:fixture', State=dict(Running=True),
                            Mounts=[dict(Destination='/data', Type='bind', Source=data)])
            responses = [runtime.subprocess.CompletedProcess([], 0, stdout=json.dumps([existing])),
                         runtime.subprocess.CompletedProcess([], 0),
                         runtime.subprocess.CompletedProcess([], 0, stdout='1')]
            report = dict(boot_test_allowed=True, binder_ready=True, image_id='sha256:fixture',
                          gpu_mode='guest', adb='fixture-adb')
            with patch.object(runtime.subprocess, 'run', side_effect=responses), \
                    patch.object(runtime, 'command', side_effect=['307', 'fixture-fingerprint', 'BP1A.250505.005.D1']), \
                    contextlib.redirect_stdout(io.StringIO()):
                runtime.deploy({}, report, argparse.Namespace(force=True, dry_run=False))
            self.assertEqual(report['container_id'], 'fixture-container')
            self.assertEqual(report['data_dir'], data)
            self.assertTrue(report['runtime_verified'])

if __name__ == '__main__':
    unittest.main()
