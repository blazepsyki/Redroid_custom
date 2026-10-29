import argparse
import hashlib
import json
import sys
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'scripts'))
import custom


class FeatureValidationTests(unittest.TestCase):
    def test_builder_environment_accepts_arm64_feature_name(self):
        script = Path(__file__).resolve().parent.parent / 'scripts/print_features.py'
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / 'features.json'
            config.write_text(json.dumps({key: False for key in custom.FEATURES}))
            process = subprocess.run([sys.executable, str(script), str(config)], capture_output=True, text=True)
            self.assertEqual(process.returncode, 0, process.stderr)
            self.assertIn('WITH_ARM64_TRANSLATION=false', process.stdout.splitlines())

    def test_invalid_or_unprepared_features_are_rejected(self):
        valid = {key: False for key in custom.FEATURES}
        for changes in ({'unknown': True}, {'with_gralloc_fix': 'true'},
                        {'with_microg': True}):
            with self.subTest(changes=changes), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / 'config').mkdir()
                (root / 'config/features.json').write_text(json.dumps(dict(valid, **changes)))
                with patch.object(custom, 'ROOT', root), self.assertRaises(RuntimeError):
                    custom.read_features()

    def test_arm64_translation_requires_extracted_file_lock(self):
        features = dict({key: False for key in custom.FEATURES}, with_arm64_translation=True)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'config').mkdir()
            (root / 'config/features.json').write_text(json.dumps(features))
            with patch.object(custom, 'ROOT', root):
                with self.assertRaisesRegex(RuntimeError, 'Native Bridge'):
                    custom.read_features()
                (root / 'config/nativebridge-lock.json').write_text('{}')
                self.assertTrue(custom.read_features()['with_arm64_translation'])


    def test_microg_requires_apk_lock_and_signature_spoofing(self):
        base = {key: False for key in custom.FEATURES}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'config').mkdir()
            with patch.object(custom, 'ROOT', root):
                (root / 'config/features.json').write_text(json.dumps(dict(base, with_microg=True)))
                with self.assertRaisesRegex(RuntimeError, 'microG APK 기록'):
                    custom.read_features()
                (root / 'config/microg-apk-lock.json').write_text('{}')
                with self.assertRaisesRegex(RuntimeError, 'signature spoofing'):
                    custom.read_features()
                (root / 'config/features.json').write_text(json.dumps(
                    dict(base, with_microg=True, with_microg_signature_spoofing=True)))
                self.assertTrue(custom.read_features()['with_microg'])


class MicrogImportTests(unittest.TestCase):
    def test_signer_rules(self):
        import microg_import
        source = json.loads((Path(custom.ROOT) / 'config/microg-source-lock.json').read_text(encoding='utf-8'))
        microg, google = source['real_certificate_sha256'], source['compatibility_certificate_sha256']
        rotated, other = '7' * 64, '0' * 64
        self.assertTrue(microg_import.signer_ok(dict(signers=[microg]), 'microg'))
        self.assertFalse(microg_import.signer_ok(dict(signers=[google]), 'microg'))
        self.assertTrue(microg_import.signer_ok(dict(signers=[google]), 'google'))
        self.assertTrue(microg_import.signer_ok(dict(signers=[rotated], lineage=[google, rotated]), 'google'))
        # 계보가 Google 원래 인증서에서 시작하지 않거나 현재 서명으로 끝나지 않으면 거부합니다.
        self.assertFalse(microg_import.signer_ok(dict(signers=[rotated], lineage=[other, rotated]), 'google'))
        self.assertFalse(microg_import.signer_ok(dict(signers=[other], lineage=[google, rotated]), 'google'))
        self.assertFalse(microg_import.signer_ok(dict(signers=[microg]), 'google'))

    def test_play_store_does_not_get_verification_agent(self):
        import microg_import
        allowed = microg_import.ALLOWED_PRIVILEGED['com.android.vending']
        self.assertIn('android.permission.INSTALL_PACKAGES', allowed)
        self.assertNotIn('android.permission.PACKAGE_VERIFICATION_AGENT', allowed)
        self.assertIn('com.google.android.finsky.setup.VpaService',
                      microg_import.APPS['com.android.vending']['disabled_components'])
        self.assertTrue(microg_import.APPS['com.android.vending']['allow_in_power_save'])


class NativeBridgeImportTests(unittest.TestCase):
    def test_excluded_binfmt_files_are_never_selected(self):
        import nativebridge_import
        for name in ('etc/init/ndk_translation.rc', 'etc/binfmt_misc/arm64_exe'):
            self.assertTrue(any(name.startswith(item) for item in nativebridge_import.EXCLUDED), name)
        self.assertFalse(any(path.startswith(nativebridge_import.EXCLUDED) for path in nativebridge_import.SINGLE))


class CustomOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='redroid-custom-test-')
        self.addCleanup(self.temporary.cleanup)
        self.source = Path(self.temporary.name)
        state = self.source / '.repo/custom-redroid/official-patches.json'
        state.parent.mkdir(parents=True)
        state.write_text('{"projects": {}}')
        self.phase = SimpleNamespace(source=self.source, state_path=state,
            lock={'official_patch_status': 'complete'}, verify_patch_state=lambda _state: None, snapshot=lambda: {})
        self.plan = dict(files={'vendor/custom/owned.txt': hashlib.sha256(b'expected').hexdigest()}, originals={})

    def test_force_cannot_overwrite_unrecognized_user_edit(self):
        target = self.source / 'vendor/custom/owned.txt'
        target.parent.mkdir(parents=True)
        target.write_text('user edit')
        with self.assertRaisesRegex(RuntimeError, '덮어쓰지 않습니다'):
            custom.verify_tree(self.phase, self.plan, final=False)
        self.assertEqual(target.read_text(), 'user edit')

    def test_unknown_file_in_custom_tree_is_preserved_and_rejected(self):
        target = self.source / 'vendor/custom/owned.txt'
        target.parent.mkdir(parents=True)
        target.write_bytes(b'expected')
        extra = target.parent / 'user-extra.txt'
        extra.write_text('preserve')
        with self.assertRaisesRegex(RuntimeError, '등록되지 않은'):
            custom.verify_tree(self.phase, self.plan)
        self.assertEqual(extra.read_text(), 'preserve')

    def test_stale_file_from_previous_plan_is_removable_only_if_unchanged(self):
        target = self.source / 'vendor/custom/owned.txt'
        target.parent.mkdir(parents=True)
        target.write_bytes(b'expected')
        stale = target.parent / 'old.apk'
        stale.write_bytes(b'old')
        previous = dict(files={'vendor/custom/old.apk': hashlib.sha256(b'old').hexdigest()})
        custom.verify_tree(self.phase, self.plan, previous, final=False)
        self.assertEqual(custom.stale_files(self.phase, self.plan, previous), [stale])
        with self.assertRaisesRegex(RuntimeError, '등록되지 않은'):
            custom.verify_tree(self.phase, self.plan)
        stale.write_bytes(b'user edit')
        with self.assertRaisesRegex(RuntimeError, '지우지 않습니다'):
            custom.verify_tree(self.phase, self.plan, previous, final=False)

    def test_symlink_in_owned_path_is_rejected(self):
        target = self.source / 'other'
        target.mkdir()
        (self.source / 'vendor').symlink_to(target, target_is_directory=True)
        with self.assertRaisesRegex(RuntimeError, 'symlink'):
            custom.checked_destination(self.source, 'vendor/custom/owned.txt')


if __name__ == '__main__':
    unittest.main()
