#!/usr/bin/env bash
set -Eeuo pipefail

test_dir=$(mktemp -d /tmp/custom-microg-test.XXXXXX)
trap 'rm -f -- "$test_dir"/com/android/server/pm/*.class; rmdir -- "$test_dir"/com/android/server/pm "$test_dir"/com/android/server "$test_dir"/com/android "$test_dir"/com "$test_dir"' EXIT
jdk_dir=/src/prebuilts/jdk/jdk21/linux-x86/bin
"$jdk_dir/javac" --release 17 -d "$test_dir" \
    /control/vendor/custom/framework/MicrogCertificatePolicy.java \
    /control/tests/framework-signature/MicrogCertificatePolicyHostTest.java
"$jdk_dir/java" -cp "$test_dir" com.android.server.pm.MicrogCertificatePolicyHostTest \
    /control/tests/framework-signature/microg-cert.der \
    /control/tests/framework-signature/google-compat-cert.der
