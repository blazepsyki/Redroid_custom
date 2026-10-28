# Redroid Custom - Android 15

AOSP `android-15.0.0_r36`과 공식 redroid 패치 위에 다음을 통합한 `redroid_x86_64_only-userdebug` 이미지를 소스에서 빌드합니다.

- Intel GPU용 Gralloc 수정(P010 등 미지원 format 거부)
- ARM64 앱 실행(Android SDK API 35의 `libndk_translation`)
- 공식 microG 인증서 전용 Signature Spoofing
- microG Services와 Google Play 스토어 기본 탑재
- 사용자 제공 Lawnchair, Fcitx5·Hangul 플러그인, Firefox(x86_64) APK 기본 탑재

요구사항은 [개발 명세서](docs/DEVELOPMENT.md)에 있습니다. 최신 결과는 [최종 보고서](docs/reports/final.md)에, 검증 경과와 Play 스토어 사용 절차는 [최종 검증 보고서](docs/reports/final-validation.md)에 있습니다. 24~72시간 방치 시험과 Google 계정이 필요한 시험(로그인 유지·FCM·Play 스토어 앱 설치)은 아직 수행하지 않았습니다.

## 폴더 구성

| 경로 | 내용 |
|---|---|
| `scripts/` | 단계별 실행 스크립트(`00`~`10`)와 Python 구현 |
| `patches/` | AOSP 프로젝트에 적용하는 custom 패치(device-redroid 3개, frameworks-base 1개, gralloc 2개, build-make 1개) |
| `vendor/custom/` | 제품 설정, microG 서명 정책, Gralloc format 시험 도구 |
| `config/` | 소스·패치·빌더·바이너리의 SHA 고정 기록과 기능 설정 |
| `manifests/redroid.xml` | 고정한 redroid local manifest |
| `docker/Dockerfile.builder` | AOSP 빌드 컨테이너 |
| `tests/` | 자동화 단위 테스트, microG 정책 Java 시험, ARM64 직접 실행 시험 프로그램 |
| `inputs/` (Git 제외) | 사용자가 확보한 APK. `microg/`(GmsCore, Play 스토어), `apks/`(Lawnchair, Fcitx5·Hangul 플러그인, Firefox), `test-apks/`(ARM64 Firefox) |
| `dist/` (Git 제외) | 이미지 패키지 `a15-r36-custom-<package_id>/`와 SBOM·라이선스 묶음 `report-<package_id>/` |
| `build-logs/` (Git 제외) | 빌드·패키지·SBOM 상태 파일과 실행 검증 증거 |
| `docs/` | [개발 명세서](docs/DEVELOPMENT.md), 최종 보고서, [패치 적용 안내](docs/patching.md) |

AOSP 소스와 빌드 출력은 `config/build.env`의 `REDROID_SOURCE_DIR`에 둡니다. 해당 경로는 ext4 파일시스템이어야 합니다. 이 저장소는 스크립트와 설정·문서만 관리합니다. 아래 명령은 저장소 루트의 Linux 셸에서 실행하며, Windows에서는 WSL 배포판의 셸을 사용합니다.

빌드 메타데이터는 `BUILD_USERNAME=redroid`, `BUILD_HOSTNAME=redroid-builder`, `BUILD_NUMBER=redroid.a15r36`으로 고정합니다. 호스트 계정명·호스트명과 외부 환경변수로 이 값을 대체하지 않습니다. 파일 쓰기 권한을 위한 UID/GID 매핑은 유지합니다. 제품 빌드와 패키징에서 `build.prop`의 사용자·호스트·incremental 번호·fingerprint를 검사하며, 패키징은 실제 rootfs archive도 다시 검사합니다. 이 검사를 통과하지 않은 기존 이미지에는 재빌드가 필요합니다.

증분 빌드에서 vendor 속성만 이전 번호를 유지하지 않도록 `build/make` 패치가 vendor `build.prop` 생성 규칙에 빌드 번호·fingerprint 파일 의존성을 추가합니다. 기존 `out/`에서도 식별정보 변경이 vendor 이미지까지 반영되어야 검사를 통과합니다.

## 준비

1. **WSL과 Docker:** Ubuntu 22.04/24.04/26.04(amd64, systemd)에서 Docker를 설치하고 빌더 이미지를 만듭니다.

   ```bash
   sudo bash scripts/setup-docker-wsl.sh --user "$(id -un)"
   docker build -t redroid-builder:a15-ubuntu22.04 -f docker/Dockerfile.builder docker
   ```

   기본 이미지는 digest로 고정했지만 apt 패키지는 설치 시점 저장소를 따릅니다. 새로 만든 이미지 ID가 `config/builder-lock.json`의 `builder_id`와 다르면 사전 점검이 중단됩니다. `docker image inspect`로 새 이미지 ID를 확인하고, 이미지 안의 `/opt/builder/packages.tsv`를 검토한 뒤 builder 기록과 `build-logs/environment/builder-packages.tsv`를 갱신해야 합니다. 새 builder로 AOSP 빌드를 확인하기 전에는 이전의 빌드 검증 결과를 새 이미지의 검증 결과로 사용하지 않습니다.

2. **로컬 설정:** `config/build.env.example`을 `config/build.env`로 복사해 경로와 병렬도(`REDROID_BUILD_JOBS`)를 정합니다. Git 사용자 정보가 다른 파일에 있으면 `REDROID_GIT_CONFIG`로 지정합니다. 공식 패치를 `git am`으로 적용하므로 committer 정보가 필요합니다.

   처음부터 빌드할 때는 `REDROID_SOURCE_DIR`을 WSL ext4 위의 빈 경로로 지정하고, 사전 점검 전에 해당 디렉터리를 만듭니다. 기존 소스의 `out/`이 남아 있으면 빌드는 그 출력을 재사용합니다.

   ```bash
   source scripts/common.sh
   mkdir -p -- "$REDROID_SOURCE_DIR"
   ```

   `00-preflight.sh`는 디렉터리를 자동 생성하지 않으며, Linux에 할당된 RAM 64GB 이상, 소스 파일시스템의 여유 공간 400GB 이상을 요구합니다. `REDROID_BACKING_DIR`을 설정하면 해당 드라이브의 여유 공간도 400GB 이상이어야 합니다.

3. **입력 파일:** 원본 입력은 Git에서 제외하고, 빌드용 APK와 SDK archive의 해시는 `config/`에 고정합니다. GmsCore·Play 스토어 APK와 추출한 번역기 일부는 개인용 이미지에 포함합니다. ARM64 Firefox는 번역 시험에만 사용합니다.

   | 파일 | 위치 | 고정 기록 |
   |---|---|---|
   | microG Services `com.google.android.gms-252432034.apk` | `inputs/microg/` | `config/microg-apk-lock.json` |
   | Play 스토어 `com.android.vending-85372340.apk` | `inputs/microg/` | `config/microg-apk-lock.json` |
   | Lawnchair `Lawnchair.15.0.0.Beta.3.0.apk` | `inputs/apks/` | `config/apps-lock.json` |
   | Fcitx5 `org.fcitx.fcitx5.android-0.1.3-0-g048f581c-x86_64-release.apk` | `inputs/apks/` | `config/apps-lock.json` |
   | Hangul 플러그인 `org.fcitx.fcitx5.android.plugin.hangul-0.1.3-0-g048f581c-x86_64-release.apk` | `inputs/apks/` | `config/apps-lock.json` |
   | Firefox `fenix-159.0.multi.android-x86_64.apk` | `inputs/apks/` | `config/apps-lock.json` |
   | Android SDK `x86_64-35_r09.zip`(약 1.7GB) | WSL `~/.cache/custom-redroid/nativebridge/` | `config/nativebridge-lock.json` |
   | 시험용 `fenix-159.0.multi.android-arm64-v8a.apk` | `inputs/test-apks/` | 이미지에 넣지 않음 |

   Firefox APK는 `https://archive.mozilla.org/pub/fenix/releases/159.0/android/`에서 받습니다. x86_64와 ARM64 APK 모두 같은 Mozilla 서명이어야 합니다.

   SDK archive는 `https://dl.google.com/android/repository/sys-img/google_apis/x86_64-35_r09.zip`에서 받습니다. Android SDK 라이선스 동의가 필요합니다.

   SDK archive와 추출 결과는 프로젝트 밖의 WSL cache에 있으므로 이 폴더만 복사하면 함께 옮겨지지 않습니다. `REDROID_CACHE_DIR`을 바꿨다면 새 cache의 `nativebridge/`에 archive를 준비합니다. 고정된 APK와 기존 `microg-apk-lock.json`을 그대로 사용하는 최초 빌드는 APK import를 다시 실행하지 않아도 됩니다. custom 적용 단계에서 입력 파일의 해시를 검사합니다.

## 처음부터 빌드

저장소 루트의 Linux 셸에서 순서대로 실행합니다. 같은 소스 경로에서 변경 작업을 동시에 실행할 수 없고, 각 단계는 앞 단계의 기록과 SHA를 검사합니다. 번호가 붙은 실행 스크립트(`00`~`03`, `06`~`10`)는 `--help`와 `--dry-run`을 지원합니다. 입력 준비용 Python 스크립트의 옵션은 각 `--help`에서 확인합니다.

```bash
# 1. Check build requirements.
bash scripts/00-preflight.sh

# 2. Sync sources and verify the pinned manifest.
bash scripts/01-sync.sh

# 3. Apply the official redroid patches.
bash scripts/02-apply-redroid.sh

# 4. Extract the ARM64 translator files from the SDK archive.
source scripts/common.sh
python3 scripts/nativebridge_import.py

# 5. Apply the custom feature configuration.
bash scripts/03-apply-custom.sh

# 6. Build, package and generate the SBOM.
bash scripts/06-build.sh --custom
bash scripts/07-package.sh --custom
bash scripts/06-build.sh --custom --sbom
```

번역기 추출 결과가 이미 있으면 4번 명령은 덮어쓰지 않고 중단합니다. 이 경우 `nativebridge_import.py --verify`로 기존 결과를 검사하고 다음 단계로 진행합니다. 이전 `build-logs/`와 `dist/` 없이도 새 소스의 공식 패치·custom 적용·빌드 기록을 생성할 수 있습니다.

결과 패키지는 `dist/a15-r36-custom-<package_id>/`(`redroid.oci.tar`, `rootfs.tar.gz`, `SHA256SUMS`, 소스·패치·빌드 기록)에 생성됩니다. 이미지 이름과 태그는 `config/build.env`의 `REDROID_IMAGE_TAG`(예: `myname/redroid:a15-custom`)로 정합니다. 비워 두면 `redroid-local:a15-r36-custom-<custom plan 앞 12자리>`입니다. 같은 태그가 로컬이나 원격 호스트에 이미 있으면 태그가 새로 가져온 이미지를 가리키도록 교체합니다. 스크립트는 이전 이미지를 삭제하지 않으며, 로컬 패키징은 기존 ID를 `custom-package-state.json`의 `replaced_image`에, 원격 가져오기는 기존 ID를 실행 출력에 남깁니다. 이전 이미지에 다른 태그가 없다면 태그 없는 이미지로 남을 수 있습니다. 태그만 바꿔서 다시 실행하면 같은 빌드를 새 태그로 다시 패키징합니다. 최신 상태는 `build-logs/custom-build-state.json`, `custom-package-state.json`, `custom-sbom-state.json`에 있습니다.

`config/source-lock.json`, `config/custom-lock.json`은 동기화·적용 과정에서 갱신하며, 전체 소스 manifest는 `config/source-manifest.xml`에 기록합니다. 기존 manifest 해시가 있으면 다운로드 결과가 그 값과 일치해야 합니다. 같은 입력을 사용해도 기록 시각과 공식 패치 적용 커밋 ID 등 실행 기록은 달라질 수 있습니다. 현재 구성은 소스·입력·적용 파일의 일치를 검사하며, 이미지의 바이트 단위 재현까지 보장하지 않습니다.

### 변경할 때

- **기능 설정이나 vendor 파일:** `config/features.json` 또는 `vendor/custom/`을 고친 뒤 `03-apply-custom.sh --force`로 다시 적용합니다. 이전 적용 기록과 해시가 같은 파일만 바꾸거나 지우고, 누군가 수정한 파일이 있으면 중단합니다.
- **빠른 컴파일 확인:** `06-build.sh --custom --modules`는 Gralloc·services·번역기·microG 모듈만 빌드합니다.
- **microG·Play 스토어 APK 교체:** `inputs/microg/`의 APK를 바꾼 뒤 `microg_import.py`를 실행합니다. 이 스크립트는 빌드한 `framework-res.apk`에서 privileged 권한 목록을 읽으므로 제품 빌드가 한 번 끝난 뒤에 실행해야 합니다. 그다음 `03-apply-custom.sh --force`와 제품 빌드를 다시 합니다.

  ```bash
  source scripts/common.sh
  python3 scripts/microg_import.py
  ```

- **기본 설치 APK 교체:** `inputs/apks/`에 네 패키지의 APK를 하나씩 두고 `python3 scripts/apps_import.py`로 패키지·서명·버전·ABI·SHA-256을 검사합니다. 원본 바이트를 그대로 `/system/app/CustomLawnchair/`, `/system/app/CustomFcitx5/`, `/system/app/CustomFcitx5Hangul/`, `/system/app/CustomFirefox/`에 설치하며 추가 privileged 권한은 부여하지 않습니다. 압축·정렬 상태를 검사해 필요한 APK에만 Soong 검사 예외를 적용합니다. 기존 `apps-lock.json`과 동일한 입력으로 처음 빌드할 때는 import를 다시 할 필요가 없습니다. 기본 런처·입력기와 언어 선택은 사용자 설정으로 유지합니다.

## 구성 요점

- **Gralloc:** redroid의 prebuilt `gralloc.gbm.so` 대신 gbm_gralloc `a8874a1` 소스를 빌드하고, 미지원 format을 나눗셈 전에 `-EINVAL`로 거부합니다(`patches/gralloc/`). Intel HD 4600에서 실제 부팅 중 P010 요청 12회가 거부되고 allocator가 유지됩니다.
- **ARM64:** SDK에서는 번역기·proxy·설정 파일만 prebuilt로 넣습니다. arm64 guest 라이브러리와 linker는 AOSP `native_bridge_support`가 빌드합니다. 이때 guest libc용 `PRODUCT_SOONG_NAMESPACES`가 필요합니다. 호스트 커널 전역에 등록하는 `binfmt_misc` 설정은 넣지 않습니다.
- **Signature Spoofing:** `patches/frameworks-base/`가 공식 microG 인증서(`9bd06727…`)로 서명된 `com.google.android.gms`·`com.android.vending`에만 Google 호환 서명을 돌려줍니다. 설치·서명 비교 API의 실제 서명은 바꾸지 않습니다.
- **microG·Play 스토어:**
  - 원본 APK를 바이트 그대로 `/system/priv-app`에 넣습니다. Soong의 `preprocessed: true`를 쓰고, 압축된 APK에는 `skip_preprocessed_apk_checks`를 함께 둡니다.
  - privileged 권한은 allowlist로 최소한만 줍니다. GmsCore는 4개, Play 스토어는 7개입니다.
  - GSI가 system_ext에 `ro.control_privapp_permissions=disable`을 두므로 product 파티션에서 `log`로 덮어씁니다.
  - Play 스토어의 `VpaService`는 Android 15 포그라운드 서비스 조건을 만족하지 못해 종료를 반복하므로 sysconfig `component-override`로 끕니다.
  - microG Companion(FakeStore)은 Play 스토어와 패키지 이름이 같아 넣지 않습니다.

## 패키지 가져오기와 이미지 교체

빌드된 패키지를 다른 Linux 호스트로 옮겼다면 패키지 디렉터리에서 무결성을 검사하고 이미지를 가져옵니다. 이미지 태그는 해당 패키지 기록의 값으로 `IMAGE_TAG`를 설정해 사용합니다.

```bash
sha256sum -c SHA256SUMS
docker load -i redroid.oci.tar
docker image inspect "$IMAGE_TAG"
```

OCI archive 가져오기는 Docker Engine 29.9.2의 containerd image store에서 검증했습니다. `rootfs.tar.gz`에는 원본 파일의 UID/GID·모드·링크·SELinux·capability xattr를 보존하며, Docker에 가져온 뒤의 메타데이터 차이는 패키지의 `docker-metadata.json`에 기록합니다. 원격 호스트에서 시험하려면 아래 전송 절차로 제어 파일과 패키지 기록도 함께 배치합니다.

`07-package.sh --custom`은 같은 빌드 실행·태그·이미지 ID의 패키지가 있으면 archive 해시를 검사하고 재사용합니다. `--force`를 함께 주면 기존 산출물을 보존하고 새 디렉터리에 패키징합니다. 같은 태그의 교체 동작은 위의 빌드 결과 설명과 같습니다. 이전 버전이 필요하면 교체 전에 `docker tag <이전 ID> <백업 태그>`로 보존하고, 그 이미지와 일치하는 패키지 기록과 실행 호스트의 시험 폴더도 남겨 둡니다. 이전 이미지·패키지가 없으면 필요한 입력과 설정을 복원해 다시 빌드해야 합니다.

## Linux 호스트에서 실행 검증

Binder를 지원하는 Linux 호스트에서 시험합니다. 이 프로젝트가 만든 `io.custom-redroid.test=true` label 컨테이너만 만들고 지우며, ADB는 실행 호스트의 localhost에만 엽니다. 원격 호스트를 사용할 때는 접속 정보를 사용자 환경에서 설정합니다.

실행 호스트에는 Binder IPC·Binderfs 또는 사용 가능한 Binder 장치가 필요합니다. Intel host GPU 시험에는 `/dev/dri/renderD128`도 필요하며, WSL의 `/dev/dxg`로 이 조건을 대신할 수 없습니다. Binder IPC가 비활성화된 커널에서는 privileged 옵션만으로 부팅할 수 없습니다. Android 15 시험은 `androidboot.use_memfd=1`을 사용합니다.

| profile | 컨테이너 | ADB | GPU |
|---|---|---|---|
| `arm64` | `redroid-custom-arm64-test` | 5561 | host(`/dev/dri/renderD128`), 메모리 상한 8GiB |
| `guest`·`gpu`·`fixed` | `redroid-custom-test`·`-gpu-test`·`-fixed-test` | 5558·5559·5560 | 이전 단계 시험용 |

실행 호스트의 파일은 다른 서비스와 같이 `/data/redroid-custom` 아래에 둡니다. 패키지는 `packages/<package_id>/`, Android `/data`는 `test-data/<실행 ID>/`, 잠금 파일은 `locks/`입니다. 처음 한 번은 관리자가 이 폴더를 만들고 실행 사용자에게 소유권을 줍니다(예: `sudo install -d -o $USER -g $USER /data/redroid-custom`). 폴더가 없거나 실행 사용자 소유가 아니면 `remote_prepare.py`가 중단합니다.

1. `scripts/remote_preflight.py`의 결과(`build-logs/remote-preflight.json`)로 실행 호스트와 시험 경로를 확인합니다.
2. `dist/transfer-test/redroid.docker.tar`에 패키지의 `redroid.oci.tar`를 복사합니다. 그다음 `scripts/prepare_remote_transfer.py --package-state build-logs/custom-package-state.json --directory dist/transfer-test`로 제어 파일 묶음과 해시를 만듭니다.
3. 전송 기록의 값으로 원격 호스트에서 `remote_prepare.py`(시험 폴더 준비)를 실행합니다. archive를 `scp`한 뒤 `remote_load.py`(해시 확인과 `docker load`)를 실행합니다. 두 스크립트는 `ssh test-host python3 - <인자> < scripts/<이름>.py`로 실행합니다. `test-host`는 사용자 환경에 정의한 SSH 별칭으로 바꿉니다.
4. 실행 호스트의 시험 폴더에서 검증합니다. `apks/`에 Firefox APK와 `hello_arm64`(`tests/arm64-exec/hello_arm64.c`를 AOSP clang으로 빌드)를 둡니다.

   ```bash
   python3 scripts/remote_coldboot.py $PWD --boots 3      # 새 /data 콜드 부팅 3회 + 같은 데이터 재시작
   bash scripts/09-verify.sh --profile arm64           # boot·gpu·arm64·microg·crash·oom, 실패 항목은 종료 코드 비트
   ```

5. 결과를 `build-logs/<폴더>/`로 가져온 뒤 최종 보고서와 SBOM·라이선스 묶음을 만듭니다.

   ```bash
   bash scripts/10-collect-report.sh --runtime-dir build-logs/runtime-results --force
   ```

   `--runtime-dir`은 필수이며, 지정한 폴더의 `coldboot-report.json`, `verify-*.json`, `microg-selfcheck.txt`를 읽습니다. `make collect-report RUNTIME_DIR=build-logs/<폴더>`도 같은 경로를 사용합니다. 이전 이미지의 시험 결과나 config의 과거 성공 상태는 현재 부정 시험의 성공으로 사용하지 않습니다.

원격 화면은 `ssh -N -L 127.0.0.1:5561:127.0.0.1:5561 test-host`로 터널을 연 뒤 `adb connect 127.0.0.1:5561`, `scrcpy -s 127.0.0.1:5561`로 봅니다. 같은 호스트에서 접속하면 SSH 터널은 필요하지 않습니다.

물리 키보드로 한글을 입력하려면 `scrcpy -s 127.0.0.1:5561 --keyboard=uhid`로 접속하고 입력기로 Fcitx5(Hangul)를 사용합니다. `WITH_UHID_INPUT`이 켜진 이미지는 컨테이너 안의 `custom_uhid_inputd`가 scrcpy UHID 키보드의 `/dev/input/eventN` 노드를 자동으로 만들고 지웁니다. 한/영 전환은 `Ctrl+Space`, 그리고 Fcitx5 단축키에 `Alt_R`을 추가한 경우 오른쪽 Alt입니다. `remote_keyboard.py`는 이 서비스가 없는 이전 이미지용 수동 대안입니다. 원인과 시험 결과, 이전 KeliBoard의 제한은 [scrcpy 입력 진단](docs/reports/input.md)에 있습니다.

### microG 부정 시험 기록

보고서 생성기는 실행 검증 결과 폴더의 `microg-signature-test.json`을 선택적으로 읽습니다. 다른 경로의 증거는 `10-collect-report.sh --runtime-dir <실행 검증 결과 폴더> --negative-test <JSON 경로>`로 지정합니다. 두 경로는 프로젝트 기준 상대 경로나 절대 경로를 사용할 수 있습니다.

기록이 없으면 보고서는 정상 생성하고 부정 시험을 `NOT TESTED`로 표시합니다. 다만 `--negative-test`로 지정한 파일이 없거나, 기록 형식이 잘못됐거나, `image_id`가 현재 패키지와 다르면 오류로 중단합니다. 과거 기록을 자동으로 찾거나 현재 이미지의 결과로 변환하지 않습니다.

다음은 기록 형식 예시입니다. `image_id`는 시험한 이미지의 실제 ID이며 현재 `custom-package-state.json`과 일치해야 합니다. 인증서와 APK 해시는 재서명한 시험 APK에서 확인한 소문자 SHA-256으로 바꿉니다. 예시의 해시 자리표시자를 그대로 사용하면 검사를 통과하지 못합니다.

```json
{
  "schema_version": 1,
  "image_id": "sha256:<시험한 이미지의 실제 ID>",
  "positive_selfcheck": {
    "system_spoofs_signature": true,
    "services_correct_signature": true
  },
  "negative_resigned_gmscore": {
    "signer_sha256": "<시험 인증서의 SHA-256>",
    "apk_sha256": "<재서명 APK의 SHA-256>",
    "system_spoofs_signature": false,
    "services_correct_signature": false
  },
  "restored_official_selfcheck": {
    "system_spoofs_signature": true,
    "services_correct_signature": true
  }
}
```

이 기록은 실제 시험 후 작성합니다. 공식 APK로 정상 동작 확인 → 다른 키로 재서명한 GmsCore의 spoof 거부 확인 → 공식 APK 복원 후 정상 동작 확인 순서의 결과를 모두 기록합니다. 부정 시험 인증서는 공식 microG 인증서와 달라야 하며, 여섯 결과 값은 문자열이 아닌 JSON boolean이어야 합니다. 정상·복원 확인이 실패하거나 재서명 APK를 허용하면 보고서에 `FAIL`을 표시합니다. 이 옵션은 증거를 읽어 판정하며 APK를 재서명하거나 설치하지 않습니다.

사용한 JSON은 `dist/report-<package_id>/microg-signature-test.json`으로 복사하고 `SHA256SUMS`에 포함합니다. 같은 보고서를 `--force`로 다시 만들 때 현재 부정 시험 증거가 없으면 이전 증거 사본도 제거해 새 보고서와 섞이지 않게 합니다. Android의 `FrameworksServicesTests` 실행 결과는 이 기록과 별개이며, 현재 보고서에는 `NOT TESTED`로 표시합니다.

### 부팅 실패 확인과 시험 중지

전송 도구는 custom 패키지 기록을 시험 폴더의 `build-logs/package-state.json`에 배치합니다. `08-deploy-test.sh`는 이 경로를 읽으므로 아래 명령은 실행 호스트의 시험 폴더에서 실행합니다. 사전 점검 종료 코드 3은 Binder·GPU 등 실행 조건 부족을 뜻하며, 이 경우 컨테이너를 만들지 않습니다. `arm64` profile의 상세 결과는 `build-logs/runtime-arm64-preflight.json`에 있습니다.

```bash
bash scripts/08-deploy-test.sh --profile arm64 --preflight-only
docker inspect redroid-custom-arm64-test
docker logs --tail 200 redroid-custom-arm64-test
```

배포 스크립트는 300초 안에 `sys.boot_completed=1`, `system_server`, Build ID를 확인해야 부팅 성공으로 기록합니다. 시험을 중지할 때는 `docker inspect`에서 `io.custom-redroid.test=true` label, 이미지 ID와 `/data`의 호스트 경로를 확인한 뒤 실행합니다.

```bash
docker stop redroid-custom-arm64-test
```

중지는 데이터 디렉터리를 삭제하지 않습니다. 반복 콜드 부팅 스크립트가 시험 컨테이너를 교체해도 각 `/data`는 실행 호스트의 시험 폴더의 `.cache/test-data/<run_id>`에 남습니다.

**Play 스토어 첫 사용:** Sign in을 누르면 microG 로그인 화면이 열립니다. 로그인한 뒤 microG Settings에서 **Google device registration**과 **Cloud Messaging**을 켭니다. 기기 등록이 꺼져 있으면 Play 스토어가 "DF-DFERH-01" 서버 오류를 냅니다. 이 설정은 Google에 기기 정보를 보내므로 이미지 기본값으로 켜지 않습니다.

## 시험과 주의점

현재 테스트는 75개입니다. 아래 명령은 임시 Git 설정을 사용하며 기존 사용자 설정을 바꾸지 않습니다. 실제 공식 패치 시험은 WSL cache의 `upstream/minijail`과 `upstream/redroid-patches` checkout이 있어야 실행되고, 없으면 해당 시험을 skip합니다. microG Java 정책 14개 검사는 별도 `make microg-host-test`로 실행합니다. `FrameworksServicesTests`에 추가한 Android framework 테스트의 실제 실행은 아직 확인하지 않았습니다.

```bash
(
    set -e
    source scripts/common.sh
    test_git_config=$(mktemp)
    trap 'rm -f -- "$test_git_config"' EXIT
    export GIT_CONFIG_GLOBAL="$test_git_config"
    unset REDROID_GIT_CONFIG
    git config --file "$test_git_config" user.name test
    git config --file "$test_git_config" user.email test@example.invalid
    python3 -m unittest discover -s tests -v
)
```

- **줄바꿈:** Windows에서 Python `write_text`로 파일을 고치면 CRLF가 되어 셸 스크립트가 `set: pipefail: invalid option`으로 깨집니다. `write_bytes`나 LF를 유지하는 편집기를 씁니다.
- **소스 경로:** 순정과 custom 빌드는 같은 `out/`을 쓰므로 마지막 빌드의 이미지만 남습니다. 패키지는 `dist/`에 보존합니다.
- **Docker 저장소:** 검증에 사용한 Docker 버전에서는 classic overlay2의 symlink SELinux xattr 처리 문제가 있었습니다([moby#53616](https://github.com/moby/moby/issues/53616)). OCI 가져오기는 containerd image store에서 확인했습니다. 관련 결과는 [검증 보고서](docs/reports/final-validation.md#docker-저장소-호환성)에 있습니다.
- **재배포:** ARM64 번역기와 Play 스토어는 재배포가 금지된 독점 바이너리입니다. 이미지는 개인용이며 공개 레지스트리에 올리지 않습니다(`dist/report-*/third-party.md`).
