#!/usr/bin/env bash
set -Eeuo pipefail

[[ $# -eq 4 ]] || { echo '빌드 인자가 잘못되었습니다.' >&2; exit 2; }

# AOSP envsetup functions expect unset variables; keep that behavior in a child shell.
exec bash -eo pipefail -s -- "$@" <<'AOSP_BUILD'
lunch_target=$1
build_jobs=$2
build_mode=$3
run_id=$4

if [[ $build_mode == custom-* ]]; then
    feature_environment=$(python3 /control-scripts/print_features.py /control/config/features.json)
    while IFS='=' read -r key value; do export "$key=$value"; done <<< "$feature_environment"
fi

source build/envsetup.sh
lunch "$lunch_target"

metadata_dir="out/custom-redroid/$run_id"
mkdir -p "$metadata_dir"
printf '%s\n' "$TARGET_RELEASE" > "$metadata_dir/target-release.txt"
build/soong/soong_ui.bash --dumpvars-mode \
    --vars='TARGET_PRODUCT TARGET_BUILD_VARIANT PLATFORM_VERSION PLATFORM_SDK_VERSION BUILD_ID PRODUCT_OUT TARGET_CPU_ABI TARGET_CPU_ABI_LIST WITH_GRALLOC_FIX WITH_MICROG_SIGNATURE_SPOOFING WITH_ARM64_TRANSLATION' \
    > "$metadata_dir/target-vars.txt"

if [[ $build_mode == build || $build_mode == custom-build ]]; then
    m -j"$build_jobs"
elif [[ $build_mode == custom-modules ]]; then
    modules=()
    [[ $WITH_GRALLOC_FIX != true ]] || modules+=(gralloc.gbm)
    [[ $WITH_VALIDATION_TOOLS != true ]] || modules+=(custom_gralloc_format_test)
    [[ $WITH_MICROG_SIGNATURE_SPOOFING != true ]] || modules+=(services)
    if [[ $WITH_ARM64_TRANSLATION == true ]]; then
        mapfile -t bridge_modules < <(sed -n 's/^    \(custom_nb_[A-Za-z0-9_]*\) \\$/\1/p' vendor/custom/nativebridge/nativebridge.mk)
        ((${#bridge_modules[@]})) || { echo 'Native Bridge 모듈 목록이 비어 있습니다.' >&2; exit 2; }
        modules+=("${bridge_modules[@]}" libberberis_exec_region native_bridge_guest_linker.native_bridge
                  native_bridge_guest_app_process.native_bridge)
    fi
    if [[ $WITH_MICROG == true ]]; then
        mapfile -t microg_modules < <(sed -n 's/^    \([A-Za-z0-9_]*\) \\$/\1/p' vendor/custom/microg/microg.mk)
        ((${#microg_modules[@]})) || { echo 'microG 모듈 목록이 비어 있습니다.' >&2; exit 2; }
        modules+=("${microg_modules[@]}")
    fi
    ((${#modules[@]})) || { echo '빌드할 custom 모듈이 없습니다.' >&2; exit 2; }
    m -j"$build_jobs" "${modules[@]}"
elif [[ $build_mode == custom-sbom ]]; then
    m -j"$build_jobs" sbom
elif [[ $build_mode != configure && $build_mode != custom-configure ]]; then
    echo '지원하지 않는 빌드 모드입니다.' >&2
    exit 2
fi
AOSP_BUILD
