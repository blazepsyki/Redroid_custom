#!/usr/bin/env python3
import argparse
import contextlib
import fcntl
import hashlib
import json
import os
import platform
import shlex
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class PhaseError(RuntimeError):
    pass


def require(condition, message):
    if not condition:
        raise PhaseError(message)


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def command(args, cwd=None, capture=True, env=None):
    result = subprocess.run([str(arg) for arg in args], cwd=cwd, text=True,
                            stdout=subprocess.PIPE if capture else None,
                            stderr=subprocess.PIPE if capture else None, env=env)
    if result.returncode:
        raise PhaseError(f'명령 실패 ({result.returncode}): {shlex.join(map(str, args))}\n'
                         f'{result.stderr or ""}')
    return result.stdout.strip() if capture else ''


def git(path, *args):
    return command(['git', '-C', path, *args])


def safe_project(source, relative):
    target = (source / relative).resolve()
    require(target != source and target.is_relative_to(source), f'소스 밖의 프로젝트 경로입니다: {relative}')
    return target


def clean_project(path):
    require(Path(git(path, 'rev-parse', '--show-toplevel')).resolve() == path.resolve(),
            f'독립된 Git 프로젝트가 아닙니다: {path}')
    git_dir = Path(git(path, 'rev-parse', '--absolute-git-dir'))
    for state in ('rebase-apply', 'rebase-merge', 'MERGE_HEAD', 'CHERRY_PICK_HEAD'):
        require(not (git_dir / state).exists(), f'진행 중인 Git 작업이 있습니다: {path}: {state}')
    require(not git(path, 'status', '--porcelain', '--untracked-files=all'),
            f'수정 또는 미추적 파일이 있습니다: {path}')


def validate_manifest(data, projects):
    nodes = ET.fromstring(data).findall('project')
    actual = {node.get('path'): (node.get('name'), node.get('revision')) for node in nodes}
    expected = {project['path']: (project['name'], project['commit']) for project in projects}
    require(len(nodes) == len(actual) and actual == expected, '고정 manifest의 프로젝트 또는 SHA가 다릅니다.')


class Phase0:
    def __init__(self, args):
        self.args = args
        self.source = Path(os.environ.get('REDROID_SOURCE_DIR', str(Path.home() / 'work/redroid-aosp15'))).resolve()
        self.cache = Path(os.environ.get('REDROID_CACHE_DIR', str(Path.home() / '.cache/custom-redroid'))).resolve()
        self.lock_path = ROOT / 'config/source-lock.json'
        self.lock = json.loads(self.lock_path.read_text(encoding='utf-8'))
        self.manifest = ROOT / self.lock['redroid_manifest']['pinned_file']
        self.log_dir = ROOT / 'build-logs'
        self.state_path = self.source / '.repo/custom-redroid/official-patches.json'
        config = os.environ.get('REDROID_GIT_CONFIG')
        self.environment = os.environ.copy()
        if config:
            require(Path(config).is_file(), f'Git 설정 파일이 없습니다: {config}')
            self.environment['GIT_CONFIG_GLOBAL'] = config

    def update_lock(self):
        write_json(self.lock_path, self.lock)

    @contextlib.contextmanager
    def exclusive(self):
        directory = self.cache / 'locks'
        directory.mkdir(parents=True, exist_ok=True)
        key = sha256(str(self.source).encode())
        with (directory / f'{key}.lock').open('a') as stream:
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise PhaseError('동일한 소스 경로에서 다른 단계가 실행 중입니다.') from error
            yield

    def logged(self, args, name, cwd=None):
        self.log_dir.mkdir(exist_ok=True)
        path = self.log_dir / name
        print(f'실행: {shlex.join(map(str, args))}\n로그: {path}', flush=True)
        with path.open('a', encoding='utf-8') as stream:
            stream.write(f'\n[{datetime.now(timezone.utc).isoformat()}] {shlex.join(map(str, args))}\n')
            stream.flush()
            result = subprocess.run([str(arg) for arg in args], cwd=cwd, env=self.environment,
                                    stdin=subprocess.DEVNULL, stdout=stream, stderr=subprocess.STDOUT)
        if result.returncode:
            tail = '\n'.join(path.read_text(encoding='utf-8', errors='replace').splitlines()[-20:])
            raise PhaseError(f'명령 실패 ({result.returncode}): {path}\n{tail}')

    def preflight(self, record=True):
        require(platform.system() == 'Linux' and platform.machine() == 'x86_64', 'x86_64 Linux가 필요합니다.')
        require(os.geteuid() != 0, '일반 사용자로 실행해 주세요.')
        require(self.source.is_dir(), f'소스 디렉터리가 없습니다: {self.source}')
        filesystem = command(['findmnt', '-n', '-o', 'FSTYPE', '--target', self.source])
        require(filesystem == 'ext4', f'소스에는 ext4가 필요합니다: {filesystem}')
        for tool in ('git', 'repo', 'docker', 'python3'):
            require(shutil.which(tool), f'필수 도구가 없습니다: {tool}')
        command(['git', 'lfs', 'version'])
        command(['docker', 'version', '--format', '{{.Server.Version}}'])
        builder = json.loads((ROOT / 'config/builder-lock.json').read_text(encoding='utf-8'))
        image = os.environ.get('REDROID_BUILDER_IMAGE', builder['builder_tag'])
        image_id = command(['docker', 'image', 'inspect', image, '--format', '{{.Id}}'])
        require(image_id == builder['builder_id'], 'builder 이미지 ID가 builder-lock.json과 다릅니다.')
        memory = {}
        for line in Path('/proc/meminfo').read_text().splitlines():
            key, value = line.split(':', 1)
            memory[key] = int(value.split()[0]) * 1024
        require(memory['MemTotal'] >= 64_000_000_000, 'Linux에 할당된 RAM이 64GB 미만입니다.')
        free = shutil.disk_usage(self.source).free
        require(free >= 400_000_000_000, '소스 경로의 여유 공간이 400GB 미만입니다.')
        backing = os.environ.get('REDROID_BACKING_DIR')
        backing_free = None
        if backing:
            require(Path(backing).is_dir(), f'가상 디스크 저장 드라이브 경로가 없습니다: {backing}')
            backing_free = shutil.disk_usage(backing).free
            require(backing_free >= 400_000_000_000, '가상 디스크 저장 드라이브의 여유 공간이 400GB 미만입니다.')
        data = self.manifest.read_bytes()
        require(sha256(data) == self.lock['redroid_manifest']['pinned_xml_sha256'], '고정 manifest의 해시가 다릅니다.')
        validate_manifest(data, self.lock['redroid_projects'])
        report = dict(checked_at=datetime.now(timezone.utc).isoformat(), source=str(self.source), filesystem=filesystem,
                      memory_total_bytes=memory['MemTotal'], memory_available_bytes=memory['MemAvailable'],
                      swap_total_bytes=memory['SwapTotal'], source_free_bytes=free, backing_free_bytes=backing_free,
                      cpus=os.cpu_count(), builder_id=image_id)
        print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
        if record and not self.args.dry_run:
            write_json(self.log_dir / 'preflight.json', report)

    def ensure_existing_tree_clean(self):
        listing = self.source / '.repo/project.list'
        if listing.is_file():
            for relative in listing.read_text().splitlines():
                path = safe_project(self.source, relative)
                if (path / '.git').exists():
                    clean_project(path)

    def sync(self):
        self.preflight()
        require(not self.state_path.exists(), '공식 패치 적용 기록이 있습니다. 동기화로 패치를 덮어쓰지 않습니다.')
        self.ensure_existing_tree_clean()
        if self.args.dry_run:
            print(f'AOSP manifest: {self.lock["aosp"]["manifest_commit"]}')
            print('예정: repo 초기화, 고정 local manifest 설치' + ('' if self.args.init_only else ', 소스 다운로드와 SHA 기록'))
            return
        aosp = self.lock['aosp']
        manifest_repo = self.source / '.repo/manifests'
        if manifest_repo.exists():
            require(git(manifest_repo, 'rev-parse', 'HEAD') == aosp['manifest_commit'], '기존 AOSP manifest SHA가 다릅니다.')
            clean_project(manifest_repo)
        elif (self.source / '.repo').exists():
            print('이전 repo 초기화 상태를 검사하며 재시도합니다.', flush=True)
        else:
            require(not any(self.source.iterdir()), '빈 디렉터리 또는 이 프로젝트에서 초기화한 repo 트리가 필요합니다.')
        if not manifest_repo.exists() or not (self.source / '.repo/manifest.xml').exists():
            init_args = ['repo', 'init', '-u', aosp['url'], '-b', aosp['manifest_commit'], '--depth=1',
                         '--git-lfs', '--no-clone-bundle', '--no-use-superproject']
            repo_lock = ROOT / 'config/repo-lock.json'
            if repo_lock.is_file():
                repo_spec = json.loads(repo_lock.read_text(encoding='utf-8'))
                init_args.extend(['--repo-url', repo_spec['url'], '--repo-rev', repo_spec['commit']])
            self.logged(init_args, 'source-sync.log', self.source)
        require(git(manifest_repo, 'rev-parse', 'HEAD') == aosp['manifest_commit'], '초기화된 manifest SHA가 다릅니다.')
        require(sha256((manifest_repo / 'default.xml').read_bytes()) == aosp['default_xml_sha256'], 'AOSP default.xml 해시가 다릅니다.')
        local_dir = self.source / '.repo/local_manifests'
        if local_dir.exists():
            extras = [path for path in local_dir.iterdir() if path.name != 'redroid.xml']
            require(not extras, f'기존 local manifest의 추가 파일을 검토해 주세요: {extras}')
        local_dir.mkdir(exist_ok=True)
        destination = local_dir / 'redroid.xml'
        if destination.exists():
            require(destination.read_bytes() == self.manifest.read_bytes(), '기존 redroid.xml이 다릅니다. 자동 교체하지 않습니다.')
        else:
            shutil.copyfile(self.manifest, destination)
        if not self.lock['sync'].get('manifest_file'):
            self.lock['sync']['status'] = 'initialized'
        self.update_lock()
        if self.args.init_only:
            print('repo 초기화와 고정 manifest 설치를 완료했습니다. 전체 소스는 아직 다운로드하지 않았습니다.')
            return
        # 고정된 SHA manifest가 있어도 새 소스 경로에는 아직 checkout이 없으므로 다운로드합니다.
        if self.lock['sync'].get('manifest_file') and not self.args.force and (self.source / 'build/make').is_dir():
            self.verify_snapshot()
            self.lock['sync']['status'] = 'complete'
            self.update_lock()
            print('고정된 전체 소스 SHA가 일치합니다. 다시 다운로드하려면 --force를 사용해 주세요.')
            return
        jobs = int(os.environ.get('REDROID_SYNC_JOBS', '4'))
        require(1 <= jobs <= 16, '동기화 병렬도는 1~16으로 지정해 주세요.')
        self.lock['sync']['status'] = 'running'
        self.update_lock()
        try:
            self.logged(['repo', 'sync', '-c', f'-j{jobs}', '--fail-fast', '--no-clone-bundle', '--no-tags'],
                        'source-sync.log', self.source)
            self.ensure_existing_tree_clean()
            for project in self.lock['redroid_projects']:
                require(git(safe_project(self.source, project['path']), 'rev-parse', 'HEAD') == project['commit'],
                        f'redroid 프로젝트 SHA가 다릅니다: {project["path"]}')
            data = subprocess.check_output(['repo', 'manifest', '-r'], cwd=self.source, env=self.environment)
            nodes = ET.fromstring(data).findall('project')
            require(nodes and all(len(node.get('revision', '')) == 40 for node in nodes), '완전한 SHA manifest가 아닙니다.')
            pinned = self.lock['sync'].get('manifest_sha256')
            require(not pinned or sha256(data) == pinned,
                    '다운로드한 소스 SHA가 고정된 config/source-manifest.xml과 다릅니다. 기록을 덮어쓰지 않습니다.')
            snapshot = ROOT / 'config/source-manifest.xml'
            snapshot.write_bytes(data)
            self.lock['sync'] = dict(status='complete', manifest_file='config/source-manifest.xml',
                                     manifest_sha256=sha256(data), project_count=len(nodes))
            self.update_lock()
            print(f'전체 소스 동기화 및 {len(nodes)}개 프로젝트 SHA 기록을 완료했습니다.')
        except BaseException:
            self.lock['sync']['status'] = 'failed'
            self.update_lock()
            raise

    def snapshot(self):
        sync = self.lock['sync']
        require(sync.get('manifest_file'), '전체 소스 동기화 결과가 없습니다.')
        data = (ROOT / sync['manifest_file']).read_bytes()
        require(sha256(data) == sync['manifest_sha256'], '전체 소스 SHA manifest 해시가 다릅니다.')
        return {node.get('path', node.get('name')): node.get('revision') for node in ET.fromstring(data).findall('project')}

    def verify_snapshot(self, state=None):
        state = state or {'projects': {}}
        for relative, base in self.snapshot().items():
            path = safe_project(self.source, relative)
            clean_project(path)
            expected = state['projects'].get(relative, {}).get('head', base)
            require(git(path, 'rev-parse', 'HEAD') == expected, f'소스 SHA가 기록과 다릅니다: {relative}')

    def patch_inventory(self, patch_repo):
        spec = self.lock['redroid_patches']
        require(git(patch_repo, 'rev-parse', 'HEAD') == spec['commit'], '공식 패치 저장소 SHA가 다릅니다.')
        clean_project(patch_repo)
        directory = patch_repo / spec['directory']
        actual = {path.relative_to(directory).as_posix() for path in directory.rglob('*.patch')}
        require(actual == {patch['file'] for patch in spec['patches']}, '공식 패치 파일 목록이 다릅니다.')
        for patch in spec['patches']:
            require(sha256((directory / patch['file']).read_bytes()) == patch['sha256'],
                    f'공식 패치 해시가 다릅니다: {patch["file"]}')
        return directory

    def apply(self):
        require(self.lock['sync']['status'] == 'complete', '전체 소스 동기화가 먼저 완료되어야 합니다.')
        spec = self.lock['redroid_patches']
        state = json.loads(self.state_path.read_text()) if self.state_path.exists() else dict(
            patch_repository_commit=spec['commit'], projects={})
        require(state['patch_repository_commit'] == spec['commit'], '기존 패치 기록의 저장소 SHA가 다릅니다.')
        self.verify_patch_state(state)
        self.verify_snapshot(state)
        patch_repo = self.cache / 'upstream/redroid-patches'
        if self.args.dry_run:
            if patch_repo.exists():
                self.patch_inventory(patch_repo)
            print(f'예정: SHA 검증 후 공식 패치 {len(spec["patches"])}개를 순서대로 적용합니다.')
            return
        command(['git', 'var', 'GIT_COMMITTER_IDENT'], cwd=self.source, env=self.environment)
        if not patch_repo.exists():
            patch_repo.parent.mkdir(parents=True, exist_ok=True)
            self.logged(['git', 'clone', '--no-checkout', '--filter=blob:none', spec['url'], patch_repo], 'redroid-patches.log')
            self.logged(['git', '-C', patch_repo, 'checkout', '--detach', spec['commit']], 'redroid-patches.log')
        patch_dir = self.patch_inventory(patch_repo)
        bases = self.snapshot()
        for patch in spec['patches']:
            relative = patch['project']
            path = safe_project(self.source, relative)
            entry = state['projects'].setdefault(relative, dict(base=bases[relative], head=bases[relative], applied=[]))
            if any(record['file'] == patch['file'] for record in entry['applied']):
                continue
            require(git(path, 'rev-parse', 'HEAD') == entry['head'], f'패치 적용 전 SHA가 다릅니다: {relative}')
            clean_project(path)
            patch_path = patch_dir / patch['file']
            write_json(self.state_path, state)
            self.lock['official_patch_status'] = 'applying'
            self.update_lock()
            try:
                self.logged(['git', '-C', path, 'am', patch_path], 'redroid-patches.log')
                clean_project(path)
                head = git(path, 'rev-parse', 'HEAD')
                require(git(path, 'rev-parse', 'HEAD^') == entry['head'], f'예상과 다른 패치 커밋 이력입니다: {relative}')
                actual_id = self.patch_id(path, head)
                expected_id = self.file_patch_id(patch_path)
                require(actual_id == expected_id, f'적용된 diff가 공식 패치와 다릅니다: {patch["file"]}')
                entry['head'] = head
                entry['applied'].append(dict(file=patch['file'], sha256=patch['sha256'], commit=head, patch_id=actual_id))
                write_json(self.state_path, state)
            except BaseException:
                self.lock['official_patch_status'] = 'failed'
                self.update_lock()
                raise
        self.verify_snapshot(state)
        self.lock['official_patch_status'] = 'complete'
        self.update_lock()
        write_json(self.log_dir / 'official-patches.json', state)
        print('모든 공식 패치의 적용 결과와 전체 프로젝트 SHA를 검증했습니다.')

    def verify_patch_state(self, state):
        inventory = self.lock['redroid_patches']['patches']
        bases = self.snapshot()
        for relative, entry in state['projects'].items():
            require(relative in bases and entry['base'] == bases[relative], f'패치 기준 SHA가 다릅니다: {relative}')
            patches = [item for item in inventory if item['project'] == relative]
            records = entry['applied']
            require(len(records) <= len(patches), f'패치 적용 기록 수가 잘못되었습니다: {relative}')
            previous = entry['base']
            for record, expected in zip(records, patches):
                require(record['file'] == expected['file'] and record['sha256'] == expected['sha256'],
                        f'패치 적용 기록의 파일 또는 해시가 다릅니다: {relative}')
                path = safe_project(self.source, relative)
                require(git(path, 'rev-parse', f'{record["commit"]}^') == previous,
                        f'패치 적용 기록의 커밋 이력이 다릅니다: {relative}')
                require(self.patch_id(path, record['commit']) == record['patch_id'],
                        f'패치 적용 기록의 diff가 다릅니다: {relative}')
                previous = record['commit']
            require(entry['head'] == previous, f'패치 적용 기록의 소스 SHA가 다릅니다: {relative}')

    @staticmethod
    def patch_id(path, commit):
        data = subprocess.check_output(['git', '-C', str(path), 'show', '--format=', commit])
        return Phase0.patch_id_bytes(data)

    @staticmethod
    def file_patch_id(path):
        return Phase0.patch_id_bytes(path.read_bytes())

    @staticmethod
    def patch_id_bytes(data):
        result = subprocess.run(['git', 'patch-id', '--stable'], input=data, stdout=subprocess.PIPE, check=True)
        fields = result.stdout.decode().split()
        require(len(fields) == 2, '단일 커밋 diff의 patch-id가 필요합니다.')
        return fields[0]


def main():
    parser = argparse.ArgumentParser(description='Phase 0 사전 점검·소스 동기화·공식 패치 적용')
    parser.add_argument('stage', choices=('preflight', 'sync', 'apply'))
    parser.add_argument('--dry-run', action='store_true', help='변경 없이 조건과 예정 작업을 검사합니다.')
    parser.add_argument('--force', action='store_true', help='동일 SHA의 소스 다운로드를 다시 실행합니다. 변경 파일은 보호합니다.')
    parser.add_argument('--init-only', action='store_true', help='sync에서 manifest만 초기화합니다.')
    args = parser.parse_args()
    try:
        require(not args.init_only or args.stage == 'sync', '--init-only는 sync에서만 사용할 수 있습니다.')
        phase = Phase0(args)
        if args.dry_run or args.stage == 'preflight':
            getattr(phase, args.stage)()
        else:
            with phase.exclusive():
                getattr(phase, args.stage)()
    except (PhaseError, OSError, ValueError, ET.ParseError) as error:
        print(f'실패: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
