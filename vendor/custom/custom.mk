include vendor/custom/features.mk

PRODUCT_SYSTEM_PROPERTIES += ro.custom.microg_spoof=$(WITH_MICROG_SIGNATURE_SPOOFING)

ifeq ($(WITH_ARM64_TRANSLATION),true)
include vendor/custom/nativebridge/nativebridge.mk
else
PRODUCT_SYSTEM_PROPERTIES += ro.dalvik.vm.native.bridge=0
endif

ifeq ($(WITH_MICROG),true)
include vendor/custom/microg/microg.mk
endif

ifeq ($(WITH_VALIDATION_TOOLS),true)
PRODUCT_PACKAGES += custom_gralloc_format_test
endif
