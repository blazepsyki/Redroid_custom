LOCAL_PATH := $(call my-dir)

ifeq ($(WITH_GRALLOC_FIX),true)
include $(CLEAR_VARS)
LOCAL_MODULE := gralloc.gbm
LOCAL_MODULE_RELATIVE_PATH := hw
LOCAL_PROPRIETARY_MODULE := true
LOCAL_SRC_FILES := source/gralloc.cpp source/gralloc_gbm.cpp
LOCAL_C_INCLUDES := external/libdrm external/libdrm/include/drm external/mesa3d/src/gbm/main
LOCAL_HEADER_LIBRARIES := libhardware_headers libsystem_headers
LOCAL_SHARED_LIBRARIES := libgbm.so.1 liblog libcutils
include $(BUILD_SHARED_LIBRARY)
endif

ifeq ($(WITH_VALIDATION_TOOLS),true)
include $(CLEAR_VARS)
LOCAL_MODULE := custom_gralloc_format_test
LOCAL_PROPRIETARY_MODULE := true
LOCAL_SRC_FILES := gralloc_format_test.cpp
LOCAL_HEADER_LIBRARIES := libhardware_headers libsystem_headers
LOCAL_SHARED_LIBRARIES := libhardware liblog
include $(BUILD_EXECUTABLE)
endif
