/*
 * Copyright (C) 2021 The Android Open Source Project
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 * http://www.apache.org/licenses/LICENSE-2.0
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 */
package com.android.server.pm;

import android.content.pm.PackageInfo;
import android.content.pm.PackageManager;
import android.content.pm.Signature;
import android.content.pm.SigningDetails;
import android.content.pm.SigningInfo;
import android.os.Build;
import android.os.Bundle;
import android.os.SystemProperties;
import android.util.Slog;

import com.android.server.pm.pkg.AndroidPackage;

import java.security.cert.CertificateException;

final class MicrogSignaturePolicy {
    private static final String TAG = "MicrogSignaturePolicy";

    private MicrogSignaturePolicy() {}

    static void applyIfAllowed(AndroidPackage pkg, PackageInfo info, long flags) {
        apply(pkg, info, flags, Build.IS_DEBUGGABLE
                && SystemProperties.getBoolean("ro.custom.microg_spoof", false));
    }

    // This changes the returned view only; installation SigningDetails are never mutated.
    static void apply(AndroidPackage pkg, PackageInfo info, long flags, boolean enabled) {
        long requested = flags & (PackageManager.GET_SIGNATURES
                | PackageManager.GET_SIGNING_CERTIFICATES);
        if (!enabled || pkg == null || info == null || requested == 0) return;
        Signature[] actual = pkg.getSigningDetails().getSignatures();
        if (actual == null || actual.length != 1 || actual[0] == null) return;
        Bundle metadata = pkg.getMetaData();
        String fake = metadata == null ? null : metadata.getString("fake-signature");
        if (!MicrogCertificatePolicy.allows(pkg.getPackageName(),
                new byte[][]{actual[0].toByteArray()}, fake)) return;

        Signature[] compatible = {new Signature(MicrogCertificatePolicy.FAKE_CERT_HEX)};
        SigningInfo signingInfo = null;
        if ((flags & PackageManager.GET_SIGNING_CERTIFICATES) != 0) {
            try {
                signingInfo = new SigningInfo(new SigningDetails(compatible,
                        SigningDetails.SignatureSchemeVersion.SIGNING_BLOCK_V3,
                        SigningDetails.toSigningKeys(compatible), null));
            } catch (CertificateException e) {
                Slog.e(TAG, "Pinned compatibility certificate is invalid", e);
                return;
            }
        }
        if ((flags & PackageManager.GET_SIGNATURES) != 0) info.signatures = compatible;
        if (signingInfo != null) info.signingInfo = signingInfo;
    }
}
