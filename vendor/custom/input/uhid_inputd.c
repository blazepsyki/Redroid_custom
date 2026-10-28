/*
 * Creates /dev/input/eventN nodes inside the redroid container for UHID input devices
 * created by the container's scrcpy server.
 *
 * Since Linux 4.18 uevents go only to the initial network namespace, so the container's
 * ueventd never sees a new UHID device. This daemon scans sysfs periodically and handles only
 * the scrcpy UHID devices that appear while a process in this container holds /dev/uhid open.
 * Host physical keyboards and other input devices are never exposed.
 */
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/sysmacros.h>
#include <time.h>
#include <unistd.h>

#ifdef __ANDROID__
#include <android/log.h>
#endif

#define SYSFS_INPUT "/sys/class/input"
#define DEV_INPUT "/dev/input"
#define UHID_PATH "/devices/virtual/misc/uhid/"
#define MAX_EVENTS 512
#define INPUT_MAJOR 13
#define AID_INPUT 1004
#define AID_UHID 3011

static const char *const kNames[] = {"scrcpy", NULL};

static int known[MAX_EVENTS];   /* events already seen in sysfs: 1 */
static int owned[MAX_EVENTS];   /* events this daemon created a node for: 1 */

/* Logs go to logcat. Writing to /dev/kmsg in the container would also land in the host kernel log. */
static void logf_(const char *format, ...) {
    char line[512];
    va_list args;
    va_start(args, format);
    vsnprintf(line, sizeof(line), format, args);
    va_end(args);
#ifdef __ANDROID__
    __android_log_write(ANDROID_LOG_INFO, "custom_uhid_inputd", line);
#else
    fprintf(stderr, "custom_uhid_inputd: %s\n", line);
#endif
}

static int read_line(const char *path, char *buffer, size_t size) {
    int fd = open(path, O_RDONLY | O_CLOEXEC);
    if (fd < 0) return -1;
    ssize_t length = read(fd, buffer, size - 1);
    close(fd);
    if (length <= 0) return -1;
    buffer[length] = '\0';
    buffer[strcspn(buffer, "\n")] = '\0';
    return 0;
}

/* Whether a process in this container's PID namespace holds /dev/uhid open. */
static int local_uhid_holder(void) {
    DIR *proc = opendir("/proc");
    if (!proc) return 0;
    struct dirent *entry;
    int found = 0;
    while (!found && (entry = readdir(proc))) {
        if (entry->d_name[0] < '1' || entry->d_name[0] > '9') continue;
        char path[300];
        snprintf(path, sizeof(path), "/proc/%s/fd", entry->d_name);
        DIR *fds = opendir(path);
        if (!fds) continue;
        struct dirent *fd;
        while ((fd = readdir(fds))) {
            char link[600], target[64];
            snprintf(link, sizeof(link), "%s/%s", path, fd->d_name);
            ssize_t length = readlink(link, target, sizeof(target) - 1);
            if (length == 9 && memcmp(target, "/dev/uhid", 9) == 0) {
                found = 1;
                break;
            }
        }
        closedir(fds);
    }
    closedir(proc);
    return found;
}

/* Returns the device number if eventN is a scrcpy UHID device, otherwise 0. */
static dev_t scrcpy_uhid_device(int number) {
    char path[PATH_MAX], resolved[PATH_MAX], value[128];
    snprintf(path, sizeof(path), SYSFS_INPUT "/event%d/device", number);
    if (!realpath(path, resolved) || !strstr(resolved, UHID_PATH)) return 0;
    snprintf(path, sizeof(path), SYSFS_INPUT "/event%d/device/name", number);
    if (read_line(path, value, sizeof(value))) return 0;
    int matched = 0;
    for (int i = 0; kNames[i]; i++) matched |= strcmp(value, kNames[i]) == 0;
    if (!matched) return 0;
    snprintf(path, sizeof(path), SYSFS_INPUT "/event%d/dev", number);
    unsigned major_number, minor_number;
    if (read_line(path, value, sizeof(value)) || sscanf(value, "%u:%u", &major_number, &minor_number) != 2
            || major_number != INPUT_MAJOR) {
        return 0;
    }
    return makedev(major_number, minor_number);
}

static void remove_node(int number, const char *reason) {
    char node[64];
    snprintf(node, sizeof(node), DEV_INPUT "/event%d", number);
    struct stat status;
    if (lstat(node, &status) == 0 && S_ISCHR(status.st_mode) && major(status.st_rdev) == INPUT_MAJOR) {
        if (unlink(node) == 0) logf_("removed %s (%s)", node, reason);
    }
    owned[number] = 0;
}

static void create_node(int number, dev_t device) {
    char node[64];
    snprintf(node, sizeof(node), DEV_INPUT "/event%d", number);
    struct stat status;
    if (lstat(node, &status) == 0) {
        if (S_ISCHR(status.st_mode) && status.st_rdev == device) {
            owned[number] = 1;
            return;
        }
        if (!S_ISCHR(status.st_mode) || major(status.st_rdev) != INPUT_MAJOR || unlink(node)) {
            logf_("refusing to replace %s", node);
            return;
        }
    }
    /* Create with final mode and group (service group input) before EventHub sees IN_CREATE. */
    mode_t previous = umask(0);
    int result = mknod(node, S_IFCHR | 0660, device);
    umask(previous);
    if (result) {
        logf_("mknod %s failed: %s", node, strerror(errno));
        return;
    }
    owned[number] = 1;
    logf_("created %s (%u:%u)", node, major(device), minor(device));
}

static void scan(void) {
    int present[MAX_EVENTS] = {0};
    DIR *directory = opendir(SYSFS_INPUT);
    if (!directory) return;
    struct dirent *entry;
    int holder = -1;
    while ((entry = readdir(directory))) {
        int number;
        char extra;
        if (sscanf(entry->d_name, "event%d%c", &number, &extra) != 1 || number < 0 || number >= MAX_EVENTS) continue;
        present[number] = 1;
        dev_t device = scrcpy_uhid_device(number);
        if (!device) {
            if (owned[number]) remove_node(number, "no longer a scrcpy UHID device");
            known[number] = 1;
            continue;
        }
        if (owned[number]) {
            create_node(number, device);
            continue;
        }
        if (!known[number]) {
            if (holder < 0) holder = local_uhid_holder();
            if (holder) {
                create_node(number, device);
            } else {
                logf_("ignored event%d: no process in this container holds /dev/uhid", number);
            }
        }
        known[number] = 1;
    }
    closedir(directory);
    for (int number = 0; number < MAX_EVENTS; number++) {
        if (!present[number]) {
            known[number] = 0;
            if (owned[number]) remove_node(number, "device removed");
        }
    }
}

/*
 * Docker copies host device nodes with the host's permissions (root:root 0600). ueventd does no coldboot
 * inside the container, so the /dev/uhid and /dev/uinput permissions from ueventd.rc are applied here.
 * This only changes the container's own /dev (tmpfs) node, not the host's.
 */
static void fix_uhid_permissions(void) {
    static const char *const kNodes[] = {"/dev/uhid", "/dev/uinput", NULL};
    for (int i = 0; kNodes[i]; i++) {
        struct stat status;
        if (lstat(kNodes[i], &status) || !S_ISCHR(status.st_mode) || major(status.st_rdev) != 10) continue;
        if (status.st_uid == AID_UHID && status.st_gid == AID_UHID && (status.st_mode & 07777) == 0660) continue;
        /* chmod first, while still the owner (root), then chown. */
        if (chmod(kNodes[i], 0660) || chown(kNodes[i], AID_UHID, AID_UHID)) {
            logf_("cannot set uhid:uhid 0660 on %s: %s", kNodes[i], strerror(errno));
        } else {
            logf_("set %s to uhid:uhid 0660", kNodes[i]);
        }
    }
}

/* Delete stale nodes left by an earlier daemon or a manual connection. */
static void remove_stale_nodes(void) {
    DIR *directory = opendir(DEV_INPUT);
    if (!directory) return;
    struct dirent *entry;
    while ((entry = readdir(directory))) {
        int number;
        char extra;
        if (sscanf(entry->d_name, "event%d%c", &number, &extra) != 1 || number < 0 || number >= MAX_EVENTS) continue;
        dev_t device = scrcpy_uhid_device(number);
        char node[64];
        struct stat status;
        snprintf(node, sizeof(node), DEV_INPUT "/event%d", number);
        if (lstat(node, &status) == 0 && device && status.st_rdev == device && local_uhid_holder()) {
            owned[number] = 1;
        } else {
            remove_node(number, "stale at startup");
        }
    }
    closedir(directory);
}

int main(void) {
    /* The init service sets group input. When run by hand as root, match the group too. */
    if (getegid() != AID_INPUT && geteuid() == 0 && setegid(AID_INPUT)) {
        logf_("setegid input failed: %s", strerror(errno));
        return 1;
    }
    if (mkdir(DEV_INPUT, 0755) && errno != EEXIST) {
        logf_("cannot create " DEV_INPUT ": %s", strerror(errno));
        return 1;
    }
    fix_uhid_permissions();
    remove_stale_nodes();
    scan();
    logf_("watching scrcpy UHID devices");
    const struct timespec interval = {0, 300 * 1000 * 1000};
    for (;;) {
        nanosleep(&interval, NULL);
        scan();
    }
}
