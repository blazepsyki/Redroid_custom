# Phase 0 공식 패치 적용

`scripts/02-apply-redroid.sh`는 고정된 공식 패치 파일을 직접 `git am`으로 적용합니다. 공식 `apply-patch.sh`의 오류 처리 방식에 의존하지 않습니다.

## 사전 조건

- 전체 소스 동기화 완료: `config/source-lock.json`의 `sync.status`가 `complete`
- 전체 프로젝트 SHA manifest와 그 SHA-256 일치
- 각 프로젝트에 수정·미추적 파일 또는 진행 중인 Git 작업 없음
- 공식 패치 저장소 commit, 패치 파일 목록과 SHA-256 일치
- 사용자의 기존 Git committer 정보 사용 가능

패치의 원래 author와 커밋 메시지는 보존합니다. 이 단계에서는 공식 패치 내용을 수정하지 않으므로 명령 실행자에 대한 Codex trailer를 추가하지 않습니다. 이후 Codex가 직접 수정한 내용을 커밋할 때는 사용자께서 지정한 모델 확인 및 기여자 표기 규칙을 따릅니다.

## 검증과 재실행

패치 하나마다 다음을 확인합니다.

1. `git am` 종료 코드가 0인지 확인합니다.
2. `rebase-apply`, `rebase-merge`, merge 또는 cherry-pick 상태가 남지 않았는지 확인합니다.
3. 생성된 커밋의 부모가 적용 전 HEAD인지 확인합니다.
4. 생성된 커밋과 원본 패치의 `git patch-id --stable`이 같은지 확인합니다.
5. 파일명·SHA-256·생성된 커밋·patch-id를 `.repo/custom-redroid/official-patches.json`에 기록합니다.

재실행 시에는 기록된 기준 SHA, 적용 순서, 커밋 이력과 diff를 다시 검증하고 이미 적용된 항목을 건너뜁니다. 모든 패치가 성공한 뒤에도 전체 프로젝트 HEAD와 작업 트리를 확인합니다. 최종 적용 기록은 `build-logs/official-patches.json`에도 저장합니다.

## 충돌이 발생한 경우

스크립트는 즉시 중단하고 `official_patch_status`를 `failed`로 기록합니다. 작업 트리와 `git am` 상태를 남기며 자동으로 abort/reset하지 않습니다. `build-logs/redroid-patches.log`에서 실패한 프로젝트와 패치 파일을 확인하고 다음으로 상태를 조사합니다.

```bash
source scripts/common.sh
patch_project_dir="$REDROID_SOURCE_DIR/external/minijail"
git -C "$patch_project_dir" status
git -C "$patch_project_dir" diff
git -C "$patch_project_dir" am --show-current-patch
```

위 예시의 `external/minijail`은 로그에 나온 실제 실패 프로젝트 경로로 바꿔야 합니다.

원본과 다른 diff가 필요한 충돌 해결은 새로운 포팅 변경에 해당합니다. 해당 변경을 별도 패치와 조사 보고서로 관리한 뒤 입력 패치와 잠금 정보를 갱신해야 합니다. `git am --continue`만 실행하고 기존 journal을 무시하면 안 됩니다. 수동으로 만든 커밋의 HEAD가 journal과 다르면 스크립트는 재실행을 거부합니다.

공식 패치 적용을 취소하려는 경우에도 삭제·reset 전에 대상 프로젝트, 현재 HEAD와 journal의 기준 SHA를 먼저 검증해야 합니다. `--force`는 이 검증을 생략하지 않습니다.
