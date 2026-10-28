#define _GNU_SOURCE
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <sys/xattr.h>
#include <unistd.h>

static void print_xattr(const char *path, const char *name) {
    unsigned char value[4096];
    ssize_t length = lgetxattr(path, name, value, sizeof(value));
    if (length < 0) {
        if (errno == ENODATA || errno == ENOTSUP) {
            fputs("-", stdout);
            return;
        }
        perror(path);
        exit(1);
    }
    for (ssize_t i = 0; i < length; i++) printf("%02x", value[i]);
}

int main(int argc, char **argv) {
    if (argc < 2) return 2;
    for (int i = 1; i < argc; i++) {
        struct stat info;
        char target[4096];
        if (lstat(argv[i], &info) != 0) { perror(argv[i]); return 1; }
        printf("%s\t%u\t%u\t%o\t", argv[i], (unsigned)info.st_uid, (unsigned)info.st_gid,
               (unsigned)info.st_mode);
        if (S_ISLNK(info.st_mode)) {
            ssize_t length = readlink(argv[i], target, sizeof(target) - 1);
            if (length < 0) { perror(argv[i]); return 1; }
            target[length] = '\0';
            fputs(target, stdout);
        } else fputs("-", stdout);
        putchar('\t');
        print_xattr(argv[i], "security.selinux");
        putchar('\t');
        print_xattr(argv[i], "security.capability");
        putchar('\n');
    }
    return 0;
}
