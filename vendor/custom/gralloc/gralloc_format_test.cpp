#include <hardware/gralloc.h>
#include <system/graphics.h>
#include <errno.h>
#include <stdio.h>
#include <sys/wait.h>
#include <unistd.h>

struct FormatCase {
    const char* name;
    int format;
    int width;
    int height;
    int usage;
    bool mustReject;
    bool mustAllocate;
};

static int runCase(const FormatCase& test) {
    const hw_module_t* module = nullptr;
    int result = hw_get_module(GRALLOC_HARDWARE_MODULE_ID, &module);
    if (result != 0 || module == nullptr) return 10;
    alloc_device_t* device = nullptr;
    result = gralloc_open(module, &device);
    if (result != 0 || device == nullptr) return 11;
    buffer_handle_t handle = nullptr;
    int stride = 0;
    result = device->alloc(device, test.width, test.height, test.format, test.usage,
                           &handle, &stride);
    bool passed;
    if (test.mustReject) {
        passed = result == -EINVAL && handle == nullptr;
    } else if (test.mustAllocate) {
        passed = result == 0 && handle != nullptr && stride >= test.width;
    } else {
        passed = (result == 0 && handle != nullptr && stride >= test.width)
                || (result < 0 && handle == nullptr);
    }
    printf("{\"case\":\"%s\",\"format\":%d,\"result\":%d,\"stride\":%d,\"pass\":%s}\n",
           test.name, test.format, result, stride, passed ? "true" : "false");
    if (handle != nullptr) device->free(device, handle);
    gralloc_close(device);
    fflush(stdout);
    return passed ? 0 : 12;
}

int main() {
    const int usage = GRALLOC_USAGE_HW_RENDER | GRALLOC_USAGE_HW_TEXTURE;
    const FormatCase tests[] = {
        {"rgba8888", HAL_PIXEL_FORMAT_RGBA_8888, 64, 64, usage, false, true},
        {"rgba1010102", HAL_PIXEL_FORMAT_RGBA_1010102, 64, 64, usage, false, false},
        {"rgba_fp16", HAL_PIXEL_FORMAT_RGBA_FP16, 64, 64, usage, false, false},
        {"p010_observed_trigger", 0x36, 4, 4, 0x932, true, false},
        {"unknown_format", 0x7ffffffe, 4, 4, 0x932, true, false},
    };
    int failures = 0;
    for (const auto& test : tests) {
        pid_t child = fork();
        if (child < 0) return 20;
        if (child == 0) _exit(runCase(test));
        int status = 0;
        if (waitpid(child, &status, 0) != child) return 21;
        if (!WIFEXITED(status) || WEXITSTATUS(status) != 0) {
            printf("{\"case\":\"%s\",\"failed\":true,\"signal\":%d,\"exit\":%d}\n",
                   test.name, WIFSIGNALED(status) ? WTERMSIG(status) : 0,
                   WIFEXITED(status) ? WEXITSTATUS(status) : -1);
            ++failures;
        }
        fflush(stdout);
    }
    return failures == 0 ? 0 : 1;
}
