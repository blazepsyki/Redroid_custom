package com.android.server.pm;

import static org.junit.Assert.*;
import static org.mockito.Mockito.*;

import android.content.pm.PackageInfo;
import android.content.pm.PackageManager;
import android.content.pm.Signature;
import android.content.pm.SigningDetails;
import android.os.Bundle;
import com.android.server.pm.pkg.AndroidPackage;
import org.junit.Test;

public class MicrogSignaturePolicyTest {
    private AndroidPackage pkg(String name, String certificate, String metadataSignature) {
        AndroidPackage pkg = mock(AndroidPackage.class);
        when(pkg.getPackageName()).thenReturn(name);
        when(pkg.getSigningDetails()).thenReturn(new SigningDetails(
                new Signature[]{new Signature(certificate)},
                SigningDetails.SignatureSchemeVersion.SIGNING_BLOCK_V3, null, null));
        Bundle metadata = new Bundle();
        metadata.putString("fake-signature", metadataSignature);
        when(pkg.getMetaData()).thenReturn(metadata);
        return pkg;
    }

    @Test public void approvedPackagesReceiveRequestedViewsOnly() {
        for (String name : new String[]{"com.google.android.gms", "com.android.vending"}) {
            AndroidPackage pkg = pkg(name, MicrogCertificatePolicy.REAL_CERT_HEX,
                    MicrogCertificatePolicy.FAKE_CERT_HEX);
            PackageInfo legacy = new PackageInfo();
            MicrogSignaturePolicy.apply(pkg, legacy, PackageManager.GET_SIGNATURES, true);
            assertEquals(new Signature(MicrogCertificatePolicy.FAKE_CERT_HEX), legacy.signatures[0]);
            assertNull(legacy.signingInfo);
            PackageInfo modern = new PackageInfo();
            MicrogSignaturePolicy.apply(pkg, modern, PackageManager.GET_SIGNING_CERTIFICATES, true);
            assertNull(modern.signatures);
            assertEquals(new Signature(MicrogCertificatePolicy.FAKE_CERT_HEX),
                    modern.signingInfo.getApkContentsSigners()[0]);
            assertEquals(new Signature(MicrogCertificatePolicy.REAL_CERT_HEX),
                    pkg.getSigningDetails().getSignatures()[0]);
        }
    }

    @Test public void unrelatedOrUnofficialPackagesAreUnchanged() {
        for (AndroidPackage pkg : new AndroidPackage[]{
                pkg("com.example.app", MicrogCertificatePolicy.REAL_CERT_HEX, MicrogCertificatePolicy.FAKE_CERT_HEX),
                pkg("com.google.android.gms", MicrogCertificatePolicy.FAKE_CERT_HEX, MicrogCertificatePolicy.FAKE_CERT_HEX),
                pkg("com.android.vending", MicrogCertificatePolicy.REAL_CERT_HEX, "invalid")}) {
            PackageInfo info = new PackageInfo();
            MicrogSignaturePolicy.apply(pkg, info,
                    PackageManager.GET_SIGNATURES | PackageManager.GET_SIGNING_CERTIFICATES, true);
            assertNull(info.signatures);
            assertNull(info.signingInfo);
        }
    }

    @Test public void disabledOrUnrequestedQueriesAreUnchanged() {
        AndroidPackage pkg = pkg("com.google.android.gms", MicrogCertificatePolicy.REAL_CERT_HEX,
                MicrogCertificatePolicy.FAKE_CERT_HEX);
        PackageInfo info = new PackageInfo();
        MicrogSignaturePolicy.apply(pkg, info, PackageManager.GET_SIGNATURES, false);
        MicrogSignaturePolicy.apply(pkg, info, 0, true);
        assertNull(info.signatures);
        assertNull(info.signingInfo);
    }
}
