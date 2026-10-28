# Custom redroid Android 15 Build Report

`scripts/10-collect-report.sh`가 2026-10-28T05:08:22.871922+00:00에 기록 파일에서 생성했습니다. 근거 파일은 각 항목 옆에 적었습니다.

- Base AOSP: `android-15.0.0_r36` / manifest commit `a0601d2912a6cedcaf97e1372173f8704a2715b0` / build ID `BP1A.250505.005.D1`
- redroid manifest/patch commits: local_manifests `a116ba2c17fa88de495434211ea9c74f0d3f922f`, redroid-patches `a5c70c0dd89c12b5027c902ab4182f31d38f3bb7`(23개 패치)
- Product: `redroid_x86_64_only-ap3a-userdebug`
- Builder: Docker 29.9.2, builder `sha256:8dfb5387322e…`
- Gralloc source commit: gbm_gralloc `a8874a18ad7884ea713f45047efad080fe973712` / patch: `0001-preserve-redroid-map-stride.patch`, `0002-reject-unsupported-allocation-formats.patch` / tests: PASS
- ARM64 translator source: Android SDK `system-images;android-35;google_apis;x86_64` r9 / archive SHA-256 `c67b9ba0ff5bc0eb…`, 83개 파일 해시는 `config/nativebridge-lock.json` / runtime: PASS
- microG APK versions/cert hashes: GmsCore 0.3.17.252432 `9bd06727e62796c0…`, Play 스토어 53.7.23-34 [0] [PR] 1001145691 계보 `f0fd6c5b410f25cb…`→`7ce83c1b71f3d572…`
- Docker image ID + tag + SHA: `sha256:316539956f6699109ef1205532cdc5d1554d6a8264f7195b65e2bcf673a87a37` / `redroid-custom:15.0.0-64only-261028` / package `20261028T041141589306Z`, `dist/a15-r36-custom-20261028T041141589306Z/SHA256SUMS`
- GPU: `Intel, Mesa Intel(R) HD Graphics 4600 (HSW GT2), OpenGL ES 3.2 Mesa 24.0.8 (git-441f064c1c)` / Mesa renderer: PASS
- Firefox ARM64: install / native library / page load: PASS / PASS / PASS
- microG Self-Check: spoof / services / vending(Play Store): PASS / PASS / PASS
- Unsupported signer negative tests: NOT TESTED (현재 이미지의 부정 시험 기록 없음)
- Android framework 계측 테스트(FrameworksServicesTests): NOT TESTED
- Gralloc crash regression: PASS (P010 요청 거부, allocator PID 유지, crash 0)
- 24~72h stability: NOT TESTED
- Regressions / follow-up issues: Google 계정 로그인 유지·FCM 푸시 수신·Play 스토어 앱 설치 NOT TESTED. Play 스토어는 microG 기기 등록을 켜야 서버 요청이 성공함
- Redistribution policy / licenses: 개인용 이미지. ARM64 번역기와 Play 스토어는 재배포 금지. `dist/report-20261028T041141589306Z/third-party.md`, `sbom.spdx.json`(패키지 824개), `NOTICE.xml.gz`
- 이전 이미지와 재배포: [패키지 가져오기와 이미지 교체](../../README.md#패키지-가져오기와-이미지-교체). 이전 이미지가 없으면 필요한 입력과 설정을 복원해 다시 빌드

## 최신 이미지 실행 검증 결과

선택한 실행 검증 기록의 콜드 부팅 1회와 같은 데이터 재시작, `09-verify.sh` 결과입니다. 원본 기록과 개인 환경의 경로는 공개 문서에 포함하지 않습니다.

| 항목 | 결과 |
|---|---|
| boot | PASS |
| gpu | PASS |
| arm64 | PASS |
| microg | PASS |
| crash | PASS |
| oom | PASS |

위 판정은 지정한 실행 검증 결과와 현재 이미지의 부정 시험 기록을 기준으로 합니다. 이전 이미지의 시험 결과는 자동으로 합산하지 않습니다. 이전 검증 경과는 [최종 검증 보고서](final-validation.md), 사용 절차는 [README](../../README.md)에 있습니다.
