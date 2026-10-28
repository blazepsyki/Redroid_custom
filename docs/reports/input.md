# scrcpy 입력 진단과 추가 APK 기본 설치

## 기본 설치 APK

| 앱 | 패키지 | 버전 | 설치 위치 |
|---|---|---|---|
| Lawnchair | `app.lawnchair` | 15.Beta 3 | `/system/app/CustomLawnchair` |
| Fcitx5 | `org.fcitx.fcitx5.android` | 0.1.3-0-g048f581c | `/system/app/CustomFcitx5` |
| Hangul 플러그인 | `org.fcitx.fcitx5.android.plugin.hangul` | 0.1.3-0-g048f581c | `/system/app/CustomFcitx5Hangul` |

사용자가 제공한 원본 APK를 서명 검사 후 `config/apps-lock.json`에 고정합니다. 패키지·인증서·해시가 달라지면 적용을 중단하며, APK 자체는 Git에서 제외합니다. 추가 privileged 권한이나 기본 앱 강제 선택은 하지 않습니다. 빌드 후 설치 파일의 SHA-256도 원본과 대조합니다.

Lawnchair는 압축·정렬 검사를 통과하므로 `skip_preprocessed_apk_checks`를 설정하지 않습니다. 다른 APK도 압축 JNI·정렬 상태를 확인해 원본 서명을 유지하는 데 필요한 검사 예외만 적용합니다. 필요하지 않은 예외를 설정하면 Soong이 빌드를 거부하므로 APK별로 판정합니다.

## `--keyboard=uhid` 키 입력이 전달되지 않는 원인

scrcpy가 UHID 키보드를 만들면 커널에 `/sys/class/input/eventN`이 생깁니다. 그런데 컨테이너 안에는 `/dev/input/eventN` 노드가 생기지 않아 Android EventHub가 키보드를 보지 못했습니다.

- 컨테이너 안의 `ueventd`는 실행 중이고 호스트의 `/sys/class/input`도 보입니다.
- Linux 4.18 이후 장치 uevent는 호스트의 초기 network namespace에만 전달됩니다. 컨테이너의 `ueventd`는 새 UHID 장치를 알 수 없습니다.
- scrcpy는 연결할 때마다 UHID 장치를 새로 만들므로 event 번호도 매번 바뀝니다. 이전의 수동 도구(`remote_keyboard.py`)는 연결마다 다시 실행해야 했습니다.

## 자동 노드 관리 (`WITH_UHID_INPUT`)

`vendor/custom/input/`의 `custom_uhid_inputd`가 `/vendor/bin`에 설치되고 init 서비스(`class main`, `user root`, `group input`)로 시작합니다.

- 0.3초 간격으로 `/sys/class/input/event*`를 확인합니다. sysfs는 inotify 알림을 주지 않습니다.
- 대상은 `/devices/virtual/misc/uhid/` 아래 장치 중 이름이 `scrcpy`인 장치뿐입니다. 호스트 물리 키보드나 다른 입력 장치는 노출하지 않습니다.
- 새 장치는 이 컨테이너의 프로세스가 `/dev/uhid`를 열고 있을 때만 연결합니다. 같은 호스트의 다른 컨테이너가 만든 scrcpy 장치를 가져오지 않기 위한 조건입니다. 두 컨테이너에서 동시에 UHID 연결을 시작하는 경우는 구분할 수 없습니다.
- 노드는 `root:input 0660`으로 처음부터 만듭니다. EventHub가 `IN_CREATE`를 보는 시점에 이미 열 수 있어야 하기 때문입니다. 장치가 사라지거나 번호가 다른 장치로 바뀌면 노드를 지웁니다.
- 로그는 logcat의 `custom_uhid_inputd` tag로 남깁니다. 컨테이너에서 `/dev/kmsg`에 쓰면 호스트 커널 로그에도 남으므로 사용하지 않습니다.
- 시작할 때 컨테이너의 `/dev/uhid`, `/dev/uinput`을 `ueventd.rc`와 같은 `uhid:uhid 0660`으로 맞춥니다. Docker는 호스트 장치 노드를 호스트 권한(`root:root 0600`) 그대로 복사하고, 컨테이너의 `ueventd`는 coldboot를 하지 않습니다. 이 상태에서는 scrcpy 서버(shell, uhid 그룹)가 `open failed: EACCES`로 실패합니다. 컨테이너의 tmpfs `/dev`만 바꾸며 호스트 노드는 바꾸지 않습니다.
- 서비스 권한은 `MKNOD`, `SYS_PTRACE`(다른 프로세스의 `/proc/<pid>/fd` 확인), `DAC_READ_SEARCH`, `CHOWN`만 둡니다.

`remote_keyboard.py`는 이 서비스가 없는 이전 이미지에서만 쓰는 수동 대안으로 남깁니다.

## 실행 시험 (2026-10-28, NAS `redroid-custom-arm64-test`)

scrcpy와 같은 이름·HID descriptor를 쓰는 시험용 UHID 키보드(`tests/uhid/uhid_type.c`)를 컨테이너 안에서 실행했습니다. 연락처 편집 화면의 입력란에 입력한 뒤 저장하지 않고 닫았습니다.

| 항목 | 결과 |
|---|---|
| 노드 생성 | 장치 생성 직후 `/dev/input/event15` (`13:79`, `root:input 0660`) |
| EventHub 등록 | `scrcpy`, `Classes: KEYBOARD \| ALPHAKEY` |
| 노드 제거 | UHID 장치 해제 직후 제거 |
| 2벌식 조합 | `g k s r m f` → `한글`, `d h c l d` → `오칭` |
| 한/영 전환 | `Ctrl+Space`, 오른쪽 `Alt` 모두 전환. `Shift+Space`는 공백 입력 |

### 이미지 반영 후 재시험

`dist/a15-r36-custom-20261028T063039161002Z`(image `sha256:d5add9db624f…`)로 `redroid-custom-arm64-test`를 다시 만들었습니다. `/data`, GPU, 포트 5561, 자원 상한, 부팅 인자는 기존 컨테이너와 같게 두었습니다. 기존 설정은 `build-logs/remote-uhid/container-before.json`에 있습니다.

- `init.svc.custom_uhid_inputd=running`, init이 시작한 서비스가 노드를 생성·제거
- Google 계정, 설치 앱, Fcitx5 단축키 설정 유지
- 컨테이너를 새로 만든 뒤 실제 scrcpy 연결에서 `/dev/uhid` 열기가 `EACCES`로 실패했습니다. 이전 컨테이너에서는 수동으로 바꾼 권한이 남아 있었습니다. 실행 중인 컨테이너의 노드만 `uhid:uhid 0660`으로 바로 고쳤고, 이후 서비스가 시작할 때 맞추도록 수정했습니다.
- 재시작 직후 Fcitx5는 영문 상태로 시작합니다. `Ctrl+Space` 또는 오른쪽 Alt로 한글로 전환한 뒤 입력합니다.

처음에는 수동으로 실행한 prototype 바이너리로, 이후 AOSP로 빌드한 `custom_uhid_inputd`로 같은 생성·제거를 확인했습니다. prototype은 `/dev/kmsg`에 기록해 NAS 호스트 커널 로그에 20줄이 남았습니다. AOSP 빌드판은 logcat만 사용합니다.

## Fcitx5 한글 입력 설정

기본 설치 대상은 Fcitx5 본체와 Hangul 플러그인입니다. 두 APK는 x86_64 전용이며 버전·서명이 일치합니다. 최소 지원 버전은 API 23이므로 Android 15의 설치 기준을 충족합니다. target SDK 36은 최소 지원 버전과 구분합니다. 본체·플러그인의 버전이 다르거나 최소 지원 버전이 API 35보다 높으면 import를 중단합니다.

설치 후 Fcitx5를 입력기로 선택하고 Fcitx5 설정에서 Hangul을 입력기 목록에 추가해야 합니다. 플러그인을 설치한 것만으로 현재 입력기나 사용자 언어 설정을 강제로 바꾸지 않습니다. [공식 프로젝트](https://github.com/fcitx5-android/fcitx5-android)는 Hangul 플러그인과 물리 키보드 기능을 안내합니다.

한/영 전환 키는 Fcitx5의 전역 설정 `[Hotkey/TriggerKeys]`입니다. 기본값은 `Control+space`, `Hangul`입니다. scrcpy UHID 키보드의 HID descriptor는 usage `0x65`까지만 보내므로, 전용 한/영 키(LANG1, `0x90`)는 Android에 도착하지 않습니다. 한/영 키를 오른쪽 Alt로 보내는 키보드에서도 전환되도록 시험 인스턴스에는 `Alt_R`을 추가했습니다.

```ini
# /sdcard/Android/data/org.fcitx.fcitx5.android/files/config/config
[Hotkey/TriggerKeys]
0=Control+space
1=Hangul
2=Alt_R
```

이 설정은 앱 데이터이므로 새 `/data`에서는 Fcitx5 설정 화면(전역 설정 → 단축키 → 입력기 전환)에서 같은 키를 추가합니다. Windows에서 scrcpy를 쓸 때는 Windows IME 상태와 관계없이 UHID가 물리 키를 보냅니다. 한/영 키가 오른쪽 Alt가 아니라 별도 키로 처리되는 키보드에서는 `Ctrl+Space`를 사용합니다.

## 이전 KeliBoard의 영문 키 전달과 한글 조합 제한

UHID는 물리 키 이벤트를 Android에 전달합니다. Windows IME에서 조합한 한글 문자열을 그대로 전송하는 기능이 아닙니다. Android 쪽 입력기와 물리 키보드 레이아웃도 설정해야 합니다([scrcpy 공식 키보드 문서](https://github.com/Genymobile/scrcpy/blob/master/doc/keyboard.md)).

제공된 KeliBoard 4.0.1의 한글 화면 키보드와 물리 키보드 처리는 별개입니다. 해당 버전 소스의 `ProductionFlags.IS_HARDWARE_KEYBOARD_SUPPORTED`는 `false`입니다. 원본 APK의 DEX에서도 `LatinIME.onKeyDown`이 Hangul 처리 경로를 호출하지 않고 상위 `InputMethodService.onKeyDown`으로 넘기는 것을 확인했습니다.

- 참조 소스: [KeliBoard v4.0.1의 ProductionFlags](https://github.com/GPLaider/KeliBoard/blob/v4.0.1/app/src/main/java/helium314/keyboard/latin/define/ProductionFlags.kt)
- 확인한 소스 commit: `7dc6d076025ece10a71823bc15ab4109bf59da47`
- APK와 인증서 해시: `config/apps-lock.json`

따라서 이전 KeliBoard 원본 APK에서는 UHID 키 전달을 복구해도 물리 키보드 한글 조합까지 해결됐다고 판정할 수 없습니다. 이 제한은 Android 빌드 설정이나 `--prefer-text`만으로 켤 수 없습니다. 원본 APK의 서명·바이트는 변경하지 않았습니다.

이전 KeliBoard를 계속 사용하는 경우의 대안은 다음과 같습니다.

- KeliBoard에서 한국어 레이아웃을 선택하고 화면 키보드로 입력합니다. 물리 키보드 연결 중 화면 키보드를 표시하는 옵션은 Android 키보드 설정에서 켭니다.
- 컴퓨터에서 조합한 한글을 복사한 뒤 scrcpy의 붙여넣기를 사용합니다. [공식 clipboard 문서](https://github.com/Genymobile/scrcpy/blob/master/doc/control.md#copy-paste)를 참고합니다.
- 물리 키보드의 직접 한글 조합에는 이를 지원하는 입력기 또는 별도로 빌드·서명한 KeliBoard가 필요합니다. 별도 서명 APK는 현재 원본과 업데이트 호환성을 확인해야 합니다.

설정 페이지는 다음으로 열 수 있습니다.

```bash
adb -s 127.0.0.1:5561 shell am start -a android.settings.HARD_KEYBOARD_SETTINGS
```
