import contextlib
import copy
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'scripts'))
import collect_report as report
from phase0 import PhaseError, sha256


class ReportTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='redroid-report-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.runtime_dir = self.root / 'build-logs/current-runtime'
        self.runtime_dir.mkdir(parents=True)
        self.source = self.root / 'source'
        self.enterContext(patch.object(report, 'ROOT', self.root))
        self.enterContext(patch.dict(os.environ, REDROID_SOURCE_DIR=str(self.source)))
        self.image = 'sha256:' + 'a' * 64
        self.official = 'b' * 64
        self.record = dict(
            schema_version=1, image_id=self.image,
            positive_selfcheck=dict(system_spoofs_signature=True, services_correct_signature=True),
            negative_resigned_gmscore=dict(signer_sha256='c' * 64, apk_sha256='d' * 64,
                                          system_spoofs_signature=False, services_correct_signature=False),
            restored_official_selfcheck=dict(system_spoofs_signature=True, services_correct_signature=True))

    def write(self, relative, value):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(json.dumps(value).encode())
        return path

    def evidence(self, record=None, relative='build-logs/current-runtime/microg-signature-test.json'):
        return self.write(relative, self.record if record is None else record)

    def negative(self, specified=None):
        return report.negative_test(self.runtime_dir, specified, self.image, self.official)

    def fixture(self):
        plan = 'e' * 64
        self.write('build-logs/custom-package-state.json', dict(
            status='packaged', custom_plan_sha256=plan, package_id='current', image_id=self.image,
            image_tag='redroid:current', dist_dir='dist/current'))
        self.write('build-logs/custom-build-state.json', dict(status='built', custom_plan_sha256=plan, jobs=8))
        sbom = self.write('source/sbom.json', dict(packages=[]))
        self.write('build-logs/custom-sbom-state.json', dict(
            status='sbom_generated', custom_plan_sha256=plan,
            sbom=dict(path='sbom.json', sha256=sha256(sbom.read_bytes()), packages=0)))
        notice = self.source / 'out/target/product/product/system/etc/NOTICE.xml.gz'
        notice.parent.mkdir(parents=True)
        notice.write_bytes(b'notice')
        self.write('build-logs/preflight.json', dict(memory_total_bytes=64 * 2**30))
        self.write('config/source-lock.json', dict(
            aosp=dict(tag='android-15.0.0_r36', manifest_commit='manifest', build_id='build'),
            redroid_manifest=dict(commit='redroid'), redroid_patches=dict(commit='patches', patches=[]),
            target=dict(product='product', lunch='product-release-userdebug')))
        self.write('config/gralloc-source-lock.json', dict(source=dict(commit='gralloc'), patches=[]))
        self.write('config/nativebridge-lock.json', dict(
            package=dict(path='sdk', revision=9, license='sdk-license'), archive_sha256='f' * 64, files=[]))
        self.write('config/microg-apk-lock.json', dict(apps=[
            dict(package='com.google.android.gms', version_name='1', signers=[self.official], signer='microg'),
            dict(package='com.android.vending', version_name='2', signers=['c' * 64],
                 lineage=['d' * 64], signer='google')]))
        self.write('config/builder-lock.json', dict(
            wsl_distribution='private-build-distribution', docker_engine='29', builder_id='builder'))
        self.write('config/microg-source-lock.json', dict(
            real_certificate_sha256=self.official, target_runtime_status='passed_selfcheck_and_negative_resign'))
        checks = {name: {'pass': True} for name in ('arm64', 'gpu', 'crash')}
        checks['gpu']['gles'] = 'GLES: Intel'
        self.write('build-logs/current-runtime/verify-current.json', dict(
            image_id=self.image, checks=checks, result=dict(boot='PASS')))
        self.write('build-logs/current-runtime/coldboot-report.json', [dict(
            label='coldboot-1', gralloc_load=dict(passed=30, iterations=30), p010_rejections=12,
            allocator_pid='100', allocator_pid_after='100',
            firefox=dict(install=True, page_render=True, libmozglue_loaded=True))])
        (self.runtime_dir / 'microg-selfcheck.txt').write_bytes('\n'.join(
            f'{label!r}\nchecked=true' for label in (
                'System spoofs signature:', 'microG Services has correct signature:',
                'microG Companion / Play Store has correct signature:')).encode())
        (self.root / 'docs/reports').mkdir(parents=True)

    def generate(self, *args):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return report.main(['--runtime-dir', 'build-logs/current-runtime', *args])

    def test_missing_default_does_not_read_historical_result(self):
        self.write('build-logs/legacy-run/runtime_dir-fixed-microg-test.json', self.record)
        self.assertEqual(self.negative()['status'], 'NOT TESTED')

    def test_explicit_missing_evidence_is_an_error(self):
        with self.assertRaises(PhaseError):
            self.negative('missing.json')

    def test_current_evidence_can_be_default_or_explicit(self):
        default = self.evidence()
        explicit = self.evidence(relative='evidence/custom.json')
        for specified, expected in ((None, default), ('evidence/custom.json', explicit), (str(explicit), explicit)):
            with self.subTest(specified=specified):
                value = self.negative(specified)
                self.assertEqual(value['status'], 'PASS')
                self.assertEqual(value['data'], expected.read_bytes())

    def test_other_image_is_rejected(self):
        self.evidence(dict(self.record, image_id='sha256:' + 'f' * 64))
        with self.assertRaisesRegex(PhaseError, '이미지 ID'):
            self.negative()

    def test_non_boolean_results_are_rejected(self):
        for value in ('false', 0, None):
            record = copy.deepcopy(self.record)
            record['negative_resigned_gmscore']['system_spoofs_signature'] = value
            self.evidence(record)
            with self.subTest(value=value), self.assertRaisesRegex(PhaseError, 'boolean'):
                self.negative()

    def test_official_signer_is_not_a_negative_test(self):
        record = copy.deepcopy(self.record)
        record['negative_resigned_gmscore']['signer_sha256'] = self.official
        self.evidence(record)
        with self.assertRaisesRegex(PhaseError, '공식 microG'):
            self.negative()

    def test_invalid_apk_hash_is_rejected(self):
        record = copy.deepcopy(self.record)
        record['negative_resigned_gmscore']['apk_sha256'] = 'unknown'
        self.evidence(record)
        with self.assertRaisesRegex(PhaseError, 'apk_sha256'):
            self.negative()

    def test_failed_control_or_spoof_acceptance_cannot_pass(self):
        for name, value in (('positive_selfcheck', False), ('restored_official_selfcheck', False),
                            ('negative_resigned_gmscore', True)):
            record = copy.deepcopy(self.record)
            record[name]['system_spoofs_signature'] = value
            self.evidence(record)
            with self.subTest(name=name):
                self.assertEqual(self.negative()['status'], 'FAIL')

    def test_missing_controls_or_schema_are_rejected(self):
        for name in ('positive_selfcheck', 'restored_official_selfcheck', 'schema_version'):
            record = copy.deepcopy(self.record)
            del record[name]
            self.evidence(record)
            with self.subTest(name=name), self.assertRaises(PhaseError):
                self.negative()

    def test_report_without_history_does_not_inherit_stale_success(self):
        self.fixture()
        (self.root / 'build-logs/preflight.json').unlink()
        self.assertEqual(self.generate(), 0)
        text = (self.root / 'docs/reports/final.md').read_text()
        self.assertIn('Unsupported signer negative tests: NOT TESTED', text)
        self.assertIn('FrameworksServicesTests): NOT TESTED', text)
        self.assertNotIn('framework tests: PASS', text)
        self.assertNotIn('legacy-run', text)

    def test_report_archives_evidence_and_force_removes_stale_copy(self):
        self.fixture()
        evidence = self.evidence()
        original = evidence.read_bytes()
        self.assertEqual(self.generate(), 0)
        public_report = (self.root / 'docs/reports/final.md').read_text()
        self.assertNotIn(str(self.root), public_report)
        self.assertNotIn('private-build-distribution', public_report)
        self.assertNotIn('build-logs/current-runtime', public_report)
        folder = self.root / 'dist/report-current'
        self.assertEqual((folder / evidence.name).read_bytes(), original)
        self.assertIn(f'{sha256(original)}  {evidence.name}', (folder / 'SHA256SUMS').read_text())
        self.assertIn('Unsupported signer negative tests: PASS', (self.root / 'docs/reports/final.md').read_text())
        evidence.unlink()
        self.assertEqual(self.generate('--force'), 0)
        self.assertFalse((folder / evidence.name).exists())
        self.assertNotIn(evidence.name, (folder / 'SHA256SUMS').read_text())

    def test_dry_run_rejects_wrong_image_without_writing_report(self):
        self.fixture()
        self.evidence(dict(self.record, image_id='sha256:' + 'f' * 64))
        self.assertEqual(self.generate('--dry-run'), 1)
        self.assertFalse((self.root / 'dist').exists())
        self.assertFalse((self.root / 'docs/reports/final.md').exists())


if __name__ == '__main__':
    unittest.main()
