.PHONY: help preflight init sync apply-redroid apply-custom configure build custom-modules custom-build package custom-package custom-sbom runtime-preflight deploy-test verify collect-report test microg-host-test

help:
	@echo '대상: preflight, sync, apply-redroid, apply-custom, custom-build, custom-package, custom-sbom, verify(NAS), collect-report, test (Linux/WSL에서 실행)'

preflight:
	bash scripts/00-preflight.sh

init:
	bash scripts/01-sync.sh --init-only

sync:
	bash scripts/01-sync.sh

apply-redroid:
	bash scripts/02-apply-redroid.sh

apply-custom:
	bash scripts/03-apply-custom.sh

configure:
	bash scripts/06-build.sh --configure-only

build:
	bash scripts/06-build.sh

custom-modules:
	bash scripts/06-build.sh --custom --modules

custom-build:
	bash scripts/06-build.sh --custom

package:
	bash scripts/07-package.sh

custom-package:
	bash scripts/07-package.sh --custom

custom-sbom:
	bash scripts/06-build.sh --custom --sbom

verify:
	bash scripts/09-verify.sh --profile arm64

collect-report:
	@test -n "$(NAS_DIR)" || { echo 'NAS_DIR에 현재 이미지의 NAS 결과 폴더를 지정해 주세요.'; exit 1; }
	bash scripts/10-collect-report.sh --nas-dir "$(NAS_DIR)" --force

microg-host-test:
	bash scripts/builder.sh bash /control-scripts/check-microg-host.sh

runtime-preflight:
	bash scripts/08-deploy-test.sh --preflight-only

deploy-test:
	bash scripts/08-deploy-test.sh

test:
	python3 -m unittest discover -s tests -v
