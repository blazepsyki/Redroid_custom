import hashlib
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'scripts'))
import apps_import
import custom
from phase0 import PhaseError


class PreinstalledAppTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.apps = []
        for index, (package, expected) in enumerate(apps_import.APPS.items()):
            data = f'original-apk-{index}'.encode()
            filename = f'app-{index}.apk'
            (self.root / filename).write_bytes(data)
            splits = []
            if expected['splits']:
                split_data = f'original-split-{index}'.encode()
                (self.root / f'app-{index}-split.apk').write_bytes(split_data)
                splits.append(dict(name='config.xhdpi', file=f'app-{index}-split.apk',
                                   sha256=hashlib.sha256(split_data).hexdigest(), size=len(split_data)))
            self.apps.append(dict(package=package, module=expected['module'], signers=expected['signers'],
                                  privileged=False, file=filename, sha256=hashlib.sha256(data).hexdigest(),
                                  skip_preprocessed_apk_checks=index % 2 == 1, version_code=114, version_name='fixture', min_sdk=23,
                                  extract_native_libs=False, native_files=[], splits=splits))
        self.lock = self.root / 'apps-lock.json'
        self.save()
        self.enterContext(patch.object(apps_import, 'INPUTS', self.root))
        self.enterContext(patch.object(apps_import, 'LOCK', self.lock))

    def save(self):
        self.lock.write_text(json.dumps(dict(apps=self.apps)))

    def test_original_apks_generate_unprivileged_preprocessed_modules(self):
        files = custom.app_files()
        blueprint = files['vendor/custom/apps/Android.bp'].decode()
        self.assertEqual(blueprint.count('privileged: false'), len(self.apps))
        self.assertEqual(blueprint.count('preprocessed: true'), len(self.apps))
        self.assertEqual(blueprint.count('skip_preprocessed_apk_checks: true'), len(self.apps) // 2)
        for app in self.apps:
            self.assertEqual(files[f'vendor/custom/apps/{app["module"]}.apk'], (self.root / app['file']).read_bytes())
            self.assertIn(app['module'], files['vendor/custom/apps/apps.mk'].decode())

    def test_changed_apk_is_rejected(self):
        (self.root / self.apps[0]['file']).write_bytes(b'modified')
        with self.assertRaisesRegex(PhaseError, 'APK가 기록과 다릅니다'):
            apps_import.verify_apks()

    def test_wrong_signer_or_privileged_setting_is_rejected(self):
        for updates in (dict(signers=['0' * 64]), dict(privileged=True), dict(module='AnotherModule')):
            with self.subTest(updates=updates), self.assertRaises(PhaseError):
                apps_import.validate_app(dict(self.apps[0], **updates))

    def test_missing_or_duplicate_package_is_rejected(self):
        for values in ([self.apps[0]], [self.apps[0], self.apps[0]]):
            self.lock.write_text(json.dumps(dict(apps=values)))
            with self.subTest(values=values), self.assertRaises(PhaseError):
                apps_import.verify_apks()

    def test_input_path_escape_is_rejected(self):
        self.apps[0]['file'] = '../outside.apk'
        self.save()
        with self.assertRaisesRegex(PhaseError, '입력 경로'):
            apps_import.verify_apks()

    def split_app(self):
        return next(app for app in self.apps if app['splits'])

    def test_split_apk_is_copied_next_to_base(self):
        app = self.split_app()
        split = app['splits'][0]
        files = custom.app_files()
        self.assertEqual(files[f'vendor/custom/apps/splits/{app["module"]}/split_config.xhdpi.apk'],
                         (self.root / split['file']).read_bytes())
        makefile = files['vendor/custom/apps/Android.mk'].decode()
        self.assertIn(f'LOCAL_MODULE_PATH := $(TARGET_OUT)/app/{app["module"]}\n', makefile)
        self.assertIn('LOCAL_MODULE_STEM := split_config.xhdpi.apk', makefile)

    def test_changed_or_missing_split_is_rejected(self):
        app = self.split_app()
        (self.root / app['splits'][0]['file']).write_bytes(b'modified')
        with self.assertRaisesRegex(PhaseError, 'APK가 기록과 다릅니다'):
            apps_import.verify_apks()
        app['splits'] = []
        self.save()
        with self.assertRaisesRegex(PhaseError, '분할 APK'):
            apps_import.verify_apks()

    def test_newer_minimum_sdk_is_rejected(self):
        self.apps[0]['min_sdk'] = 36
        self.save()
        with self.assertRaisesRegex(PhaseError, 'Android 15'):
            apps_import.verify_apks()

    def native_fixture(self):
        app = self.apps[1]
        data = b'\x7fELF' + b'fixture-native-library'
        with zipfile.ZipFile(self.root / app['file'], 'w') as archive:
            archive.writestr('lib/x86_64/libfixture.so', data)
        app['sha256'] = hashlib.sha256((self.root / app['file']).read_bytes()).hexdigest()
        app['extract_native_libs'] = True
        app['native_files'] = [dict(member='lib/x86_64/libfixture.so', sha256=hashlib.sha256(data).hexdigest(), size=len(data))]
        self.save()
        return app, data

    def test_native_libraries_are_installed_per_app_without_changing_apk(self):
        app, data = self.native_fixture()
        files = custom.app_files()
        self.assertEqual(files[f'vendor/custom/apps/native/{app["module"]}/libfixture.so'], data)
        self.assertEqual(files[f'vendor/custom/apps/{app["module"]}.apk'], (self.root / app['file']).read_bytes())
        self.assertIn(f'$(TARGET_OUT)/app/{app["module"]}/lib/x86_64', files['vendor/custom/apps/Android.mk'].decode())

    def test_changed_embedded_library_hash_is_rejected(self):
        app, _ = self.native_fixture()
        app['native_files'][0]['sha256'] = '0' * 64
        self.save()
        with self.assertRaisesRegex(PhaseError, '라이브러리 해시'):
            custom.app_files()


if __name__ == '__main__':
    unittest.main()
