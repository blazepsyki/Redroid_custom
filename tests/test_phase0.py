import argparse
import contextlib
import copy
import importlib.util
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location('phase0', PROJECT / 'scripts/phase0.py')
phase0 = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(phase0)


class ManifestTests(unittest.TestCase):
    def test_reject_mutable_revision_and_duplicate_project(self):
        projects = [dict(path='device/redroid', name='device_redroid', commit='a' * 40)]
        for data in (
            b'<manifest><project path="device/redroid" name="device_redroid" revision="master"/></manifest>',
            ('<manifest>' + f'<project path="device/redroid" name="device_redroid" revision="{"a" * 40}"/>' * 2
             + '</manifest>').encode(),
        ):
            with self.subTest(data=data), self.assertRaises(phase0.PhaseError):
                phase0.validate_manifest(data, projects)

    def test_reject_path_and_symlink_escape(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source'
            source.mkdir()
            (source / 'outside').symlink_to(root, target_is_directory=True)
            for relative in ('../other', 'outside/other', '.'):
                with self.subTest(relative=relative), self.assertRaises(phase0.PhaseError):
                    phase0.safe_project(source, relative)


class OfficialPatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cache = Path(os.environ.get('REDROID_TEST_CACHE', str(Path.home() / '.cache/custom-redroid')))
        cls.minijail = cls.cache / 'upstream/minijail'
        cls.patch_repo = cls.cache / 'upstream/redroid-patches'
        if not cls.minijail.is_dir() or not cls.patch_repo.is_dir():
            raise unittest.SkipTest('실제 upstream minijail과 redroid-patches 조사 checkout이 필요합니다.')
        cls.original_lock = json.loads((PROJECT / 'config/source-lock.json').read_text())
        cls.original_patch = cls.original_lock['redroid_patches']['patches'][0]
        assert cls.original_patch['project'] == 'external/minijail'

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='redroid-phase0-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / 'source'
        self.target = self.source / 'external/minijail'
        self.target.parent.mkdir(parents=True)
        subprocess.run(['git', 'clone', '--quiet', '--shared', str(self.minijail), str(self.target)], check=True,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.base = phase0.git(self.target, 'rev-parse', 'HEAD')
        self.lock = copy.deepcopy(self.original_lock)
        self.lock['redroid_patches']['patches'] = [copy.deepcopy(self.original_patch)]
        manifest = f'<manifest><project path="external/minijail" revision="{self.base}"/></manifest>'.encode()
        (self.root / 'config').mkdir()
        (self.root / 'config/source-manifest.xml').write_bytes(manifest)
        self.lock['sync'] = dict(status='complete', manifest_file='config/source-manifest.xml',
                                 manifest_sha256=phase0.sha256(manifest), project_count=1)
        phase0.write_json(self.root / 'config/source-lock.json', self.lock)
        root_patch = patch.object(phase0, 'ROOT', self.root)
        root_patch.start()
        self.addCleanup(root_patch.stop)
        environment = patch.dict(os.environ, REDROID_SOURCE_DIR=str(self.source), REDROID_CACHE_DIR=str(self.cache))
        environment.start()
        self.addCleanup(environment.stop)
        self.args = argparse.Namespace(stage='apply', dry_run=False, force=False, init_only=False)
        self.phase = phase0.Phase0(self.args)
        self.patch_dir = self.patch_repo / self.lock['redroid_patches']['directory']
        self.output = io.StringIO()

    def apply(self):
        with patch.object(self.phase, 'patch_inventory', return_value=self.patch_dir), contextlib.redirect_stdout(self.output):
            self.phase.apply()

    def test_apply_real_official_patch_and_rerun_without_duplicate(self):
        self.apply()
        head = phase0.git(self.target, 'rev-parse', 'HEAD')
        self.assertNotEqual(head, self.base)
        self.assertEqual(phase0.git(self.target, 'show', '-s', '--format=%ae'), 'ziyang.zhou@outlook.com')
        identity = phase0.command(['git', 'config', 'user.email'], cwd=self.target, env=self.phase.environment)
        self.assertEqual(phase0.git(self.target, 'show', '-s', '--format=%ce'), identity)
        self.assertNotIn('Co-authored-by: Codex', phase0.git(self.target, 'show', '-s', '--format=%B'))
        self.apply()
        self.assertEqual(phase0.git(self.target, 'rev-parse', 'HEAD'), head)
        state = json.loads(self.phase.state_path.read_text())
        self.assertEqual(len(state['projects']['external/minijail']['applied']), 1)
        self.assertEqual(self.phase.lock['official_patch_status'], 'complete')

    def test_untracked_file_is_preserved_even_with_force(self):
        marker = self.target / 'user-work.txt'
        marker.write_text('preserve')
        self.args.force = True
        with self.assertRaises(phase0.PhaseError):
            self.apply()
        self.assertEqual(marker.read_text(), 'preserve')
        self.assertEqual(phase0.git(self.target, 'rev-parse', 'HEAD'), self.base)

    def test_real_git_am_failure_preserves_conflict_and_failure_status(self):
        self.patch_dir = self.root / 'conflicting-patches'
        bad_patch = self.patch_dir / self.original_patch['file']
        bad_patch.parent.mkdir(parents=True)
        original = (self.patch_repo / self.lock['redroid_patches']['directory'] / self.original_patch['file']).read_text()
        bad_patch.write_text(original.replace('Install the syscall filter.', 'Context that does not exist.'))
        with self.assertRaises(phase0.PhaseError):
            self.apply()
        git_dir = Path(phase0.git(self.target, 'rev-parse', '--absolute-git-dir'))
        self.assertTrue((git_dir / 'rebase-apply').is_dir())
        self.assertEqual(phase0.git(self.target, 'rev-parse', 'HEAD'), self.base)
        self.assertEqual(self.phase.lock['official_patch_status'], 'failed')
        with self.assertRaisesRegex(phase0.PhaseError, '진행 중인 Git 작업'):
            self.apply()

    def test_success_exit_with_pending_am_is_not_accepted(self):
        def faulty_command(*_args, **_kwargs):
            git_dir = Path(phase0.git(self.target, 'rev-parse', '--absolute-git-dir'))
            (git_dir / 'rebase-apply').mkdir()
        with patch.object(self.phase, 'logged', side_effect=faulty_command), self.assertRaises(phase0.PhaseError):
            self.apply()
        self.assertEqual(self.phase.lock['official_patch_status'], 'failed')

    def test_recorded_head_mismatch_is_rejected(self):
        phase0.write_json(self.phase.state_path, dict(
            patch_repository_commit=self.lock['redroid_patches']['commit'],
            projects={'external/minijail': dict(base=self.base, head='0' * 40, applied=[])},
        ))
        with self.assertRaisesRegex(phase0.PhaseError, '소스 SHA'):
            self.apply()

    def test_changed_journal_digest_is_rejected(self):
        self.apply()
        state = json.loads(self.phase.state_path.read_text())
        state['projects']['external/minijail']['applied'][0]['sha256'] = '0' * 64
        phase0.write_json(self.phase.state_path, state)
        with self.assertRaisesRegex(phase0.PhaseError, '기록의 파일 또는 해시'):
            self.apply()

    def test_full_patch_inventory_matches_lock(self):
        self.phase.lock['redroid_patches'] = self.original_lock['redroid_patches']
        self.assertEqual(self.phase.patch_inventory(self.patch_repo),
                         self.patch_repo / self.original_lock['redroid_patches']['directory'])

    def test_changed_patch_digest_is_rejected(self):
        self.phase.lock['redroid_patches'] = copy.deepcopy(self.original_lock['redroid_patches'])
        self.phase.lock['redroid_patches']['patches'][0]['sha256'] = '0' * 64
        with self.assertRaisesRegex(phase0.PhaseError, '해시'):
            self.phase.patch_inventory(self.patch_repo)


if __name__ == '__main__':
    unittest.main()
