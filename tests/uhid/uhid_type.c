// Test UHID keyboard that behaves like scrcpy's --keyboard=uhid.
// Uses the same device name ("scrcpy") and the same HID keyboard descriptor, then sends
// the key sequence from the arguments as HID reports.
//   uhid_type WAIT_MS TOKEN...   TOKEN = [MODIFIER:]USAGE (hex) | sleep=MS
//   e.g. uhid_type 3000 0x0a 0x0e 0x16 sleep=500 0x2c   (MODIFIER 0x02 = left Shift)
#include <fcntl.h>
#include <linux/input.h>
#include <linux/uhid.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static const unsigned char kDescriptor[] = {
    0x05, 0x01, 0x09, 0x06, 0xa1, 0x01, 0x05, 0x07, 0x19, 0xe0, 0x29, 0xe7,
    0x15, 0x00, 0x25, 0x01, 0x75, 0x01, 0x95, 0x08, 0x81, 0x02,
    0x95, 0x01, 0x75, 0x08, 0x81, 0x01, 0x95, 0x06, 0x75, 0x08,
    0x15, 0x00, 0x25, 0x65, 0x05, 0x07, 0x19, 0x00, 0x29, 0x65, 0x81, 0x00, 0xc0,
};

static int send_report(int fd, unsigned char modifiers, unsigned char usage) {
    struct uhid_event event;
    memset(&event, 0, sizeof(event));
    event.type = UHID_INPUT2;
    event.u.input2.size = 8;
    event.u.input2.data[0] = modifiers;
    event.u.input2.data[2] = usage;
    return write(fd, &event, sizeof(event)) == sizeof(event) ? 0 : -1;
}

int main(int argc, char **argv) {
    if (argc < 2) {
        fprintf(stderr, "usage: %s WAIT_MS [MODIFIER:]USAGE|sleep=MS ...\n", argv[0]);
        return 2;
    }
    int fd = open("/dev/uhid", O_RDWR | O_CLOEXEC);
    if (fd < 0) { perror("open /dev/uhid"); return 1; }
    struct uhid_event event;
    memset(&event, 0, sizeof(event));
    event.type = UHID_CREATE2;
    strcpy((char *) event.u.create2.name, "scrcpy");
    event.u.create2.rd_size = sizeof(kDescriptor);
    event.u.create2.bus = BUS_VIRTUAL;
    event.u.create2.vendor = 0x18d1;
    event.u.create2.product = 0x0001;
    memcpy(event.u.create2.rd_data, kDescriptor, sizeof(kDescriptor));
    if (write(fd, &event, sizeof(event)) != sizeof(event)) { perror("UHID_CREATE2"); return 1; }
    printf("created\n");
    fflush(stdout);
    usleep(atoi(argv[1]) * 1000);
    for (int i = 2; i < argc; i++) {
        if (strncmp(argv[i], "sleep=", 6) == 0) {
            usleep(atoi(argv[i] + 6) * 1000);
            continue;
        }
        unsigned modifiers = 0, usage;
        char *colon = strchr(argv[i], ':');
        if (colon) {
            modifiers = strtoul(argv[i], NULL, 16);
            usage = strtoul(colon + 1, NULL, 16);
        } else {
            usage = strtoul(argv[i], NULL, 16);
        }
        if (modifiers && send_report(fd, modifiers, 0)) { perror("report"); return 1; }
        usleep(30000);
        if (send_report(fd, modifiers, usage) || (usleep(40000), send_report(fd, modifiers, 0))
                || (modifiers && (usleep(30000), send_report(fd, 0, 0)))) {
            perror("report");
            return 1;
        }
        usleep(120000);
    }
    sleep(2);
    memset(&event, 0, sizeof(event));
    event.type = UHID_DESTROY;
    if (write(fd, &event, sizeof(event)) != sizeof(event)) perror("UHID_DESTROY");
    close(fd);
    printf("destroyed\n");
    return 0;
}
