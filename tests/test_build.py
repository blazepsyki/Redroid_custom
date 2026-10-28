import argparse
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'scripts'))
import build
import custom
import apps_import


class BuildResultTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='redroid-build-test-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / 'config').mkdir()
        (self.root / 'config/builder-lock.json').write_text(json.dumps(dict(builder_id='fixture-builder')))
        state_path = self.root / 'source/.repo/custom-redroid/official-patches.json'
        build.write_json(state_path, dict(patch_repository_commit='fixture-patches',
                                          projects={'fixture': dict(applied=['fixture'])}))
        target = dict(product='redroid_x86_64_only', release='ap3a', variant='userdebug',
                      lunch='redroid_x86_64_only-ap3a-userdebug')
        self.phase = SimpleNamespace(
            source=self.root / 'source', state_path=state_path,
            lock=dict(official_patch_status='complete', redroid_patches=dict(commit='fixture-patches', patches=['fixture']),
                      sync=dict(manifest_sha256='fixture-manifest'), aosp=dict(build_id='BP1A.250505.005.D1'), target=target),
            preflight=lambda: None, verify_patch_state=lambda _state: None, verify_snapshot=lambda _state: None,
        )
        self.args = argparse.Namespace(configure_only=True, dry_run=False, force=False)
        root_patch = patch.object(build, 'ROOT', self.root)
        root_patch.start()
        self.addCleanup(root_patch.stop)
        phase_patch = patch.object(build, 'Phase0', return_value=self.phase)
        phase_patch.start()
        self.addCleanup(phase_patch.stop)

    def result(self):
        return json.loads((self.root / 'build-logs/build-state.json').read_text())

    def successful_command(self, args, log_name, wrong_api=False):
        (self.root / 'build-logs' / log_name).write_text('fixture command exited successfully\n')
        metadata = self.phase.source / 'out/custom-redroid' / args[-1]
        metadata.mkdir(parents=True)
        properties = dict(TARGET_PRODUCT='redroid_x86_64_only', TARGET_BUILD_VARIANT='userdebug',
                          PLATFORM_VERSION='15', PLATFORM_SDK_VERSION='34' if wrong_api else '35',
                          BUILD_ID='BP1A.250505.005.D1', PRODUCT_OUT='out/target/product/redroid_x86_64_only',
                          TARGET_CPU_ABI_LIST='x86_64', WITH_GRALLOC_FIX='true',
                          WITH_MICROG_SIGNATURE_SPOOFING='true', WITH_ARM64_TRANSLATION='false')
        (metadata / 'target-vars.txt').write_text(''.join(f"{key}='{value}'\n" for key, value in properties.items()))
        (metadata / 'target-release.txt').write_text('ap3a\n')

    def test_command_failure_is_recorded_without_success(self):
        def failing_command(*_args):
            raise build.PhaseError('fixture compiler failure')
        self.phase.logged = failing_command
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(build.PhaseError):
            build.run(self.args)
        self.assertEqual(self.result()['status'], 'failed')
        self.assertIsNotNone(self.result()['finished_at'])
        self.assertFalse(self.result()['runtime_verified'])

    def test_success_exit_with_wrong_target_api_is_rejected(self):
        self.phase.logged = lambda args, log: self.successful_command(args, log, wrong_api=True)
        with self.assertRaisesRegex(build.PhaseError, '실제 빌드 속성'):
            build.run(self.args)
        self.assertEqual(self.result()['status'], 'failed')

    def test_success_exit_without_images_is_not_a_successful_build(self):
        self.args.configure_only = False
        self.phase.logged = self.successful_command
        with self.assertRaisesRegex(build.PhaseError, '산출물이 없습니다'):
            build.run(self.args)
        self.assertEqual(self.result()['status'], 'failed')
        self.assertNotIn('artifacts', self.result())

    def test_custom_module_build_is_separate_from_full_image_success(self):
        self.args.configure_only, self.args.custom, self.args.modules = False, True, True
        features = dict(with_gralloc_fix=True, with_microg_signature_spoofing=True,
                        with_arm64_translation=False, with_microg=False, with_validation_tools=True,
                        with_uhid_input=False)
        custom_state = dict(features=features, plan_sha256='fixture-custom-plan')
        app = dict(module='CustomFixture', sha256=build.sha256(b'fixture module'), native_files=[])
        def complete_modules(args, log):
            self.successful_command(args, log)
            for name in ('vendor/lib64/hw/gralloc.gbm.so', 'vendor/bin/custom_gralloc_format_test',
                         'system/framework/services.jar', 'system/app/CustomFixture/CustomFixture.apk'):
                path = self.phase.source / 'out/target/product/redroid_x86_64_only' / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b'fixture module')
        self.phase.logged = complete_modules
        with patch.object(custom, 'verify_applied', return_value=custom_state), \
                patch.object(apps_import, 'verify_apks', return_value=dict(apps=[app])), \
                contextlib.redirect_stdout(io.StringIO()):
            build.run(self.args)
        state = json.loads((self.root / 'build-logs/custom-build-state.json').read_text())
        self.assertEqual(state['status'], 'modules_built')
        self.assertFalse((self.root / 'build-logs/build-state.json').exists())
        self.assertFalse(state['runtime_verified'])
        self.assertEqual(len(state['artifacts']), 4)


if __name__ == '__main__':
    unittest.main()
