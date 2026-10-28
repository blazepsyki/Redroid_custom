# Custom redroid Android 15 — 소스 통합 빌드 프로젝트 명세서

> **용도:** 로컬 Codex 또는 다른 코딩 에이전트에 전달할 실행 가능한 개발·검증 작업 명세서
>
> **문서 버전:** 1.0 / 2026-10-27
>
> **상태:** 구현 계획 및 검증 기준. *새로운 소스 빌드가 성공했거나 실제 기기에서 검증됐다는 의미는 아님.*
>
> **최종 목표:** AOSP 15 + redroid를 소스에서 빌드하면서 **Intel GPU/Gralloc 안정화**, **x86_64에서 ARM64 앱 실행**, **공식 microG 호환 Signature Spoofing**, **선택적 microG·Google Play 스토어 기본 탑재**를 하나의 재현 가능한 Android 컨테이너 이미지에 통합한다.

---

## 0. Codex에 주는 작업 지시

이 문서를 로컬 개발 저장소의 `SPEC.md`로 사용한다. Codex는 다음 원칙을 따른다.

1. 작업을 **소스 조사 → 원본 redroid 빌드 → GPU/Gralloc → ARM64 Translation → microG Framework → microG 통합 → Docker 패키징 → 검증**의 게이트로 구분한다. 앞 단계가 성공하지 않으면 뒤 단계의 성공을 주장하지 않는다.
2. 모든 소스와 바이너리의 버전·Git SHA·SHA-256·패치 적용 기록을 보존한다. 브랜치/명령/경로는 실제 체크아웃에서 확인하고, 문서 예시는 무비판적으로 복사하지 않는다.
3. 공식 AOSP 및 redroid 프로젝트를 기준으로 하되 원본 Git 저장소는 불필요하게 fork하지 않는다. 개인 저장소는 **패치, manifest, 빌드 설정, 테스트 코드, 문서**를 관리한다.
4. 작업 결과는 스크립트로 재현 가능하게 만든다. 패키징 완료 전에 운영 중인 호스트에 변경을 가하지 않는다.
5. **Google 독점 ARM 번역 라이브러리/서드파티 APK/토큰/계정 데이터는 Git에 커밋하거나 공개 Docker 이미지에 배포하지 않는다.** 사용자가 직접 확보한 파일을 로컬 빌드에서만 참조한다. 사용·재배포 조건을 검토한다.
6. Signature Spoofing은 **공식 서명으로 검증한 microG 패키지에만** 적용한다. 패키지 이름만 같은 비공식 서명 APK, 일반 앱, 전체 시스템에 대한 범용 서명 우회는 구현하지 않는다. Play Integrity나 하드웨어 인증 우회는 범위 밖이다.
7. 구현 단계별로 `docs/reports/<phase>.md`에 완료 여부·검증 증거·남은 문제를 남긴다. 검증 실패는 성공으로 표기하지 않는다.
8. 큰 빌드, 기존 컨테이너/볼륨 삭제 또는 OS 설정 변경은 명시적으로 분리한다. 빌드 머신의 데이터도 삭제 전 목록과 대상을 검증한다.

### 수용 기준(최종 Definition of Done)

- [ ] AOSP `android-15.0.0_r36` + 대응 redroid 소스로 `redroid_x86_64_only-userdebug` 이미지를 **처음부터** 재현 가능하게 생성한다.
- [ ] 새 이미지 부팅, Binder, GPU 장치 접근, `SurfaceFlinger`의 `Mesa Intel HD Graphics 4600` 렌더러를 확인한다.
- [ ] Gralloc이 HDR/고비트 픽셀 포맷을 안전하게 처리하며, 기존 `SIGFPE/FPE_INTDIV` 회귀 테스트를 통과한다. **해당 단일 크래시가 해결됐다고 판정하기 위해 실제 동일 트리거 시험을 포함한다.**
- [ ] `libndk_translation`의 라이브러리와 종속성이 포함된 **개인용** 이미지에서 ARM64-only Firefox를 설치하고 Gecko 네이티브 코드 및 웹 페이지 렌더링을 확인한다.
- [ ] microG의 Self-Check에서 시스템 서명 위장·Services 서명과 `com.android.vending` 서명 항목이 통과한다(Companion 대신 Play 스토어를 탑재한 이미지에서도 확인). 공식 microG 서명이 아닌 앱의 부정 테스트도 통과한다.
- [ ] microG·Play 스토어는 기본 탑재한다(`WITH_MICROG=true`). 미탑재 빌드는 산출물로 유지하지 않는다(사용자 결정, 2026-10-28).
- [ ] 메모리 8GiB의 별도 Docker 컨테이너에서 24~72시간 운영 검증 및 반복 재부팅 검증을 진행한다. 리소스 및 Gralloc 크래시 기록을 남긴다.
- [ ] 최종 산출물: 빌드 스크립트, 개인용 Docker 이미지/OCI 아카이브, 패치 파일, source manifest lock, SBOM/라이선스 목록, SHA-256, 검증 보고서, 복구 지침.

---

## 1. 기준 소프트웨어 구성과 회귀 조건

| 항목 | 기준값 및 근거 |
|---|---|
| Android fingerprint | `redroid/redroid_x86_64_only/redroid_x86_64_only:15/BP1A.250505.005.D1/eng.root:userdebug/test-keys` |
| Build ID | `BP1A.250505.005.D1` |
| AOSP 리비전 | `android-15.0.0_r36` (빌드 번호 대응, 소스 작업에서 재확인) |
| Android ABI | `x86_64,arm64-v8a`; 기존 이미지는 `64only` |
| Native Bridge | `libnb.so -> libndk_translation.so` / `libberberis_exec_region.so` |
| ARM64 실증 | Firefox 158.0 ARM64-only 설치, `primaryCpuAbi=arm64-v8a`, Berberis 초기화, Gecko 네이티브 라이브러리 로드 및 실제 페이지 렌더링 성공 |
| GPU 실증 | Intel HD Graphics 4600 (Haswell GT2), Mesa 24.0.8, OpenGL ES 3.2, `host` 모드 |
| 발견된 결함 | Gralloc `gralloc_gbm_bo_create+621`에서 부팅 중 `SIGFPE/FPE_INTDIV` 1건, 서비스 재시작 후 실행은 정상 |
| microG | Services/Companion 설치 성공, Self-Check의 Signature Spoofing/서명 검사 실패 |
| 런타임 요구 조건 | Binder/Binderfs를 지원하는 Linux 커널, Docker, GPU host 모드에서 DRM render node 접근 |

**구분:** 위 내용은 이전 Docker 바이너리 덧씌우기 실험 결과다. 신규 AOSP **소스 통합 이미지의 정상 동작을 증명하지는 않는다.**

### 목표 아키텍처

```text
AOSP android-15.0.0_r36  +  redroid local manifests / patches
                            |
             개인 소스 패치 계층
             |-- gbm_gralloc 최신 픽셀 포맷 처리/0나눗셈 방지
             |-- microG 공식 서명 한정 PackageManager 패치
             |-- Native Bridge property + ABI + init/linker 구성
             |-- optional microG system/prebuilt modules
                            |
              redroid_x86_64_only-userdebug 빌드
                            |
              Android rootfs -> private OCI/Docker image
                            |
       Docker / Intel i915 GPU / Binder / 8GiB / ADB localhost
                            |
      ARM64 Firefox + microG Self-Check + FCM + 장시간 안정성
```

---

## 2. 개발 머신 요구 사항 및 빌드 준비

Android 공식 AOSP 요구사항은 **64비트 x86, 최소 64GiB RAM, 400GB 이상 여유 공간**이다. 원활한 컴파일은 더 많은 CPU/SSD가 유리하다. 32GiB에서도 동시 작업 수를 줄여 시도할 수 있지만 성공·소요 자원은 보장되지 않는다.

- **우선순위:** 별도 Ubuntu Linux 머신 또는 충분한 메모리·SSD를 확보한 Linux 빌드 환경.
- Windows 11 개발 PC를 사용할 경우 WSL2 Ubuntu를 검토할 수 있지만 **AOSP 소스와 출력물은 WSL2 ext4 파일시스템 안에 저장**한다. `/mnt/c` NTFS 경로에서 빌드하지 않는다. WSL2의 RAM, swap, 가상 디스크 용량을 명시적으로 확인한다.
- 빌드 머신과 실행 검증 호스트의 역할을 구분하고, 빌드 자원은 별도로 확보한다.
- Docker Builder를 사용하되 redroid 문서의 `android-builder-docker/Dockerfile`은 **Ubuntu 20.04, Python 2 등 구형 구성**이므로 Android 15용으로 빌드 의존성 재검증 후 새 Dockerfile을 작성한다.

### 2.1 사전 점검 예시

```bash
uname -m
free -h
df -h "$HOME"
nproc
git --version
docker version || true
command -v repo || true
```

필수 빌드 도구 버전은 AOSP 소스의 실제 의존성에 맞춰 정한다. 사용자 이름·이메일은 임의로 고정하지 않는다.

### 2.2 저장소 구조(산출해야 할 리포지토리)

```text
custom-redroid/
├── SPEC.md
├── README.md
├── Makefile
├── config/
│   ├── build.env.example
│   ├── source-lock.json
│   └── features.json
├── manifests/
│   └── custom.xml                # 필요한 추가 소스만 선언
├── patches/
│   ├── redroid/                  # upstream 패치 재배포보다는 commit 기록
│   ├── gralloc/
│   ├── frameworks-base/
│   └── device-redroid/
├── vendor/custom/                # Android.bp, product mk, init rc 등
├── scripts/
│   ├── 00-preflight.sh
│   ├── 01-sync.sh
│   ├── 02-apply-redroid.sh
│   ├── 03-apply-custom.sh
│   ├── nativebridge_import.py   # 04단계: API35 번역기 추출·검증
│   ├── microg_import.py         # 05단계: microG·Play 스토어 APK import
│   ├── 06-build.sh
│   ├── 07-package.sh
│   ├── 08-deploy-test.sh
│   ├── 09-verify.sh
│   └── 10-collect-report.sh
├── tests/
│   ├── framework-signature/
│   ├── gralloc-format/
│   ├── arm64-app/
│   └── smoke/
├── docker/
│   ├── Dockerfile.builder
│   └── compose.test.yml
├── docs/
│   ├── architecture.md
│   ├── patching.md
│   ├── licenses.md
│   └── reports/
├── local-prebuilts/              # .gitignore, 독점/외부 바이너리
├── build-logs/                   # .gitignore
└── dist/                         # .gitignore 또는 release artifact 관리
```

구현 전 `source-lock.json`으로 AOSP manifest/각 프로젝트 SHA, redroid manifest SHA, redroid patch SHA, gbm_gralloc SHA, microG APK SHA를 기록한다.

---

## 3. 소스 동기화와 redroid 순정 재현(Phase 0)

**목표:** 개인 패치를 전혀 적용하지 않은 redroid 15를 소스에서 빌드·패키징·부팅시켜 빌드 환경과 공식 패치의 정합성을 확인한다.

### 3.1 정확한 소스 버전 선택

```bash
mkdir -p "$HOME/work/redroid-aosp15"
cd "$HOME/work/redroid-aosp15"

repo init \
  -u https://android.googlesource.com/platform/manifest \
  -b android-15.0.0_r36 \
  --depth=1 --git-lfs

# 반드시 리모트에 실제 브랜치가 있는지 먼저 확인
git ls-remote --heads \
  https://github.com/remote-android/local_manifests.git
```

브랜치 `15.0.0`이 실제 있음을 확인한 경우에만:

```bash
git clone --branch 15.0.0 \
  https://github.com/remote-android/local_manifests.git \
  .repo/local_manifests

repo sync -c -j4
```

**주의:** 현재 GitHub 웹 표시의 기본 브랜치는 `8.1.0`이다. 브랜치 명칭을 추측하지 않는다. 원하는 브랜치가 없으면 redroid 문서/패치 내 `device/redroid` 및 manifest 프로젝트를 확인해 Android 15용 `local_manifests`를 정확히 구성한다. 이 단계는 Codex의 **조사·기록 게이트**다.

### 3.2 공식 redroid 패치

```bash
cd "$HOME/work"
git clone https://github.com/remote-android/redroid-patches.git

test -d redroid-patches/android-15.0.0_r36
./redroid-patches/apply-patch.sh \
  "$HOME/work/redroid-aosp15" android-15.0.0_r36 \
  2>&1 | tee "$HOME/work/redroid-patch.log"

# 스크립트는 개별 패치 실패 시에도 성공 코드로 끝날 수 있으므로 반드시 내용 확인
if grep -Fq '*****[ERROR]*****' "$HOME/work/redroid-patch.log"; then
  echo 'redroid patch failed; STOP' >&2
  exit 1
fi
```

Codex는 위 단순 grep 예시 대신 `git am` 상태와 각 프로젝트의 적용 커밋/충돌까지 검사하는 안정적인 검증 코드를 구현해야 한다. 원본 저장소와 개인 패치 적용 후 변경 내역을 서로 구분한다.

### 3.3 빌드 타깃 탐색 및 빌드

```bash
cd "$HOME/work/redroid-aosp15"
source build/envsetup.sh

# 실제 존재 여부 확인 후 선택
lunch redroid_x86_64_only-userdebug

# 별도 빌드 머신의 리소스에 따라 병렬도 조절
m -j4
```

AOSP의 일부 브랜치에서는 `lunch` 인터페이스 표기가 변경될 수 있다. 실패 시 실제 등록된 product/target의 목록을 확인하고 `lunch` 인자를 수정한다. 예시 명령이 모든 체크아웃에서 유효하다고 가정하지 않는다.

#### Phase 0 완료 조건

- [ ] `out/target/product/<actual-product>` 생성 및 Android 이미지 산출물 확인.
- [ ] 원본 redroid rootfs/OCI 컨테이너 패키징 및 Android `sys.boot_completed=1` 확인.
- [ ] `ro.build.fingerprint`, `ro.build.id`, SELinux/파일 권한, `pidof system_server` 확인.
- [ ] 모든 패치 및 프로젝트 SHA 고정.
- [ ] 기준 이미지 태그 예: `redroid-local:a15-r36-vanilla-<shortsha>` 생성.

---

## 4. Gralloc / Intel GPU 통합(Phase 1)

### 4.1 이미 관측한 장애

기존 GPU host 렌더링에서 한 차례 다음 충돌이 발생했다.

```text
SIGFPE (FPE_INTDIV)
/vendor/lib64/hw/gralloc.gbm.so
gralloc_gbm_bo_create+621
```

이는 redroid Issue #930의 `gralloc_gbm_bo_create+621`와 일치한다. 원인은 특정 HAL pixel format에서 `bpp=0`이 되어 0으로 나누는 경로다. 다만 제보 환경은 AMD GPU이고 현재 호스트는 Intel Haswell이므로 **동일한 요청 포맷 때문에 발생했는지는 재현 후 판단**한다.

참고 업스트림 패치: [`robherring/gbm_gralloc` a8874a1 — Add support for newer pixel formats](https://github.com/robherring/gbm_gralloc/commit/a8874a18ad7884ea713f45047efad080fe973712)

이 커밋은 `RGBA_FP16`, `RGBA_1010102`의 GBM 포맷·BPP 매핑을 추가한다.

### 4.2 Codex의 구현 절차

1. `repo manifest -r`와 Android 소스 트리에서 실제 `gralloc.gbm.so`를 생산하는 프로젝트/commit/Android.bp 또는 Makefile을 식별한다.
2. 기존 코드에 `a8874a1`에 상응하는 처리가 이미 포함됐는지 확인한다. 있으면 **중복 패치를 적용하지 않는다.**
3. 해당 변경이 없고 현재 HAL API와 호환된다면 패치를 cherry-pick 또는 수동 포팅한다.
4. `gralloc_gbm_get_bpp()`에서 알 수 없는 포맷의 BPP=0이 division으로 넘어가지 않도록 **명시적 오류 처리**를 추가한다. 알 수 없는 모든 포맷을 4BPP로 간주하는 바이너리 우회는 적용하지 않는다.
5. 호스트 Mesa 드라이버(`i915`), Android EGL Mesa/GBM, Render Node `/dev/dri/renderD128`, HAL의 런타임 속성을 바꾸지 않고 최대한 기존 성공 구성을 유지한다.
6. 테스트에서는 4BPP(`RGBA_1010102`), 8BPP(`RGBA_FP16`), 지원되지 않는 포맷, 일반 RGBA8888을 각각 검증한다. 실제 GBM 하드웨어가 포맷을 지원하지 않으면 적절히 실패해야 하며 크래시가 발생해서는 안 된다.

### 4.3 GPU 기능 검증

```bash
adb -s 127.0.0.1:5558 shell getprop sys.boot_completed
adb -s 127.0.0.1:5558 shell getprop ro.hardware.egl
adb -s 127.0.0.1:5558 shell getprop ro.hardware.gralloc
adb -s 127.0.0.1:5558 shell dumpsys SurfaceFlinger | grep '^GLES:'
sudo intel_gpu_top
```

기대값은 Intel HD Graphics 4600/Haswell Mesa 하드웨어 렌더러. `llvmpipe`/SwiftShader는 `host` 모드의 성공 판정에 사용할 수 없다.

추가로 `logcat -b crash`에서 Gralloc `SIGFPE`가 없음을 반복 부팅과 그래픽 포맷 테스트 후 확인한다. 로그가 한 번 비어 있었다는 사실만으로 버그 해결을 확정하지 않는다.

---

## 5. ARM64 Translation 통합(Phase 2)

### 5.1 설계

- 대상은 **x86_64 64only Android에서 ARM64 전용 앱 실행**이다. 32비트 ARM 실행과 일반 ELF를 `binfmt_misc`로 직접 실행하는 기능은 별도 선택 과제.
- 기존 성공 구성을 기준으로 Google Android **API 35** 에뮬레이터에서 추출한 `libndk_translation.so`, `libberberis_exec_region.so`, 종속 라이브러리 및 필요한 init rc를 사용한다.
- `ro.dalvik.vm.native.bridge=libnb.so`, `ro.dalvik.vm.isa.arm64=x86_64`, 실제 ABI 목록과 linker namespace 설정을 일치시킨다. 세부 속성의 설정 위치와 init 순서는 대상 소스에서 확인한다.
- 실제 번역 라이브러리가 포함되지 않았으면 `arm64-v8a`를 ABI 목록에 광고하지 않는다. 컴파일이나 패키징 단계에서 **누락 시 실패**하도록 한다.

### 5.2 Prebuilt 모듈

`vendor/custom/nativebridge/` 또는 해당 타깃이 허용하는 전용 디렉터리에 다음을 만든다.

- `Android.bp`: host/build machine용이 아닌 Android target용 **prebuilt shared_library**, 종속성 및 설치 파티션 설정.
- `device.mk`/product 설정: 바이너리 및 property/ABI 구성.
- 필요시 `init/ndk_translation.rc` 통합.
- Google SDK에서 추출한 파일의 **버전별 SHA-256 manifest**.
- `libnb.so`의 심볼릭 링크 경로 및 동적 링커 로드 확인.

Android Soong의 `prebuilt_etc` 및 `cc_prebuilt_library_shared` 사용 여부는 실제 파일 배치/링커 의존성에 맞춰 확인한다. 이 문서에서 임의로 ELF 종속성이나 `/system/lib64/` 고정 경로를 확정하지 않는다.

### 5.3 재현 가능한 추출 단계

기존 로컬 추출 자동화 `redroid-arm64-automation-v6`의 성공 절차를 참고한다. 이 방식은 API 35 SDK 이미지가 **GPT → Android super → LP(system/system_ext/product/vendor) → ext4** 구조였기 때문에 단순 `mount system.img`가 아닌 단계별 추출을 수행한다.

1. Google 공식 Android SDK `system-images;android-35;google_apis;x86_64` 확보.
2. 원본 이미지 해시와 패키지 버전 기록.
3. GPT/LP 메타데이터를 검사하고 읽기 전용으로 논리 파티션 추출.
4. 필요한 라이브러리/설정 파일을 모두 추출하고 누락 시 중단.
5. 로컬 빌드 전용 디렉터리에 넣고 외부 Git 저장소에 커밋하지 않는다.
6. 빌드 산출물에서 ARM64 APK 설치·네이티브 함수 호출 성공으로 검증한다.

**재현된 실제 검사 로그의 예:**

```text
ro.product.cpu.abilist = x86_64,arm64-v8a
ro.dalvik.vm.native.bridge = libnb.so
libnb.so -> libndk_translation.so
I berberis: Initialized Berberis (aarch64)
Load .../lib/arm64/libmozglue.so ...: ok
```

최종 합격은 ARM64-only Firefox 158.0으로 웹 페이지를 로드했을 때다. ABI 광고, APK 설치, 라이브러리 파일 존재만으로 합격시킬 수 없다.

---

## 6. microG 전용 Signature Spoofing(Phase 3)

### 6.1 참조 구현 및 보안 모델

Android 15 / LineageOS 22.1의 [`ComputerEngine.java`](https://github.com/LineageOS/android_frameworks_base/blob/lineage-22.1/services/core/java/com/android/server/pm/ComputerEngine.java)를 참조한다. microG 공식 문서에 따르면 2024년 이후 서명 검사 API 변경 때문에 **구형 서명 위장 패치로는 최신 앱에서 불완전할 수 있다.**

구현 요구사항:

1. 대상 패키지는 정확히 `com.google.android.gms`, `com.android.vending`로 제한한다.
2. 앱 이름만 비교하지 않는다. **설치된 APK의 실제 signing certificate가 공식 microG 인증서와 일치하는지 검증**한다.
3. 정상 확인된 패키지의 Signature/SigningInfo 조회에서만 해당 Google 호환 서명 응답을 제공한다.
4. `GET_SIGNATURES`(legacy), `GET_SIGNING_CERTIFICATES`, `PackageInfo.signatures`, `PackageInfo.signingInfo`, 다른 관련 서명 비교 API의 영향을 코드와 테스트로 확인한다.
5. 다른 일반 APK의 서명은 변경 없이 반환해야 한다. 위장된 서명으로 실제 APK 검증·설치 정책을 통째로 우회하지 않는다.
6. 로깅에 개인 Google 계정, 토큰, 인증 관련 정보가 남지 않게 한다.
7. 인증서를 재서명한 비공식 microG 패키지, 비허용 패키지, 변조된 `fake-signature` 메타데이터는 모두 실패해야 한다.
8. 최신 microG의 **자체 Self-Check**를 정식 검증 도구로 사용하며 구형 외부 Signature Spoofing Checker만으로 판정하지 않는다.

### 6.2 대상 소스와 이식 방식

```text
frameworks/base/services/core/java/
  com/android/server/pm/ComputerEngine.java
```

- LineageOS `lineage-22.1`의 microG 부분만 diff로 뽑는다. **LineageOS의 전체 ComputerEngine.java를 AOSP 파일 위에 덮어쓰지 않는다.**
- 참조 구현 내 `MICROG_REAL_SIGNATURE`, `MICROG_FAKE_SIGNATURE`, 조건 검사, 패키지 정보 생성/수정 경로를 현재 AOSP r36 API에 맞춰 이식한다.
- LineageOS 로직에 추가 JNI/네이티브 서비스 또는 다른 프레임워크 변경 사항이 있으면 **전체 redroid 소스 빌드에서 함께 수정할 수 있다.** `services.jar` 하나로 제한하지 않는다.
- System Server/PackageManager 부팅 경로가 변경되므로 최소한 단위 테스트, AOSP 부팅, 패키지 조회 계측 테스트 후 microG Self-Check를 수행한다.

**정책:** 실패한 microG 기능을 통과시키기 위해 루트·앱 전체에 일반적인 `FAKE_PACKAGE_SIGNATURE` 권한을 열어주거나 Google Play Integrity 무결성 응답을 위조하지 않는다.

### 6.3 정적/동적 테스트

| 테스트 | 기대 결과 |
|---|---|
| 공식 microG Services 서명 조회 | 정책상 허용된 Google 호환 서명 반환 |
| 공식 microG Companion 서명 조회 | 정책상 허용된 Google 호환 서명 반환 |
| 동일 패키지명·다른 인증서 | 서명 위장 거부 |
| 다른 앱 이름에 spoofing 메타데이터 삽입 | 서명 위장 거부 |
| `signatures`와 `signingInfo` | 규칙에 따른 일관된 결과 |
| PackageManager 설치 및 업데이트 | 정상적인 원본 APK 서명 검증 유지 |
| microG Self-Check | 시스템/Services/Companion 서명 검사 통과 |
| 정상 앱의 설치 및 업데이트 | 회귀 없음 |

실제 앱이 Play Integrity나 하드웨어 Keystore attestation을 필수로 요구하면 지원하지 않을 수 있다. 이것은 Signature Spoofing 성공과 별개다.

---

## 7. microG 기본 탑재(Phase 4, 선택 기능)

**Signature Spoofing 프레임워크 패치를 먼저 통과시킨다.** microG 기본 탑재는 `WITH_MICROG` 옵션으로 구성하며, 최종 산출물은 `WITH_MICROG=true` 하나만 유지한다. 옵션 자체는 빌드 단계 분리와 문제 분석용으로 남긴다.

- microG Services(`com.google.android.gms`)의 공식 릴리스 APK와 Google Play 스토어(`com.android.vending`) APK를 버전과 SHA-256으로 고정.
- APK 서명은 Android SDK `apksigner verify --print-certs`와 `apksigner lineage`로 검사. microG Services는 공식 microG 인증서, Play 스토어는 서명 계보가 Google 원래 인증서(`f0fd6c…`, Signature Spoofing의 호환 인증서와 같음)에서 시작해 현재 서명으로 끝나야 한다. 불일치하면 패키징을 중단.
- `android_app_import` 등의 prebuilt 모듈 설정과 product package 등록.
- 필요 권한은 Android 15 privapp permission allowlist와 실제 microG 문서를 확인해 **최소한만** 부여한다. `privileged=true`/시스템 앱 배치만으로 모든 권한이 자동 부여된다고 가정하지 않는다.
- `GsfProxy`는 대상 앱에 구식 Google C2DM 의존성이 있을 때만 추가한다.
- Google 계정 로그인, UnifiedNlp 위치 기능, FCM을 사용하는 앱 등 실제 필요 기능을 별도 테스트한다.
- `com.android.vending` 자리에는 microG Companion(FakeStore) 대신 **Google Play 스토어**를 priv-app으로 탑재한다(사용자 결정, 2026-10-28). 두 앱은 패키지 이름이 같고 서명이 달라 함께 둘 수 없으며, 시스템에 FakeStore가 있으면 Play 스토어를 설치할 수 없다. 그 밖의 Google 공식 GApps(GmsCore, GSF 등)는 microG와 충돌하므로 섞지 않는다.
- Play 스토어의 privileged 권한도 allowlist로 최소한만 부여하고 나머지는 명시적으로 거부한다. 특히 `PACKAGE_VERIFICATION_AGENT`는 부여하지 않는다. Android 15에서 동작하지 않아 크래시를 일으키는 컴포넌트는 권한 확대 대신 sysconfig `component-override`로 비활성화한다.
- Signature Spoofing 정책은 Play 스토어 때문에 바꾸지 않는다. 실제 Google 서명 Play 스토어는 위장 대상이 아니다.
- microG 기기 등록(checkin)은 Google에 기기 정보를 보내므로 이미지 기본값으로 켜지 않는다. Play 스토어 사용 전 사용자가 켜는 절차를 문서화한다.

권장 빌드 옵션 예시(실제 구현은 Soong/product config에 맞게 조정):

```text
WITH_ARM64_TRANSLATION=true
WITH_GRALLOC_FIX=true
WITH_MICROG_SIGNATURE_SPOOFING=true
WITH_MICROG=true                  # 최종 산출물 기본값
```

최종 산출물은 Gralloc 수정·ARM64 Translation·Signature Spoofing·microG·Play 스토어를 모두 켠 통합 빌드 하나다. 순정 redroid 빌드는 Phase 0 기준 이미지로만 보존하며, 기능별 부분 빌드 매트릭스는 유지하지 않는다.

---

## 8. 전체 빌드, OCI 패키징 및 이미지 검증(Phase 5)

### 8.1 빌드 규칙

- AOSP 소스 전체 빌드는 *빌드 머신에서만* 수행한다. `m -jN`은 실제 RAM/CPU를 기준으로 제한.
- 패치 실패 시 `git am` 상태 확인 후 즉시 중단. redroid의 `apply-patch.sh`가 실패를 로그만 남기고 진행하는 경우가 있어 별도 검증 필수.
- Android 이미지 내부 `/init`을 일반 GNU/Linux용 `/bin/sh`로 가정하지 않는다. Android rootfs Dockerfile에 `RUN`이 동작한다고 가정하지 않는다.
- 최종 패키지는 **AOSP 빌드 산출물의 파일 구조/SELinux 레이블/심볼릭 링크**를 유지해야 한다. 컨테이너에서 보이는 `/system`, `/vendor`, `/product`, `/system_ext` 경로를 검증한다.

### 8.2 패키징

redroid 공식 문서에는 `system.img`, `vendor.img` 등을 읽기 전용으로 마운트해 rootfs tar로 만들고 `docker import -c 'ENTRYPOINT [...]'`로 가져오는 예제가 있다. **이 절차는 과거 Android 버전의 단순 파티션 예제**다.

현재 Android 15에서는 GPT/super/논리 파티션, `system`, `vendor`, `product`, `system_ext` 등 실제 AOSP 출력 레이아웃이 달라질 수 있으므로, **빌드된 이미지의 형식과 redroid 프로젝트의 최신 importer를 확인한 뒤 정확한 rootfs 조립 절차를 구현한다.** 단순히 `mount system.img system`이 되는 것으로 가정하지 않는다.

패키징 검증 포인트:

1. 추출/마운트는 기본 읽기 전용, `trap`으로 언마운트 및 임시 디렉터리 제거.
2. tar 생성 시 심볼릭 링크·numeric owner·xattrs 유지; 모든 파일을 777로 열지 않는다.
3. `system_server`/Gralloc/ARM64 번역 라이브러리/microG 파일이 올바른 파티션과 경로에 존재하는지 확인.
4. Docker 이미지의 진입점은 redroid `/init` 및 `androidboot.hardware=redroid` 요구사항에 맞게 설정.
5. 배포 태그에 AOSP 태그, custom patch commit, 기능 조합을 기록하며 `latest` 단독 의존을 피한다.
6. 독점 바이너리가 포함된 컨테이너의 공개 레지스트리 push는 하지 않는다.

예시 태그:

```text
redroid-local:a15-r36-vanilla-<sha>
redroid-local:a15-r36-gralloc-<sha>
redroid-local:a15-r36-arm64-<sha>
redroid-local:a15-r36-microg-<sha>
```

---

## 9. 배포 테스트(Phase 6)

### 9.1 독립된 테스트 인스턴스

기존 컨테이너와 데이터 디렉터리를 공유하지 않는다. 신규 컨테이너의 ADB 포트는 호스트에서 비어 있는 localhost 포트를 사용한다. `host GPU` 모드에서 Intel Render Node를 사용하며 8GiB RAM 상한을 적용한다.

다음은 **실제 이미지가 준비된 이후** 테스트할 Compose 골격이다. `IMAGE_TAG`와 `DATA_DIR`은 명시적으로 정의하고 디렉터리를 사전 생성해야 한다.

```yaml
services:
  custom-redroid:
    image: ${IMAGE_TAG}
    container_name: redroid-custom-test
    privileged: true
    restart: 'no'         # 초기 테스트는 자동 재시작 금지
    cpus: 1.5
    mem_limit: 8g
    ports:
      - '127.0.0.1:5558:5555'
    volumes:
      - /dev/dri:/dev/dri
      - ${DATA_DIR}:/data
    command:
      - androidboot.redroid_width=720
      - androidboot.redroid_height=1280
      - androidboot.redroid_dpi=240
      - androidboot.redroid_fps=30
      - androidboot.redroid_gpu_mode=host
      - androidboot.redroid_gpu_node=/dev/dri/renderD128
      - androidboot.use_memfd=1
    labels:
      - com.centurylinklabs.watchtower.enable=false
    logging:
      driver: json-file
      options:
        max-size: '10m'
        max-file: '3'
```

- **메모리 8GiB는 상한이지 초기 예약이 아니다.** 호스트 Swap과 기존 서비스 부하를 고려해 `memory.events`, `memory.peak`, `docker stats`로 관찰한다.
- ADB를 외부 네트워크에 공개하지 않는다. Windows scrcpy는 SSH 터널 사용.
- 시험 이미지가 privileged 컨테이너이므로 신뢰할 수 없는 APK를 설치하지 말 것.
- 정식 운영 전 `restart: unless-stopped`와 영속성, 백업/복원 정책을 검토한다.

### 9.2 스모크 테스트 명령

```bash
adb connect 127.0.0.1:5558
adb -s 127.0.0.1:5558 shell getprop sys.boot_completed
adb -s 127.0.0.1:5558 shell getprop ro.build.fingerprint
adb -s 127.0.0.1:5558 shell getprop ro.dalvik.vm.native.bridge
adb -s 127.0.0.1:5558 shell getprop ro.product.cpu.abilist
adb -s 127.0.0.1:5558 shell dumpsys SurfaceFlinger | grep '^GLES:'
adb -s 127.0.0.1:5558 shell pidof system_server
adb -s 127.0.0.1:5558 logcat -d -b crash | tail -n 100
docker stats --no-stream redroid-custom-test
```

ARM64 Firefox APK는 이전에 검증한 공식 `fenix-158.0.multi.android-arm64-v8a.apk`를 사용하고, 패키지 ID는 `org.mozilla.firefox`로 지정한다. 앱 설치/Activity 실행/프로세스 유지/Gecko 페이지 렌더링을 4개 독립 항목으로 기록한다.

### 9.3 microG 검증

Self-Check에서 서명 관련 세 항목을 반드시 통과해야 한다. 그다음 필요한 Google 계정 로그인 및 FCM 푸시 수신 시험을 진행한다. Self-Check 통과가 실제 Google API 전체 호환성을 의미하지는 않는다.

### 9.4 Gralloc 크래시와 자원 검증

```bash
adb -s 127.0.0.1:5558 logcat -d -b crash \
  | grep -iE 'gralloc|allocator|SIGFPE|FPE_INTDIV' || true

sudo journalctl -k --since '1 hour ago' --no-pager \
  | grep -iE 'i915.*(hang|reset|error|timeout)|GPU HANG' || true

docker exec redroid-custom-test sh -c '
cat /sys/fs/cgroup/memory.events
cat /sys/fs/cgroup/memory.peak
cat /sys/fs/cgroup/memory.swap.current
'
```

테스트는 최소 3회 콜드 부팅, 실제 그래픽 픽셀 포맷 부하, Firefox ARM64 반복 실행/페이지 열기, 24~72시간 방치 후 상태 확인을 포함한다. 재부팅 이후 앱 데이터·로그인 유지도 확인한다.

---

## 10. 자동화 스크립트 인터페이스 계약

각 스크립트는 `set -Eeuo pipefail`을 사용하며 파일과 디렉터리는 **실제 검사 후** 접근한다. 모든 단계는 재실행 가능해야 한다. `--help`, `--dry-run`, `--force`(명시적 확인) 옵션을 구현한다.

| 스크립트 | 수행 | 사전 조건 | 결과/실패 처리 |
|---|---|---|---|
| `00-preflight.sh` | RAM, SSD, 도구, 아키텍처 검증 | 없음 | 부족 시 즉시 중단 |
| `01-sync.sh` | AOSP + redroid 정확한 SHA sync | manifest 브랜치 검증 | `source-lock.json` 기록 |
| `02-apply-redroid.sh` | 공식 패치 | 깨끗한 source tree | 누락/충돌 시 중단 |
| `03-apply-custom.sh` | Gralloc/Framework/기기 설정 패치 | 승인된 baseline | 각 변경 diff/테스트 기록 |
| `nativebridge_import.py`(04단계) | API35 binary 추출·검증 | 공식 SDK 이미지 | 해시/종속성 검증 실패 시 중단 |
| `microg_import.py`(05단계) | microG·Play 스토어 APK import, 권한·비활성 컴포넌트 고정 | microG feature flag | 서명·계보 불일치 시 중단 |
| `06-build.sh` | 제품 빌드, `--sbom`으로 SPDX SBOM 생성 | 모든 패치 적용 및 테스트 | 로그·SHA·제품 속성 저장 |
| `07-package.sh` | OCI rootfs 빌드 | 산출물 정합성 | 컨테이너 파일/label 검사 |
| `08-deploy-test.sh` | 별도 5558 인스턴스 | 배포 이미지 존재 | 기존 5556 손대지 않음 |
| `09-verify.sh` | GPU/ARM64/microG·Play 스토어/Crash/OOM | 실행 중 테스트 컨테이너(실행 호스트의 시험 폴더에서 실행) | 실패 항목별 종료 코드 비트·JSON |
| `10-collect-report.sh` | provenance·SBOM·NOTICE·제3자 목록 | 패키지·SBOM·실행 검증 기록 | `docs/reports/final.md`, `dist/report-<package_id>/` |

**통제 메커니즘**

- 인스턴스 삭제/재생성은 `redroid-custom-test`와 지정된 테스트 데이터만 대상으로 하며, 실제 볼륨 경로가 예상과 다르면 중단.
- 재부팅·크래시 테스트는 새 `/data`를 사용. 기존 사용자 데이터 복제는 백업/권한 검증 후 별도 단계에서 시행.
- 테스트 결과는 timestamp와 이미지 SHA에 결부하여 저장.
- 빌드 성공 후 `dist/`에 최소한 `source-lock.json`, `build-report.md`, `SHA256SUMS`, OCI 아카이브, 테스트 결과를 저장.

---

## 11. 실패 유형과 대응

| 상황 | 대응 |
|---|---|
| Android 15 local manifest 브랜치 부재 | upstream 코드/이전 디바이스 트리 조사 후 정확한 로컬 manifest 작성, 추측 중단 |
| redroid patch `git am` 충돌 | 어느 리포지토리·commit인지 기록, 해당 프로젝트만 충돌 해결, 자동 실패 은폐 금지 |
| AOSP 빌드 시 OOM | 병렬 빌드 축소 또는 빌드 머신 용량 확대; 서버 운영을 방해하지 않음 |
| `system.img`가 GPT/super | LP 파티션 구조를 인식해 조립, 임의로 ext4로 마운트하지 않음 |
| Native Bridge 파일은 있으나 앱 즉시 종료 | linker namespace/종속 SO/init rc/ABI/로그 진단 후 실제 앱 확인 |
| microG Self-Check 실패 | 공식 signing cert 검사, PackageManager API, `signingInfo` 처리 검증; 허용 범위 확대 금지 |
| GMS(실제 Google 패키지)와 microG 충돌 | 이미지에서 둘 중 하나만 포함; 테스트 `/data` 새로 생성 |
| Gralloc SIGFPE 재발 | 요청 포맷·관련 코드 경로·BPP 및 GBM 지원 재조사, 임의 BPP=4 강제 금지 |
| 컨테이너 부팅 실패 | 새 컨테이너만 중지, crash/logcat/docker logs 분석; 기존 정상 이미지 보존 |
| GPU renderer가 SwiftShader | GPU node / mount / EGL / gralloc / Mesa 드라이버 확인 |
| ABI 선언과 Native Bridge 미일치 | 패키징 중단, 잘못된 ABI 광고 방지 |
| Google 바이너리 재배포 제한 | 로컬 추출과 외부 비공개 배포로 제한하거나 바이너리 제외 빌드 제공 |

---

## 12. 최종 리포트 양식

Codex는 최종적으로 다음 내용을 `docs/reports/final.md`에 기재한다.

```text
# Custom redroid Android 15 Build Report

- Base AOSP: android-15.0.0_r36 / manifest commit: ...
- redroid manifest/patch commits: ...
- Product: redroid_x86_64_only-userdebug
- Builder: OS / tools / RAM / parallel jobs
- Gralloc source commit: ... / patch: ... / tests: PASS|FAIL
- ARM64 translator source: Android SDK API 35 / hashes: ... / runtime: PASS|FAIL
- microG APK versions/cert hashes: ... / framework tests: PASS|FAIL
- Docker image ID + tag + SHA: ...
- GPU: Intel HD Graphics 4600 / Mesa renderer: PASS|FAIL
- Firefox ARM64: install / native library / page load: PASS|FAIL
- microG Self-Check: spoof / services / vending(Play Store): PASS|FAIL
- Unsupported signer negative tests: PASS|FAIL
- Gralloc crash regression: PASS|FAIL|NOT TESTED
- 24~72h stability: PASS|FAIL|NOT TESTED
- Regressions / follow-up issues: ...
- Redistribution policy / licenses: ...
- Rollback command and previous image ID: ...
```

`NOT TESTED`는 성공으로 바꾸지 않는다. 모든 결과물은 사용된 source SHA/이미지 ID와 연결한다.

---

## 13. Codex에 전달할 시작 프롬프트

아래 프롬프트를 새 로컬 Codex 세션의 첫 메시지로 사용할 수 있다.

> 현재 저장소의 `SPEC.md`를 읽고 Android 15 기반 custom redroid 프로젝트를 구현해 주세요. 목표는 AOSP `android-15.0.0_r36` + redroid의 `redroid_x86_64_only-userdebug` 소스 빌드, `gbm_gralloc` 포맷 크래시 수정, Google API 35 `libndk_translation`의 로컬 prebuilt 통합, LineageOS 22.1 참조 microG 공식 서명 전용 Signature Spoofing, 선택적 microG·Play 스토어 APK 통합, 개인용 OCI 이미지 및 검증 자동화입니다. Phase 0의 정확한 manifest/패치 버전 확인과 순정 redroid 재현부터 시작하고, 단계마다 소스 커밋·패치·검증 기록을 남기세요. 기존 운영 호스트와 컨테이너/데이터에는 접근하거나 변경하지 마세요. 독점 Google 바이너리는 저장소에 포함하거나 공개 배포하지 마세요. microG의 신뢰할 수 있는 서명 검사 범위를 확대하지 마세요. 빌드/테스트가 실제로 이뤄지지 않은 항목은 `NOT TESTED`로 표시하고, 실행 불가능한 명령은 추측하지 말고 먼저 조사하세요. 완료된 단계마다 코드, 문서, 실패 원인 및 다음 단계 명령을 정리해 주세요.

---

## 14. 확인한 참고 자료

1. redroid 공식 빌드 가이드: https://github.com/remote-android/redroid-doc/tree/master/android-builder-docker
2. redroid 상세 Docker Builder 절차: https://github.com/remote-android/redroid-doc/blob/master/android-builder-docker/README.detailed.md
3. redroid 패치 저장소: https://github.com/remote-android/redroid-patches/tree/master/android-15.0.0_r36
4. redroid local manifests: https://github.com/remote-android/local_manifests
5. redroid 공식 설정 및 Native Bridge: https://github.com/remote-android/redroid-doc
6. ARM64 Bridge 이슈 #933: https://github.com/remote-android/redroid-doc/issues/933
7. Gralloc 크래시 이슈 #930: https://github.com/remote-android/redroid-doc/issues/930
8. Gralloc 소스 수정 `a8874a1`: https://github.com/robherring/gbm_gralloc/commit/a8874a18ad7884ea713f45047efad080fe973712
9. LineageOS 22.1 `ComputerEngine.java`: https://github.com/LineageOS/android_frameworks_base/blob/lineage-22.1/services/core/java/com/android/server/pm/ComputerEngine.java
10. microG Signature Spoofing 공식 문서: https://github.com/microg/GmsCore/wiki/Signature-Spoofing
11. AOSP 하드웨어 요구사항: https://source.android.com/docs/setup/start/requirements
12. AOSP Build numbers: https://source.android.com/docs/setup/reference/build-numbers

**중요한 검증 범위:** 위 자료를 참고해 작성했으나, 실제 Android 15 AOSP 전체 빌드·새 Gralloc 실행·microG 패치 컴파일·새 Docker 이미지 테스트는 이 문서를 만드는 과정에서 수행하지 않았다. 소스 리비전과 실제 수정 파일 경로는 로컬 Codex가 다시 확인해야 한다.
