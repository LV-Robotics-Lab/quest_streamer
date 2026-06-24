cmake_minimum_required(VERSION 3.22.1)

project(questtelemetry LANGUAGES C CXX)

find_package(OpenXR REQUIRED CONFIG)

add_library(native_app_glue STATIC
    ${ANDROID_NDK}/sources/android/native_app_glue/android_native_app_glue.c
)
target_include_directories(native_app_glue PUBLIC
    ${ANDROID_NDK}/sources/android/native_app_glue
)

add_library(questtelemetry MODULE
    quest_telemetry.cpp
)

target_compile_features(questtelemetry PRIVATE cxx_std_17)
target_compile_definitions(questtelemetry PRIVATE
    XR_USE_PLATFORM_ANDROID
    XR_USE_GRAPHICS_API_OPENGL_ES
)

target_link_libraries(questtelemetry PRIVATE
    OpenXR::openxr_loader
    native_app_glue
    android
    EGL
    GLESv3
    log
)

set_target_properties(questtelemetry PROPERTIES
    LINK_FLAGS "-u ANativeActivity_onCreate"
)
