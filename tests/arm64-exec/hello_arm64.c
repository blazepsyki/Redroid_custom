// binfmt_misc 직접 실행 시험용 aarch64 정적 실행 파일. libc 없이 write·exit syscall만 사용합니다.
static long sys3(long n, long a, long b, long c) {
    register long x8 __asm__("x8") = n;
    register long x0 __asm__("x0") = a;
    register long x1 __asm__("x1") = b;
    register long x2 __asm__("x2") = c;
    __asm__ volatile("svc #0" : "+r"(x0) : "r"(x8), "r"(x1), "r"(x2) : "memory");
    return x0;
}

void _start(void) {
    static const char message[] = "custom-redroid arm64 direct exec ok\n";
    sys3(64, 1, (long)message, sizeof(message) - 1);  // write
    sys3(93, 0, 0, 0);                                 // exit
    for (;;) {}
}
