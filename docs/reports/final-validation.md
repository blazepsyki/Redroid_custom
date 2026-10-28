# 최종 통합 이미지 검증 보고서

- 작업일: 2026-10-28, Asia/Seoul
- 범위: microG 기본 탑재(Phase 4)와 Play 스토어 교체, binfmt 직접 실행, 반복 콜드 부팅·데이터 유지(Phase 6), Docker 저장소 호환성, 원본 메타데이터 이미지 부팅
- 제외: 24~72시간 장시간 방치 시험, Google 계정 로그인·FCM 푸시 수신(사용자 계정 필요)

## 최종 이미지

아래 표는 반복 부팅·원본 이미지 시험을 수행한 이전 이미지입니다. 현재 최신 이미지의 결과와 구분하며, [Play 스토어로 교체](#play-스토어로-교체-2026-10-28)와 [최종 보고서](final.md)를 참고해 주세요. 원본 로그·스크린샷·호스트별 경로는 공개 문서에서 제외합니다.

| 항목 | 값 |
|---|---|
| 빌드 실행 | `20261027T170153629337Z`, custom plan `2e2209a067e5b329…` |
| 패키지 ID | `20261027T170501731990Z` |
| 원본 태그 | `redroid-local:a15-r36-custom-2e2209a067e5` |
| 기능 | Gralloc 수정, microG signature spoofing, ARM64 Translation, microG 기본 탑재, 검증 도구 |
| ABI | `x86_64,arm64-v8a` |

## microG 기본 탑재 (`WITH_MICROG=true`)

사용자가 제공한 APK를 `inputs/microg/`(Git 제외)에 두고 `scripts/microg_import.py`로 검사합니다. Android SDK `apksigner`로 서명을 확인해 공식 microG 인증서(`9bd06727…`)가 아니면 기록하지 않으며, 결과는 `config/microg-apk-lock.json`에 버전·SHA-256과 함께 고정합니다.

| 앱 | 버전 | 설치 위치 |
|---|---|---|
| microG Services (`com.google.android.gms`) | 0.3.17.252432 | `/system/priv-app/GmsCore` |
| microG Companion (`com.android.vending`) | 0.3.17.40226 | `/system/app/FakeStore` (privileged 아님) |

**원본 서명 유지:** `android_app_import`의 기본 처리(dex·JNI 비압축 재작성)는 v2 서명을 깨뜨렸습니다(`apksigner`: DOES NOT VERIFY). `preprocessed: true`로 원본 APK를 바이트 그대로 설치하고, 압축된 GmsCore에만 `skip_preprocessed_apk_checks`를 둡니다. 빌드 검사는 설치된 APK의 SHA-256이 원본과 같아야 통과합니다.

**최소 권한:** 빌드한 framework에서 signature|privileged 권한을 읽어, GmsCore가 요청한 14개를 모두 명시했습니다.

| 허용 (4) | 거부 (10) |
|---|---|
| `CHANGE_DEVICE_IDLE_TEMP_WHITELIST`(FCM), `INSTALL_LOCATION_PROVIDER`(네트워크 위치), `START_ACTIVITIES_FROM_BACKGROUND`(로그인·인증 화면), `UPDATE_APP_OPS_STATS` | `DUMP`, `INTERACT_ACROSS_USERS`, `LOCATION_HARDWARE`, `MANAGE_USB`, `MODIFY_PHONE_STATE`, `NETWORK_SCAN`, `UPDATE_DEVICE_STATS`, `WATCH_APPOPS`, `PROVIDE_REMOTE_CREDENTIALS`, `PROVIDE_DEFAULT_ENABLED_CREDENTIAL_SERVICE` |

GSI board 설정(`build/make/target/board/gsi_system_ext.prop`)이 system_ext에 `ro.control_privapp_permissions=disable`을 두어, 처음 시험한 이미지에서는 allowlist가 무시되고 14개가 모두 부여되었습니다. product 파티션에서 `log`로 덮어써 해결했습니다. `log` 모드는 allowlist의 허용·거부를 적용하고, 목록이 없는 다른 앱은 경고만 남기므로 부팅에 영향이 없습니다.

원본 메타데이터 이미지의 실행 검증 결과:

- `ro.control_privapp_permissions=log`
- 허용한 4개만 `granted=true`, 거부한 10개는 부여 목록에 없음
- Companion의 `INSTALL_PACKAGES`·`DELETE_PACKAGES` 미부여
- Self-Check: System spoofs signature, Services·Companion 서명 모두 통과

GsfProxy는 대상 앱의 C2DM 의존성이 확인되지 않아 넣지 않았습니다. Google 계정 로그인과 FCM 수신은 사용자 계정이 필요해 수행하지 않았습니다.

### Play 스토어로 교체 (2026-10-28)

위 이미지에 들어 있던 microG Companion(FakeStore)은 Play 스토어와 같은 `com.android.vending`을 쓰지만 microG 키로 서명되어 있습니다. 시스템 앱으로 있으면 Google 서명 Play 스토어 설치가 서명 불일치(`INSTALL_FAILED_UPDATE_INCOMPATIBLE`)로 거부되고, 사용자 단위 삭제로도 해결되지 않습니다. 그래서 FakeStore를 빼고 실제 Play 스토어를 priv-app `Phonesky`로 넣었습니다.

| 항목 | 값 |
|---|---|
| APK | `com.android.vending` 53.7.23-34 (versionCode 85372340), x86_64·arm64 포함 universal, `inputs/microg/`(Git 제외) |
| 서명 | v3, 현재 인증서 `7ce83c1b…`(RSA 4096). 계보 첫 인증서가 Google 원래 인증서 `f0fd6c…`(microG 위장 정책의 호환 인증서와 같음) |
| 설치 위치 | `/system/priv-app/Phonesky/Phonesky.apk`, 원본 APK 바이트 그대로(`preprocessed`, `skip_preprocessed_apk_checks`) |
| 허용 (7) | `INSTALL_PACKAGES`, `DELETE_PACKAGES`, `CLEAR_APP_CACHE`, `GET_ACCOUNTS_PRIVILEGED`, `USE_INSTALLER_V2`, `MANAGE_USERS`, `INTERACT_ACROSS_USERS` |
| 거부 (27) | 요청한 나머지 privileged 권한 전부. `PACKAGE_VERIFICATION_AGENT`를 거부해 Play 스토어가 다른 설치 경로를 검증·차단하지 않습니다 |
| 비활성 컴포넌트 | `com.google.android.finsky.setup.VpaService`(`/system/etc/sysconfig/component-overrides-com.android.vending.xml`) |
| 검증 이미지 | custom plan `0d22c19d6134…`, 이미지 ID `sha256:316539…`. 빌드와 패키징의 식별자는 [최종 보고서](final.md) 참고 |

#### 단계별 문제와 변경

| 순서 | 증상 | 원인 | 변경 |
|---|---|---|---|
| 1 | Play 스토어 설치 거부 | 시스템 FakeStore와 패키지명은 같고 서명이 다름 | FakeStore 제거, Play 스토어를 priv-app으로 탑재 |
| 2 | 실행 직후 종료 | 사용자 목록 조회에 `MANAGE_USERS` 필요 | 허용 목록에 추가 |
| 3 | 새 `/data` 첫 부팅에서 반복 종료(crash 51줄) | 백그라운드 작업의 `getSharedLibraries(MATCH_ANY_USER)`에 `INTERACT_ACROSS_USERS` 필요 | 허용 목록에 추가 |
| 4 | 로그인 후 "서버에서 정보를 가져오는 중에 오류가 발생했습니다. DF-DFERH-01" | microG 기기 등록(checkin)이 기본으로 꺼져 있어(`checkin_enable_service=0`) Play 서버가 `fdfe` 요청을 `400`으로 거부 | 이미지는 바꾸지 않음. 사용자가 microG Settings에서 Google device registration과 Cloud Messaging을 켬 |
| 5 | "Google Play가 업데이트됩니다 / 새 언어로 전환됩니다" 화면에서 종료 | `VpaService`(초기 설정 때 기본 앱 자동 설치)가 `systemExempted` 포그라운드 서비스를 시작하는데, Android 15는 이 유형에 `SCHEDULE_EXACT_ALARM`·`USE_EXACT_ALARM`·VPN 승인 중 하나를 요구하고 Play 스토어는 어느 것도 요청하지 않음 | sysconfig `component-override`로 이 서비스만 처음부터 끔 |

5번은 정확한 알람 권한을 주는 대신 서비스를 끄는 방식을 택했습니다. Play 스토어 manifest가 두 권한을 요청하지 않아 appop만으로는 통과하지 않습니다. 또 VPN 승인이나 `fgs_type_perm_enforcement_flag_system_exempted`를 끄는 방법은 의미가 다르거나 모든 앱의 검사를 약하게 만듭니다. 이 서비스는 새 기기 초기 설정에서 기본 앱을 내려받는 기능이라 redroid에서는 필요하지 않습니다. 비활성 상태는 Play 스토어가 `/data`로 자체 업데이트한 뒤에도 유지됩니다.

#### 스크립트 변경

- `scripts/microg_import.py`: 패키지별 서명 종류(`microg`/`google`)와 v3 계보(`apksigner lineage`)를 기록·검사합니다. Play 스토어의 허용 권한과 비활성 컴포넌트를 정의합니다.
- `scripts/custom.py`: privileged 앱마다 privapp allowlist를, 비활성 컴포넌트가 있으면 sysconfig `component-override`를 생성합니다. 이전 계획이 `vendor/custom`에 만든 파일 중 새 계획에 없는 것은 이전 기록과 해시가 같을 때만 지우고, 수정된 파일이면 중단합니다.
- `scripts/build.py`: 설치 경로·allowlist·sysconfig 파일 목록을 하드코딩 대신 `config/microg-apk-lock.json`에서 만들고, 설치된 APK가 원본과 같은지 검사합니다.
- `scripts/remote_coldboot.py`: Play 스토어 설치 위치·주요 권한 부여·`VpaService` 비활성·첫 화면 실행을 확인하고, Java 크래시의 프로세스와 원인을 기록합니다. journalctl의 `-- No entries --`를 GPU 오류로 세던 판정을 고쳤습니다.
- `tests/test_custom.py`: 서명 계보 규칙, Play 스토어 권한·비활성 컴포넌트, stale 파일 처리 테스트를 추가했습니다.

microG signature spoofing 정책은 바꾸지 않았습니다. 실제 Google 서명인 Play 스토어는 위장 대상이 아니며, 위장은 계속 공식 microG 인증서의 두 패키지에만 적용됩니다.

#### 사용 절차

1. Play 스토어에서 Sign in을 누르면 microG 로그인 화면으로 넘어갑니다. Google 계정으로 로그인합니다.
2. 앱 목록의 microG Settings에서 **Google device registration**과 **Cloud Messaging**을 켭니다. 기기 등록은 Google에 기기 정보를 보내므로 이미지 기본값으로 켜지 않았습니다.
3. Play 스토어를 다시 열면 자체 업데이트와 언어 리소스 설치가 진행됩니다. 업데이트 파일을 받은 뒤 Play 스토어가 닫히면 설치됩니다.

#### 실행 검증 결과

이미지 `sha256:316539…`(권한 7개 + `VpaService` 비활성)의 실행 검증 결과입니다.

| 회차 | 부팅 | Play 스토어 실행 | Gralloc 부하 | P010 거부 | Firefox | binfmt | crash | OOM |
|---|---|---|---|---|---|---|---|---|
| 콜드 1 | 28.7초 | 통과 | 30/30 | 12 | 통과 | 통과 | 0 | 0 |
| 같은 데이터 재시작 | — | 통과 | 5/5 | 12 | 통과 | 통과 | 0 | 0 |

- 부여된 privileged 권한은 허용한 7개와 정확히 같습니다(이전 이미지에서 확인).
- `VpaService`는 `pm query-services`에서 조회되지 않고, 직접 시작해도 `Not found`입니다. 같은 패키지의 다른 서비스는 조회됩니다(`vpa-service-check.txt`). sysconfig `component-override`는 사용자 설정(`disabledComponents`)이 아니라 패키지 분석 단계에서 적용되므로 `dumpsys package`의 비활성 목록에는 나오지 않습니다.
- Play 스토어 첫 화면이 열리고, Sign in을 누르면 microG 로그인 화면(`org.microg.gms.auth.login.LoginActivity`)으로 넘어갑니다.

`VpaService` 설정 전 중간 이미지에서는 로그인과 기기 등록 후 5번 종료를 재현했습니다. `pm disable`로 `VpaService`만 끄자 크래시가 멈췄고, 자체 업데이트가 `53.7.24-34`(versionCode 85372440, `/data/app`)로 완료됐습니다. 업데이트 뒤에도 서비스 비활성과 크래시 0이 유지됐습니다.

최신 이미지에서 로그인·기기 등록 후 자체 업데이트, 앱 검색·설치·라이선스 확인은 아직 수행하지 않았습니다.

## binfmt 직접 실행

호스트 커널 전역에 등록하는 `ndk_translation.rc`와 `binfmt_misc` 핸들러는 계속 제외합니다. 등록이 가리키는 interpreter인 `/system/bin/ndk_translation_program_runner_binfmt_misc_arm64`만 이미지에 넣었습니다. 커널은 interpreter 경로를 실행하는 프로세스의 mount namespace에서 찾으므로, 호스트에 등록이 이미 있으면 컨테이너 안에서 ARM ELF가 직접 실행됩니다. 등록이 없는 호스트에서는 동작하지 않으며, 이 프로젝트는 호스트 등록을 하지 않습니다.

시험 프로그램 `tests/arm64-exec/hello_arm64.c`(libc 없는 aarch64 정적 실행 파일)는 매 부팅에서 `custom-redroid arm64 direct exec ok`를 출력했고, guest `linker64`도 실행되었습니다.

## 반복 콜드 부팅과 데이터 유지

`scripts/remote_coldboot.py`가 `arm64` profile(host GPU, 메모리 상한 8GiB)로 새 `/data` 부팅 3회와 같은 데이터 재시작 1회를 수행합니다. 회차마다 이 시험이 만든 컨테이너만 제거합니다.

결과 표는 아래 [원본 메타데이터 이미지](#원본-메타데이터-이미지) 절에 있습니다. 원본 메타데이터의 실행 결과와 symlink SELinux xattr를 제외한 호환 변형의 결과를 구분합니다.

- 부팅 시간은 컨테이너 생성부터 `sys.boot_completed=1` 확인까지이며 5초 간격 확인이라 오차가 있습니다.
- Gralloc 부하는 `custom_gralloc_format_test`(5개 format)를 회차마다 30회 반복한 결과입니다.
- Firefox 렌더링은 Gecko가 접근성 트리에 노출한 example.com 본문으로 판정하고 화면을 함께 저장했습니다.
- 재시작 후 Firefox 설치·프로필·이전 탭이 유지되었습니다(탭 수 증가 확인).
- 호스트 커널 로그에 i915 hang·reset 기록이 없었습니다.

## Docker 저장소 호환성

Docker 29.9.2의 classic overlay2에서 symlink의 SELinux xattr를 링크 대상에 적용하려다 가져오기가 실패하는 현상을 관측했습니다([moby#53616](https://github.com/moby/moby/issues/53616)). 원본 OCI archive 가져오기와 아래 기능 검증은 containerd image store에서 수행했습니다. 운영 호스트의 설정·서비스 구성과 이전 작업 내역은 이 문서에 포함하지 않습니다.

## 원본 메타데이터 이미지

전환 후 원본 OCI archive(패키지 `20261027T170501731990Z`의 `redroid.oci.tar`)를 그대로 가져왔습니다. 이미지 ID는 원본 manifest digest `sha256:a9ecaa70…`와 같습니다.

symlink 534개를 컨테이너 안에서 `rootfs-probe`(lgetxattr)로 읽어 원본 메타데이터와 대조했습니다. 533개가 SELinux 레이블·링크 대상·소유자까지 일치했습니다. 나머지 `/etc`는 Docker가 컨테이너 시작 시 `/etc/hosts`·`resolv.conf`를 넣기 위해 만드는 디렉터리이며, Android는 `/system/etc`를 사용합니다. `docker export`는 SELinux xattr를 tar에 쓰지 않으므로 이 대조에 사용할 수 없습니다.

같은 `remote_coldboot.py` 시험 결과:

| 회차 | 부팅 | allocator | Gralloc 부하 | P010 거부 | Firefox 설치·실행·유지·렌더링 | binfmt | crash | OOM | 메모리 최대 |
|---|---|---|---|---|---|---|---|---|---|
| 콜드 1 | 26.0초 | 146 유지 | 30/30 | 12 | 통과 | 통과 | 0 | 0 | 약 3.8GiB |
| 콜드 2 | 22.1초 | 148 유지 | 30/30 | 12 | 통과 | 통과 | 0 | 0 | 약 3.9GiB |
| 콜드 3 | 22.1초 | 145 유지 | 30/30 | 12 | 통과 | 통과 | 0 | 0 | 약 4.0GiB |
| 같은 데이터 재시작 | — | 146 유지 | 5/5 | 12 | 통과 | 통과 | 0 | 0 | 약 2.9GiB |

같은 인스턴스에서 `ro.control_privapp_permissions=log`, GmsCore에 허용한 4개 권한만 부여, microG Self-Check의 spoof·서명 세 항목 통과를 확인했습니다(`microg-selfcheck.png`). 원본 메타데이터 이미지의 부팅과 기능 시험을 통과한 것으로 판정합니다.

## 빌드 식별정보 고정

빌드 머신의 계정명에서 `eng.<계정명>`을 자동 생성하던 동작을 제거했습니다. 빌더는 사용자 `redroid`, 호스트 `redroid-builder`, 빌드 번호 `redroid.a15r36`을 전달합니다. UID/GID는 파일 쓰기 권한에만 사용합니다.

첫 증분 빌드는 system 속성만 갱신하고 vendor 속성은 이전 값을 유지해 사후 검사에서 실패했습니다. 원인은 vendor `build.prop` 생성 명령이 읽는 fingerprint·빌드 번호 파일의 Ninja 의존성 누락이었습니다. `patches/build-make/0001-track-vendor-build-identity.patch`로 의존성을 추가한 뒤 vendor 속성과 이미지를 다시 생성했습니다. 전체 `out/`은 삭제하지 않았습니다.

| 항목 | 결과 |
|---|---|
| 제품 빌드 | `20261028T053130525777Z`, 성공 |
| 패키지 | `20261028T053327563763Z` |
| 이미지 ID | `sha256:e00f5c6818197ce27f967e636ceba1ee3e97e1017508f04c6fc51b6a049aa8a3` |
| `ro.build.user` | `redroid` |
| `ro.build.host` | `redroid-builder` |
| `ro.build.version.incremental` | `redroid.a15r36` |
| 제품·rootfs archive·Docker snapshot의 `build.prop` | 8개 파일의 식별정보 검사 통과 |
| 계정명·호스트명 흔적 검색 | 일반 파일 2,873개와 압축 파일 내부 항목 51,601개에서 검색 결과 0건 |
| Docker 이미지 config | 계정명·호스트명 검색 결과 0건 |
| 회귀 테스트 | 64개 통과 |
| SBOM | 새 custom plan으로 재생성 |
| 새 이미지 부팅·기능 검증 | NOT TESTED |

검색에는 빌드 계정명·기존 incremental 번호·빌드 호스트명의 UTF-8 및 UTF-16LE 표현을 사용했고, APK/JAR 등 ZIP 항목과 gzip 내용도 검사했습니다. 위 검사는 해당 식별자의 잔존 여부를 확인한 것이며, 일반적인 개인정보 전체를 탐지하는 검사는 아닙니다.

이전 이미지와 실행 중인 컨테이너는 보존했습니다. 새 이미지를 가져와 컨테이너를 다시 생성해야 실행 화면의 빌드 번호가 바뀝니다. 이 절의 정적 검사 결과를 위의 이전 이미지 부팅·앱 시험 결과와 구분합니다.

## 남은 항목

- 24~72시간 방치 후 상태 확인
- Google 계정 로그인, FCM 푸시 수신, Play 스토어 앱 설치
