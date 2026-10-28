# scrcpy 입력 진단과 추가 APK 기본 설치

## 기본 설치 APK

| 앱 | 패키지 | 버전 | 설치 위치 |
|---|---|---|---|
| Lawnchair | `app.lawnchair` | 15.Beta 3 | `/system/app/CustomLawnchair` |
| Fcitx5 | `org.fcitx.fcitx5.android` | 0.1.3-0-g048f581c | `/system/app/CustomFcitx5` |
| Hangul 플러그인 | `org.fcitx.fcitx5.android.plugin.hangul` | 0.1.3-0-g048f581c | `/system/app/CustomFcitx5Hangul` |

사용자가 제공한 원본 APK를 서명 검사 후 `config/apps-lock.json`에 고정합니다. 패키지·인증서·해시가 달라지면 적용을 중단하며, APK 자체는 Git에서 제외합니다. 추가 privileged 권한이나 기본 앱 강제 선택은 하지 않습니다. 빌드 후 설치 파일의 SHA-256도 원본과 대조합니다.

Lawnchair는 압축·정렬 검사를 통과하므로 `skip_preprocessed_apk_checks`를 설정하지 않습니다. 다른 APK도 압축 JNI·정렬 상태를 확인해 원본 서명을 유지하는 데 필요한 검사 예외만 적용합니다. 필요하지 않은 예외를 설정하면 Soong이 빌드를 거부하므로 APK별로 판정합니다.

## 키 입력 자체가 전달되지 않는 문제

scrcpy 서버 로그에서 UHID 장치 열기의 `EACCES`와 `ENODEV`를 확인했습니다. 이후 `/dev/uhid` 열기는 성공했으나, 커널에 생성된 UHID 입력 장치의 event 노드가 컨테이너 안에 없는 경우를 별도 시험 장치로 재현했습니다. 올바른 그룹·모드로 노드를 생성하자 Android EventHub가 장치를 인식했고, 기존 scrcpy 키보드도 인식했습니다. 재접속 후 영문 키 입력 성공은 사용자 재시험으로 확인했습니다.

문제는 다음 순서로 구분합니다.

1. 실행 호스트 커널에 UHID 드라이버가 있고 `/dev/uhid`를 사용할 수 있어야 합니다. `ENODEV`는 단순 파일 권한 변경으로 해결되지 않습니다.
2. scrcpy를 실행하는 Android 프로세스가 장치를 열 수 있어야 합니다. `EACCES`는 해당 프로세스의 uid·그룹과 장치 권한을 확인합니다.
3. 커널에 생긴 input 장치의 event 노드가 컨테이너 안에도 있어야 하며, system_server가 읽을 수 있어야 합니다.
4. `dumpsys input`에서 scrcpy 장치가 `KEYBOARD`로 등록되는지 확인합니다.

세 번째 조건을 처리하는 도구를 실행 호스트에서 사용합니다. 예시의 `event7`은 현재 연결이 생성한 장치 번호로 바꿔야 합니다. 다른 컨테이너의 scrcpy 장치를 선택하지 않도록 연결 전후 목록을 비교합니다.

```bash
python3 scripts/remote_keyboard.py --list
python3 scripts/remote_keyboard.py --container redroid-custom-arm64-test --event event7
```

도구는 프로젝트 label이 있는 실행 중 컨테이너와 이름이 `scrcpy`인 Linux input 장치 하나만 허용합니다. 호스트 입력 디렉터리가 마운트되어 있으면 중단합니다. event 노드는 root:input, 0660으로 생성하며 호스트의 `/dev` 권한을 바꾸지 않습니다. 모든 장치를 자동 노출하는 서비스는 추가하지 않았습니다. UHID 연결을 새로 만들면 장치 번호와 노드 상태를 다시 확인해야 합니다.

## 현재 입력기 구성

기본 설치 대상은 Fcitx5 본체와 Hangul 플러그인입니다. 두 APK는 x86_64 전용이며 버전·서명이 일치합니다. 최소 지원 버전은 API 23이므로 Android 15의 설치 기준을 충족합니다. target SDK 36은 최소 지원 버전과 구분합니다. 본체·플러그인의 버전이 다르거나 최소 지원 버전이 API 35보다 높으면 import를 중단합니다.

설치 후 Fcitx5를 입력기로 선택하고 Fcitx5 설정에서 Hangul을 입력기 목록에 추가해야 합니다. 플러그인을 설치한 것만으로 현재 입력기나 사용자 언어 설정을 강제로 바꾸지 않습니다. [공식 프로젝트](https://github.com/fcitx5-android/fcitx5-android)는 Hangul 플러그인과 물리 키보드 기능을 안내합니다. 이 구성의 실제 한글 입력 성공은 실행 시험 결과와 구분합니다.

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
