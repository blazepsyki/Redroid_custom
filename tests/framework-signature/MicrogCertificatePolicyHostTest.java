package com.android.server.pm;

import java.nio.file.Files;
import java.nio.file.Path;
import java.security.cert.CertificateFactory;
import java.io.ByteArrayInputStream;
import java.util.Locale;

public final class MicrogCertificatePolicyHostTest {
    private static int checks;
    private static void check(boolean value, String name) {
        if (!value) throw new AssertionError(name);
        ++checks;
    }

    public static void main(String[] args) throws Exception {
        byte[] official = Files.readAllBytes(Path.of(args[0]));
        byte[] compatible = Files.readAllBytes(Path.of(args[1]));
        CertificateFactory factory = CertificateFactory.getInstance("X.509");
        factory.generateCertificate(new ByteArrayInputStream(official));
        factory.generateCertificate(new ByteArrayInputStream(compatible));
        String fake = MicrogCertificatePolicy.FAKE_CERT_HEX;
        byte[][] single = {official};
        check(MicrogCertificatePolicy.allows("com.google.android.gms", single, fake), "services");
        check(MicrogCertificatePolicy.allows("com.android.vending", single, fake), "companion");
        check(MicrogCertificatePolicy.allows("com.google.android.gms", single,
                fake.toUpperCase(Locale.ROOT)), "hex-case");
        check(!MicrogCertificatePolicy.allows("com.example.app", single, fake), "package");
        check(!MicrogCertificatePolicy.allows(null, single, fake), "null-package");
        byte[] wrong = official.clone();
        wrong[wrong.length - 1] ^= 1;
        check(!MicrogCertificatePolicy.allows("com.google.android.gms", new byte[][]{wrong}, fake), "wrong-cert");
        check(!MicrogCertificatePolicy.allows("com.android.vending", new byte[][]{compatible}, fake), "google-cert");
        check(!MicrogCertificatePolicy.allows("com.google.android.gms", new byte[][]{official, wrong}, fake), "multiple");
        check(!MicrogCertificatePolicy.allows("com.google.android.gms", null, fake), "null-signers");
        check(!MicrogCertificatePolicy.allows("com.google.android.gms", new byte[][]{null}, fake), "null-cert");
        check(!MicrogCertificatePolicy.allows("com.google.android.gms", single, null), "null-metadata");
        check(!MicrogCertificatePolicy.allows("com.google.android.gms", single, fake.substring(2)), "truncated");
        check(!MicrogCertificatePolicy.allows("com.google.android.gms", single, "not-hex"), "malformed");
        check(!MicrogCertificatePolicy.allows("com.google.android.gms.extra", single, fake), "suffix");
        System.out.println("microG certificate policy: " + checks + " checks passed");
    }
}
