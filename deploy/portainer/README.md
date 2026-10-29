# redroid-custom Portainer Stack

실제로 사용할 custom redroid 인스턴스를 NAS의 다른 서비스와 같이 Portainer Stack으로 운영합니다. 정의 파일은 [`docker-compose.yml`](docker-compose.yml), 변수 예시는 [`stack.env.example`](stack.env.example)입니다. 이미지 빌드·시험 절차와는 별개입니다.

## 구성

| 항목 | 값 |
|---|---|
| 컨테이너 | `redroid-custom` |
| 이미지 | `${REDROID_IMAGE}` (기본 `redroid-custom:15.0.0-64only-261028`), `pull_policy: never` |
| 재시작 | `unless-stopped` |
| ADB | `${REDROID_ADB_PORT}` (기본 5555 → 컨테이너 5555 |
| Android `/data` | `/data/redroid-custom/data` |
| GPU | `/dev/dri` bind, `androidboot.redroid_gpu_mode=host` |
| 자원 상한 | CPU 1.5, 메모리 8GiB |
| 로그 | json-file, 10MB × 3 |

이미지는 레지스트리가 아니라 NAS에 직접 가져온 로컬 이미지입니다. Portainer의 "Re-pull image" 옵션은 끄고 배포합니다. 켜면 받을 곳이 없어 실패합니다. Google SDK에서 추출한 번역 바이너리가 들어 있으므로 레지스트리에 올리지 않습니다.

## 호스트 사전 준비 (한 번)

1. 서비스 폴더: `/data/redroid-custom/data`가 있어야 합니다. 없으면 관리자가 만들고 실행 사용자에게 소유권을 줍니다.

   ```bash
   sudo install -d -o $USER -g $USER /data/redroid-custom /data/redroid-custom/data
   ```

2. 커널 모듈 자동 로드: Android는 Binder가 필요하고, scrcpy `--keyboard=uhid`는 UHID가 필요합니다. 2026-10-28 기준 Psyki-NAS는 `uhid`만 `/etc/modules-load.d/uhid.conf`에 등록되어 있고 `binder_linux`는 현재 로드만 된 상태입니다. 재부팅 뒤 자동 시작이 실패하지 않도록 등록합니다.

   ```bash
   echo binder_linux | sudo tee /etc/modules-load.d/redroid.conf
   ```

## 기존 수동 컨테이너에서 전환

2026-10-28 현재 인스턴스는 수동으로 만든 `redroid-custom-arm64-test`이며 `/data`가 `/data/redroid-custom/test-data/20261028T025207233730Z`에 있습니다. 같은 데이터를 스택으로 넘기는 순서입니다.

1. 수동 컨테이너를 멈추고 지웁니다. `/data` 폴더는 지우지 않습니다.

   ```bash
   docker stop -t 30 redroid-custom-arm64-test
   docker rm redroid-custom-arm64-test
   ```

2. Android `/data`를 스택 경로로 옮깁니다. 같은 파일시스템 안의 이름 변경이라 소유자·권한이 유지됩니다.

   ```bash
   mv /data/redroid-custom/test-data/20261028T025207233730Z /data/redroid-custom/data
   rmdir /data/redroid-custom/test-data   # 비어 있을 때만 지워집니다
   ```

3. Portainer → Stacks → Add stack에서 이름을 `redroid-custom`으로 하고 `docker-compose.yml` 내용을 붙여 넣습니다. Environment variables에는 `stack.env.example`의 값을 필요한 만큼 넣습니다.
4. 배포 후 아래 명령으로 확인합니다.

   ```bash
   adb connect <NAS IP>:5555
   adb -s <NAS IP>:5555 shell getprop sys.boot_completed      # 1
   adb -s <NAS IP>:5555 shell getprop init.svc.custom_uhid_inputd   # running
   adb -s <NAS IP>:5555 shell ls -l /dev/uhid                 # uhid uhid 0660
   ```

계정·앱·Fcitx5 설정은 `/data`에 있으므로 그대로 유지됩니다.

## 이미지 갱신

1. 새 이미지를 NAS에 가져옵니다(`docker load`). 같은 태그로 가져오면 태그가 새 이미지로 바뀝니다.
2. 태그를 바꿨다면 스택의 `REDROID_IMAGE`를 새 태그나 이미지 ID로 바꿉니다.
3. Portainer에서 스택을 "Update the stack"으로 다시 배포합니다(Re-pull image 끔). `/data`는 그대로 쓰입니다.
4. 위의 확인 명령을 다시 실행합니다. 이전 이미지는 자동으로 지워지지 않으므로 필요 없으면 `docker image rm`으로 정리합니다.

## 접속

```powershell
scrcpy -s Psyki-NAS:5555 --keyboard=uhid
```

한/영 전환은 `Ctrl+Space`입니다. 키보드의 한/영 전용 키는 scrcpy UHID 모드에서 전달되지 않습니다. 자세한 내용은 [scrcpy 입력 진단](../../docs/reports/input.md)에 있습니다.
