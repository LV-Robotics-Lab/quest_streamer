#include <EGL/egl.h>
#include <GLES3/gl3.h>
#include <android/log.h>
#include <android_native_app_glue.h>
#include <jni.h>
#include <openxr/openxr.h>
#include <openxr/openxr_platform.h>

#include <arpa/inet.h>
#include <errno.h>
#include <fcntl.h>
#include <math.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <string.h>
#include <sys/socket.h>
#include <time.h>
#include <unistd.h>

#include <array>
#include <cstdint>
#include <sstream>
#include <string>
#include <vector>

#define LOG_TAG "QuestTelemetry"
#define LOGI(...) __android_log_print(ANDROID_LOG_INFO, LOG_TAG, __VA_ARGS__)
#define LOGW(...) __android_log_print(ANDROID_LOG_WARN, LOG_TAG, __VA_ARGS__)
#define LOGE(...) __android_log_print(ANDROID_LOG_ERROR, LOG_TAG, __VA_ARGS__)

namespace {

constexpr const char* kControllerLogTag = "wE9ryARX";
constexpr const char* kControllerSchema = "nero.quest_controller.raw.v1";
constexpr const char* kTouchPlusExtension = "XR_META_touch_controller_plus";
constexpr int kControllerPort = 9200;
constexpr const char* kHandHost = "127.0.0.1";
constexpr int kHandPort = 8000;
constexpr int64_t kReconnectDelayNs = 1000LL * 1000LL * 1000LL;

int64_t monotonicNs() {
    timespec ts{};
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return static_cast<int64_t>(ts.tv_sec) * 1000000000LL + ts.tv_nsec;
}

bool xrOk(XrResult result, const char* expr, int line) {
    if (XR_SUCCEEDED(result)) {
        return true;
    }
    LOGE("OpenXR call failed at line %d: %s -> %d", line, expr, result);
    return false;
}

#define XR_CHECK(x) xrOk((x), #x, __LINE__)

struct EglState {
    EGLDisplay display = EGL_NO_DISPLAY;
    EGLConfig config = nullptr;
    EGLContext context = EGL_NO_CONTEXT;
    EGLSurface surface = EGL_NO_SURFACE;

    bool init() {
        display = eglGetDisplay(EGL_DEFAULT_DISPLAY);
        if (display == EGL_NO_DISPLAY || eglInitialize(display, nullptr, nullptr) == EGL_FALSE) {
            LOGE("eglInitialize failed");
            return false;
        }
        if (eglBindAPI(EGL_OPENGL_ES_API) == EGL_FALSE) {
            LOGE("eglBindAPI failed");
            return false;
        }

        const EGLint configAttribs[] = {
            EGL_RENDERABLE_TYPE, EGL_OPENGL_ES3_BIT,
            EGL_SURFACE_TYPE, EGL_PBUFFER_BIT,
            EGL_RED_SIZE, 8,
            EGL_GREEN_SIZE, 8,
            EGL_BLUE_SIZE, 8,
            EGL_ALPHA_SIZE, 8,
            EGL_DEPTH_SIZE, 24,
            EGL_STENCIL_SIZE, 8,
            EGL_NONE,
        };
        EGLint numConfigs = 0;
        if (eglChooseConfig(display, configAttribs, &config, 1, &numConfigs) == EGL_FALSE ||
            numConfigs < 1) {
            LOGE("eglChooseConfig failed");
            return false;
        }

        const EGLint contextAttribs[] = {EGL_CONTEXT_CLIENT_VERSION, 3, EGL_NONE};
        context = eglCreateContext(display, config, EGL_NO_CONTEXT, contextAttribs);
        if (context == EGL_NO_CONTEXT) {
            LOGE("eglCreateContext failed");
            return false;
        }

        const EGLint surfaceAttribs[] = {EGL_WIDTH, 16, EGL_HEIGHT, 16, EGL_NONE};
        surface = eglCreatePbufferSurface(display, config, surfaceAttribs);
        if (surface == EGL_NO_SURFACE) {
            LOGE("eglCreatePbufferSurface failed");
            return false;
        }
        if (eglMakeCurrent(display, surface, surface, context) == EGL_FALSE) {
            LOGE("eglMakeCurrent failed");
            return false;
        }
        return true;
    }

    void shutdown() {
        if (display != EGL_NO_DISPLAY) {
            eglMakeCurrent(display, EGL_NO_SURFACE, EGL_NO_SURFACE, EGL_NO_CONTEXT);
            if (surface != EGL_NO_SURFACE) {
                eglDestroySurface(display, surface);
            }
            if (context != EGL_NO_CONTEXT) {
                eglDestroyContext(display, context);
            }
            eglTerminate(display);
        }
        display = EGL_NO_DISPLAY;
        surface = EGL_NO_SURFACE;
        context = EGL_NO_CONTEXT;
        config = nullptr;
    }
};

class TcpHandSink {
public:
    ~TcpHandSink() {
        closeSocket();
    }

    void sendLine(const std::string& payload) {
        if (!ensureConnected()) {
            return;
        }
        const std::string message = payload + "\n";
        ssize_t written = ::send(fd_, message.data(), message.size(), MSG_NOSIGNAL);
        if (written < 0 || static_cast<size_t>(written) != message.size()) {
            LOGW("hand telemetry TCP send failed: %s", strerror(errno));
            closeSocket();
        }
    }

private:
    int fd_ = -1;
    int64_t nextConnectNs_ = 0;

    bool ensureConnected() {
        if (fd_ >= 0) {
            return true;
        }
        const int64_t now = monotonicNs();
        if (now < nextConnectNs_) {
            return false;
        }
        nextConnectNs_ = now + kReconnectDelayNs;

        int fd = socket(AF_INET, SOCK_STREAM, 0);
        if (fd < 0) {
            return false;
        }

        sockaddr_in addr{};
        addr.sin_family = AF_INET;
        addr.sin_port = htons(kHandPort);
        inet_pton(AF_INET, kHandHost, &addr.sin_addr);
        if (connect(fd, reinterpret_cast<sockaddr*>(&addr), sizeof(addr)) != 0) {
            close(fd);
            return false;
        }

        int one = 1;
        setsockopt(fd, IPPROTO_TCP, TCP_NODELAY, &one, sizeof(one));
        fd_ = fd;
        LOGI("hand telemetry connected to %s:%d", kHandHost, kHandPort);
        return true;
    }

    void closeSocket() {
        if (fd_ >= 0) {
            close(fd_);
            fd_ = -1;
        }
    }
};

class TcpControllerServer {
public:
    ~TcpControllerServer() {
        shutdown();
    }

    bool start() {
        if (listenFd_ >= 0) {
            return true;
        }
        int fd = socket(AF_INET, SOCK_STREAM, 0);
        if (fd < 0) {
            LOGE("controller socket failed: %s", strerror(errno));
            return false;
        }
        int one = 1;
        setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &one, sizeof(one));
        sockaddr_in addr{};
        addr.sin_family = AF_INET;
        addr.sin_port = htons(kControllerPort);
        addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
        if (bind(fd, reinterpret_cast<sockaddr*>(&addr), sizeof(addr)) != 0 ||
            ::listen(fd, 1) != 0) {
            LOGE("controller server bind/listen failed on %d: %s", kControllerPort, strerror(errno));
            close(fd);
            return false;
        }
        const int flags = fcntl(fd, F_GETFL, 0);
        fcntl(fd, F_SETFL, flags | O_NONBLOCK);
        listenFd_ = fd;
        LOGI("OpenXR controller server listening on 127.0.0.1:%d", kControllerPort);
        return true;
    }

    void sendLine(const std::string& payload) {
        acceptClient();
        if (clientFd_ < 0) {
            return;
        }
        const std::string message = payload + "\n";
        const ssize_t written = ::send(
            clientFd_, message.data(), message.size(), MSG_DONTWAIT | MSG_NOSIGNAL);
        if (written < 0 || static_cast<size_t>(written) != message.size()) {
            LOGW("controller TCP send failed or was partial: %s", strerror(errno));
            closeClient();
        }
    }

    void shutdown() {
        closeClient();
        if (listenFd_ >= 0) {
            close(listenFd_);
            listenFd_ = -1;
        }
    }

private:
    int listenFd_ = -1;
    int clientFd_ = -1;

    void acceptClient() {
        if (clientFd_ >= 0 || listenFd_ < 0) {
            return;
        }
        int fd = accept(listenFd_, nullptr, nullptr);
        if (fd < 0) {
            if (errno != EAGAIN && errno != EWOULDBLOCK) {
                LOGW("controller TCP accept failed: %s", strerror(errno));
            }
            return;
        }
        int one = 1;
        setsockopt(fd, IPPROTO_TCP, TCP_NODELAY, &one, sizeof(one));
        const int flags = fcntl(fd, F_GETFL, 0);
        fcntl(fd, F_SETFL, flags | O_NONBLOCK);
        clientFd_ = fd;
        LOGI("OpenXR controller TCP client connected");
    }

    void closeClient() {
        if (clientFd_ >= 0) {
            close(clientFd_);
            clientFd_ = -1;
        }
    }
};

std::string fixed(float value, int precision = 6) {
    std::ostringstream ss;
    ss.setf(std::ios::fixed);
    ss.precision(precision);
    ss << value;
    return ss.str();
}

void appendVec3(std::ostringstream& ss, const XrVector3f& v) {
    ss << fixed(v.x, 4) << ", " << fixed(v.y, 4) << ", " << fixed(v.z, 4);
}

void appendQuat(std::ostringstream& ss, const XrQuaternionf& q) {
    ss << fixed(q.x, 3) << ", " << fixed(q.y, 3) << ", "
       << fixed(q.z, 3) << ", " << fixed(q.w, 3);
}

void appendMatrix(std::ostringstream& ss, const XrPosef& pose) {
    float x = pose.orientation.x;
    float y = pose.orientation.y;
    float z = pose.orientation.z;
    float w = pose.orientation.w;
    const float len = sqrtf(x * x + y * y + z * z + w * w);
    if (len > 0.0f) {
        x /= len;
        y /= len;
        z /= len;
        w /= len;
    }

    const float xx = x * x;
    const float yy = y * y;
    const float zz = z * z;
    const float xy = x * y;
    const float xz = x * z;
    const float yz = y * z;
    const float wx = w * x;
    const float wy = w * y;
    const float wz = w * z;

    const float m[16] = {
        1.0f - 2.0f * (yy + zz), 2.0f * (xy - wz), 2.0f * (xz + wy), pose.position.x,
        2.0f * (xy + wz), 1.0f - 2.0f * (xx + zz), 2.0f * (yz - wx), pose.position.y,
        2.0f * (xz - wy), 2.0f * (yz + wx), 1.0f - 2.0f * (xx + yy), pose.position.z,
        0.0f, 0.0f, 0.0f, 1.0f,
    };

    for (int i = 0; i < 16; ++i) {
        if (i != 0) {
            ss << ' ';
        }
        ss << fixed(m[i]);
    }
}

void appendMatrixJson(std::ostringstream& ss, const XrPosef& pose) {
    std::ostringstream matrix;
    appendMatrix(matrix, pose);
    ss << '[';
    std::istringstream values(matrix.str());
    std::string value;
    bool first = true;
    while (values >> value) {
        if (!first) {
            ss << ',';
        }
        ss << value;
        first = false;
    }
    ss << ']';
}

void appendJsonBool(std::ostringstream& ss, bool value) {
    ss << (value ? "true" : "false");
}

bool booleanIntentExtra(android_app* app, const char* key, bool defaultValue) {
    JavaVM* vm = app->activity->vm;
    JNIEnv* env = nullptr;
    bool detach = false;
    const jint envStatus = vm->GetEnv(reinterpret_cast<void**>(&env), JNI_VERSION_1_6);
    if (envStatus == JNI_EDETACHED) {
        if (vm->AttachCurrentThread(&env, nullptr) != JNI_OK) {
            return defaultValue;
        }
        detach = true;
    } else if (envStatus != JNI_OK || env == nullptr) {
        return defaultValue;
    }

    bool result = defaultValue;
    jobject activity = app->activity->clazz;
    jclass activityClass = env->GetObjectClass(activity);
    jmethodID getIntent = env->GetMethodID(
        activityClass, "getIntent", "()Landroid/content/Intent;");
    jobject intent = getIntent == nullptr ? nullptr : env->CallObjectMethod(activity, getIntent);
    if (intent != nullptr && !env->ExceptionCheck()) {
        jclass intentClass = env->GetObjectClass(intent);
        jmethodID getBooleanExtra = env->GetMethodID(
            intentClass, "getBooleanExtra", "(Ljava/lang/String;Z)Z");
        if (getBooleanExtra != nullptr) {
            jstring extraKey = env->NewStringUTF(key);
            result = env->CallBooleanMethod(
                intent,
                getBooleanExtra,
                extraKey,
                defaultValue ? JNI_TRUE : JNI_FALSE) == JNI_TRUE;
            env->DeleteLocalRef(extraKey);
        }
        env->DeleteLocalRef(intentClass);
        env->DeleteLocalRef(intent);
    }
    if (env->ExceptionCheck()) {
        env->ExceptionClear();
        result = defaultValue;
    }
    env->DeleteLocalRef(activityClass);
    if (detach) {
        vm->DetachCurrentThread();
    }
    return result;
}

class QuestTelemetryApp {
public:
    QuestTelemetryApp(android_app* app, bool enableHandTelemetry)
        : app_(app), enableHandTelemetry_(enableHandTelemetry) {}

    bool init() {
        if (!initLoader()) {
            return false;
        }
        if (!createInstance()) {
            return false;
        }
        if (!createSystem()) {
            return false;
        }
        if (!egl_.init()) {
            return false;
        }
        if (!createSession()) {
            return false;
        }
        if (!createPassthrough()) {
            return false;
        }
        if (!controllerServer_.start()) {
            return false;
        }
        createSpaces();
        createActions();
        createHandTrackers();
        LOGI("Quest telemetry native runtime initialized");
        return true;
    }

    void shutdown() {
        controllerServer_.shutdown();
        if (session_ != XR_NULL_HANDLE) {
            if (handTrackerLeft_ != XR_NULL_HANDLE && xrDestroyHandTrackerEXT_) {
                xrDestroyHandTrackerEXT_(handTrackerLeft_);
            }
            if (handTrackerRight_ != XR_NULL_HANDLE && xrDestroyHandTrackerEXT_) {
                xrDestroyHandTrackerEXT_(handTrackerRight_);
            }
            for (XrSpace space : actionSpaces_) {
                if (space != XR_NULL_HANDLE) {
                    xrDestroySpace(space);
                }
            }
            if (localSpace_ != XR_NULL_HANDLE) {
                xrDestroySpace(localSpace_);
            }
            if (passthroughLayer_ != XR_NULL_HANDLE && xrDestroyPassthroughLayerFB_) {
                xrDestroyPassthroughLayerFB_(passthroughLayer_);
                passthroughLayer_ = XR_NULL_HANDLE;
            }
            if (passthrough_ != XR_NULL_HANDLE && xrDestroyPassthroughFB_) {
                xrDestroyPassthroughFB_(passthrough_);
                passthrough_ = XR_NULL_HANDLE;
            }
            xrDestroySession(session_);
            session_ = XR_NULL_HANDLE;
        }
        if (instance_ != XR_NULL_HANDLE) {
            xrDestroyInstance(instance_);
            instance_ = XR_NULL_HANDLE;
        }
        egl_.shutdown();
    }

    bool sessionRunning() const {
        return sessionRunning_;
    }

    void pollEvents() {
        XrEventDataBuffer event{XR_TYPE_EVENT_DATA_BUFFER};
        while (xrPollEvent(instance_, &event) == XR_SUCCESS) {
            if (event.type == XR_TYPE_EVENT_DATA_SESSION_STATE_CHANGED) {
                const auto* state =
                    reinterpret_cast<const XrEventDataSessionStateChanged*>(&event);
                sessionState_ = state->state;
                LOGI("OpenXR session state %d", sessionState_);
                if (sessionState_ == XR_SESSION_STATE_READY) {
                    XrSessionBeginInfo beginInfo{XR_TYPE_SESSION_BEGIN_INFO};
                    beginInfo.primaryViewConfigurationType =
                        XR_VIEW_CONFIGURATION_TYPE_PRIMARY_STEREO;
                    if (XR_CHECK(xrBeginSession(session_, &beginInfo))) {
                        sessionRunning_ = true;
                    }
                } else if (sessionState_ == XR_SESSION_STATE_STOPPING) {
                    sessionRunning_ = false;
                    XR_CHECK(xrEndSession(session_));
                } else if (sessionState_ == XR_SESSION_STATE_EXITING ||
                           sessionState_ == XR_SESSION_STATE_LOSS_PENDING) {
                    app_->destroyRequested = 1;
                }
            } else if (event.type == XR_TYPE_EVENT_DATA_REFERENCE_SPACE_CHANGE_PENDING) {
                const auto* change =
                    reinterpret_cast<const XrEventDataReferenceSpaceChangePending*>(&event);
                if (change->referenceSpaceType == XR_REFERENCE_SPACE_TYPE_LOCAL) {
                    localSpaceChangePending_ = true;
                    localSpaceChangeTime_ = change->changeTime;
                    LOGI(
                        "OpenXR LOCAL reference-space change pending time=%lld pose_valid=%d",
                        static_cast<long long>(change->changeTime),
                        change->poseValid == XR_TRUE ? 1 : 0);
                }
            }
            event = {XR_TYPE_EVENT_DATA_BUFFER};
        }
    }

    void frame() {
        XrFrameWaitInfo waitInfo{XR_TYPE_FRAME_WAIT_INFO};
        XrFrameState frameState{XR_TYPE_FRAME_STATE};
        if (!XR_CHECK(xrWaitFrame(session_, &waitInfo, &frameState))) {
            return;
        }
        XrFrameBeginInfo beginInfo{XR_TYPE_FRAME_BEGIN_INFO};
        XR_CHECK(xrBeginFrame(session_, &beginInfo));

        if (localSpaceChangePending_ &&
            (localSpaceChangeTime_ <= 0 ||
             frameState.predictedDisplayTime >= localSpaceChangeTime_)) {
            rotateProviderSession("local_reference_space_change");
            localSpaceChangePending_ = false;
            localSpaceChangeTime_ = 0;
        }

        // shouldRender controls composition only. Input sampling must continue
        // on every running frame so a compositor decision cannot silently
        // stall the teleoperation source.
        updateTelemetry(frameState.predictedDisplayTime);

        XrCompositionLayerPassthroughFB passthroughComposition{
            XR_TYPE_COMPOSITION_LAYER_PASSTHROUGH_FB};
        passthroughComposition.layerHandle = passthroughLayer_;
        const XrCompositionLayerBaseHeader* layers[] = {
            reinterpret_cast<const XrCompositionLayerBaseHeader*>(&passthroughComposition),
        };

        XrFrameEndInfo endInfo{XR_TYPE_FRAME_END_INFO};
        endInfo.displayTime = frameState.predictedDisplayTime;
        endInfo.environmentBlendMode = XR_ENVIRONMENT_BLEND_MODE_OPAQUE;
        endInfo.layerCount = frameState.shouldRender ? 1u : 0u;
        endInfo.layers = frameState.shouldRender ? layers : nullptr;
        XR_CHECK(xrEndFrame(session_, &endInfo));
    }

private:
    android_app* app_ = nullptr;
    EglState egl_;
    XrInstance instance_ = XR_NULL_HANDLE;
    XrSystemId systemId_ = XR_NULL_SYSTEM_ID;
    XrSession session_ = XR_NULL_HANDLE;
    XrSessionState sessionState_ = XR_SESSION_STATE_UNKNOWN;
    bool sessionRunning_ = false;
    bool handTrackingEnabled_ = false;
    bool touchPlusEnabled_ = false;
    bool enableHandTelemetry_ = false;
    int64_t providerSessionId_ = monotonicNs();
    uint64_t controllerFrameSeq_ = 0;
    bool localSpaceChangePending_ = false;
    XrTime localSpaceChangeTime_ = 0;

    XrSpace localSpace_ = XR_NULL_HANDLE;

    XrPath leftHandPath_ = XR_NULL_PATH;
    XrPath rightHandPath_ = XR_NULL_PATH;

    XrActionSet actionSet_ = XR_NULL_HANDLE;
    XrAction gripPoseAction_ = XR_NULL_HANDLE;
    XrAction triggerValueAction_ = XR_NULL_HANDLE;
    XrAction gripValueAction_ = XR_NULL_HANDLE;
    XrAction thumbstickAction_ = XR_NULL_HANDLE;
    XrAction thumbstickClickAction_ = XR_NULL_HANDLE;
    XrAction thumbrestTouchAction_ = XR_NULL_HANDLE;
    XrAction aButtonAction_ = XR_NULL_HANDLE;
    XrAction bButtonAction_ = XR_NULL_HANDLE;
    XrAction xButtonAction_ = XR_NULL_HANDLE;
    XrAction yButtonAction_ = XR_NULL_HANDLE;
    XrSpace leftGripSpace_ = XR_NULL_HANDLE;
    XrSpace rightGripSpace_ = XR_NULL_HANDLE;
    std::vector<XrSpace> actionSpaces_;

    PFN_xrGetOpenGLESGraphicsRequirementsKHR xrGetOpenGLESGraphicsRequirementsKHR_ = nullptr;
    PFN_xrCreatePassthroughFB xrCreatePassthroughFB_ = nullptr;
    PFN_xrDestroyPassthroughFB xrDestroyPassthroughFB_ = nullptr;
    PFN_xrCreatePassthroughLayerFB xrCreatePassthroughLayerFB_ = nullptr;
    PFN_xrDestroyPassthroughLayerFB xrDestroyPassthroughLayerFB_ = nullptr;
    XrPassthroughFB passthrough_ = XR_NULL_HANDLE;
    XrPassthroughLayerFB passthroughLayer_ = XR_NULL_HANDLE;
    PFN_xrCreateHandTrackerEXT xrCreateHandTrackerEXT_ = nullptr;
    PFN_xrDestroyHandTrackerEXT xrDestroyHandTrackerEXT_ = nullptr;
    PFN_xrLocateHandJointsEXT xrLocateHandJointsEXT_ = nullptr;
    XrHandTrackerEXT handTrackerLeft_ = XR_NULL_HANDLE;
    XrHandTrackerEXT handTrackerRight_ = XR_NULL_HANDLE;
    std::array<XrHandJointLocationEXT, XR_HAND_JOINT_COUNT_EXT> leftJoints_{};
    std::array<XrHandJointLocationEXT, XR_HAND_JOINT_COUNT_EXT> rightJoints_{};

    TcpHandSink handSink_;
    TcpControllerServer controllerServer_;

    bool initLoader() {
        PFN_xrInitializeLoaderKHR initializeLoader = nullptr;
        XrResult result = xrGetInstanceProcAddr(
            XR_NULL_HANDLE,
            "xrInitializeLoaderKHR",
            reinterpret_cast<PFN_xrVoidFunction*>(&initializeLoader));
        if (XR_FAILED(result) || initializeLoader == nullptr) {
            LOGE("xrInitializeLoaderKHR unavailable");
            return false;
        }
        XrLoaderInitInfoAndroidKHR loaderInitInfo{XR_TYPE_LOADER_INIT_INFO_ANDROID_KHR};
        loaderInitInfo.applicationVM = app_->activity->vm;
        loaderInitInfo.applicationContext = app_->activity->clazz;
        return XR_CHECK(initializeLoader(
            reinterpret_cast<const XrLoaderInitInfoBaseHeaderKHR*>(&loaderInitInfo)));
    }

    bool isExtensionAvailable(const char* name) const {
        uint32_t count = 0;
        xrEnumerateInstanceExtensionProperties(nullptr, 0, &count, nullptr);
        std::vector<XrExtensionProperties> props(count, {XR_TYPE_EXTENSION_PROPERTIES});
        xrEnumerateInstanceExtensionProperties(nullptr, count, &count, props.data());
        for (const auto& prop : props) {
            if (strcmp(prop.extensionName, name) == 0) {
                return true;
            }
        }
        return false;
    }

    bool createInstance() {
        std::vector<const char*> extensions = {
            XR_KHR_ANDROID_CREATE_INSTANCE_EXTENSION_NAME,
            XR_KHR_OPENGL_ES_ENABLE_EXTENSION_NAME,
        };
        if (!isExtensionAvailable(XR_FB_PASSTHROUGH_EXTENSION_NAME)) {
            LOGE("XR_FB_passthrough is unavailable; refusing to start a black immersive app");
            return false;
        }
        extensions.push_back(XR_FB_PASSTHROUGH_EXTENSION_NAME);
        if (isExtensionAvailable(kTouchPlusExtension)) {
            extensions.push_back(kTouchPlusExtension);
            touchPlusEnabled_ = true;
        }
        if (enableHandTelemetry_) {
            if (isExtensionAvailable(XR_EXT_HAND_TRACKING_EXTENSION_NAME)) {
                extensions.push_back(XR_EXT_HAND_TRACKING_EXTENSION_NAME);
                handTrackingEnabled_ = true;
            } else {
                LOGW("XR_EXT_hand_tracking is unavailable");
            }
            if (isExtensionAvailable("XR_META_simultaneous_hands_and_controllers")) {
                extensions.push_back("XR_META_simultaneous_hands_and_controllers");
            }
        }

        XrInstanceCreateInfoAndroidKHR androidInfo{XR_TYPE_INSTANCE_CREATE_INFO_ANDROID_KHR};
        androidInfo.applicationVM = app_->activity->vm;
        androidInfo.applicationActivity = app_->activity->clazz;

        XrInstanceCreateInfo createInfo{XR_TYPE_INSTANCE_CREATE_INFO};
        createInfo.next = &androidInfo;
        strcpy(createInfo.applicationInfo.applicationName, "quest_streamer_telemetry");
        createInfo.applicationInfo.applicationVersion = 1;
        strcpy(createInfo.applicationInfo.engineName, "quest_streamer");
        createInfo.applicationInfo.engineVersion = 1;
        // OpenXR 1.0 plus the Touch Plus extension covers Quest 3/3S while
        // remaining compatible with runtimes that have not promoted it to 1.1.
        createInfo.applicationInfo.apiVersion = XR_MAKE_VERSION(1, 0, 0);
        createInfo.enabledExtensionCount = static_cast<uint32_t>(extensions.size());
        createInfo.enabledExtensionNames = extensions.data();

        if (!XR_CHECK(xrCreateInstance(&createInfo, &instance_))) {
            return false;
        }
        xrGetInstanceProcAddr(
            instance_,
            "xrGetOpenGLESGraphicsRequirementsKHR",
            reinterpret_cast<PFN_xrVoidFunction*>(&xrGetOpenGLESGraphicsRequirementsKHR_));
        return xrGetOpenGLESGraphicsRequirementsKHR_ != nullptr;
    }

    bool createSystem() {
        XrSystemGetInfo systemInfo{XR_TYPE_SYSTEM_GET_INFO};
        systemInfo.formFactor = XR_FORM_FACTOR_HEAD_MOUNTED_DISPLAY;
        if (!XR_CHECK(xrGetSystem(instance_, &systemInfo, &systemId_))) {
            return false;
        }

        XrSystemPassthroughPropertiesFB passthroughProperties{
            XR_TYPE_SYSTEM_PASSTHROUGH_PROPERTIES_FB};
        XrSystemProperties systemProperties{XR_TYPE_SYSTEM_PROPERTIES};
        systemProperties.next = &passthroughProperties;
        if (!XR_CHECK(xrGetSystemProperties(instance_, systemId_, &systemProperties))) {
            return false;
        }
        if (passthroughProperties.supportsPassthrough != XR_TRUE) {
            LOGE("OpenXR system reports that passthrough is unsupported");
            return false;
        }
        return true;
    }

    bool createSession() {
        XrGraphicsRequirementsOpenGLESKHR requirements{
            XR_TYPE_GRAPHICS_REQUIREMENTS_OPENGL_ES_KHR};
        if (!XR_CHECK(xrGetOpenGLESGraphicsRequirementsKHR_(instance_, systemId_, &requirements))) {
            return false;
        }

        XrGraphicsBindingOpenGLESAndroidKHR graphicsBinding{
            XR_TYPE_GRAPHICS_BINDING_OPENGL_ES_ANDROID_KHR};
        graphicsBinding.display = egl_.display;
        graphicsBinding.config = egl_.config;
        graphicsBinding.context = egl_.context;

        XrSessionCreateInfo sessionInfo{XR_TYPE_SESSION_CREATE_INFO};
        sessionInfo.next = &graphicsBinding;
        sessionInfo.systemId = systemId_;
        return XR_CHECK(xrCreateSession(instance_, &sessionInfo, &session_));
    }

    bool createPassthrough() {
        xrGetInstanceProcAddr(
            instance_,
            "xrCreatePassthroughFB",
            reinterpret_cast<PFN_xrVoidFunction*>(&xrCreatePassthroughFB_));
        xrGetInstanceProcAddr(
            instance_,
            "xrDestroyPassthroughFB",
            reinterpret_cast<PFN_xrVoidFunction*>(&xrDestroyPassthroughFB_));
        xrGetInstanceProcAddr(
            instance_,
            "xrCreatePassthroughLayerFB",
            reinterpret_cast<PFN_xrVoidFunction*>(&xrCreatePassthroughLayerFB_));
        xrGetInstanceProcAddr(
            instance_,
            "xrDestroyPassthroughLayerFB",
            reinterpret_cast<PFN_xrVoidFunction*>(&xrDestroyPassthroughLayerFB_));
        if (!xrCreatePassthroughFB_ || !xrDestroyPassthroughFB_ ||
            !xrCreatePassthroughLayerFB_ || !xrDestroyPassthroughLayerFB_) {
            LOGE("XR_FB_passthrough function pointers are unavailable");
            return false;
        }

        XrPassthroughCreateInfoFB passthroughInfo{XR_TYPE_PASSTHROUGH_CREATE_INFO_FB};
        passthroughInfo.flags = XR_PASSTHROUGH_IS_RUNNING_AT_CREATION_BIT_FB;
        if (!XR_CHECK(xrCreatePassthroughFB_(session_, &passthroughInfo, &passthrough_))) {
            return false;
        }

        XrPassthroughLayerCreateInfoFB layerInfo{
            XR_TYPE_PASSTHROUGH_LAYER_CREATE_INFO_FB};
        layerInfo.passthrough = passthrough_;
        layerInfo.flags = XR_PASSTHROUGH_IS_RUNNING_AT_CREATION_BIT_FB;
        layerInfo.purpose = XR_PASSTHROUGH_LAYER_PURPOSE_RECONSTRUCTION_FB;
        if (!XR_CHECK(
                xrCreatePassthroughLayerFB_(session_, &layerInfo, &passthroughLayer_))) {
            return false;
        }
        LOGI("OpenXR passthrough composition initialized");
        return true;
    }

    void createSpaces() {
        XrReferenceSpaceCreateInfo spaceInfo{XR_TYPE_REFERENCE_SPACE_CREATE_INFO};
        spaceInfo.poseInReferenceSpace.orientation.w = 1.0f;
        spaceInfo.referenceSpaceType = XR_REFERENCE_SPACE_TYPE_LOCAL;
        XR_CHECK(xrCreateReferenceSpace(session_, &spaceInfo, &localSpace_));
    }

    void rotateProviderSession(const char* reason) {
        const int64_t previous = providerSessionId_;
        providerSessionId_ = monotonicNs();
        controllerFrameSeq_ = 0;
        LOGI(
            "OpenXR controller provider session rotated reason=%s old=%lld new=%lld",
            reason,
            static_cast<long long>(previous),
            static_cast<long long>(providerSessionId_));
    }

    void createActions() {
        XR_CHECK(xrStringToPath(instance_, "/user/hand/left", &leftHandPath_));
        XR_CHECK(xrStringToPath(instance_, "/user/hand/right", &rightHandPath_));
        const XrPath hands[] = {leftHandPath_, rightHandPath_};

        XrActionSetCreateInfo setInfo{XR_TYPE_ACTION_SET_CREATE_INFO};
        strcpy(setInfo.actionSetName, "quest_streamer");
        strcpy(setInfo.localizedActionSetName, "Quest Streamer");
        setInfo.priority = 0;
        XR_CHECK(xrCreateActionSet(instance_, &setInfo, &actionSet_));

        gripPoseAction_ = createAction(
            XR_ACTION_TYPE_POSE_INPUT, "grip_pose", "Grip Pose", hands, 2);
        triggerValueAction_ = createAction(
            XR_ACTION_TYPE_FLOAT_INPUT, "trigger_value", "Trigger Value", hands, 2);
        gripValueAction_ = createAction(
            XR_ACTION_TYPE_FLOAT_INPUT, "grip_value", "Grip Value", hands, 2);
        thumbstickAction_ = createAction(
            XR_ACTION_TYPE_VECTOR2F_INPUT, "thumbstick", "Thumbstick", hands, 2);
        thumbstickClickAction_ = createAction(
            XR_ACTION_TYPE_BOOLEAN_INPUT, "thumbstick_click", "Thumbstick Click", hands, 2);
        thumbrestTouchAction_ = createAction(
            XR_ACTION_TYPE_BOOLEAN_INPUT, "thumbrest_touch", "Thumbrest Touch", hands, 2);
        aButtonAction_ = createAction(XR_ACTION_TYPE_BOOLEAN_INPUT, "button_a", "A", nullptr, 0);
        bButtonAction_ = createAction(XR_ACTION_TYPE_BOOLEAN_INPUT, "button_b", "B", nullptr, 0);
        xButtonAction_ = createAction(XR_ACTION_TYPE_BOOLEAN_INPUT, "button_x", "X", nullptr, 0);
        yButtonAction_ = createAction(XR_ACTION_TYPE_BOOLEAN_INPUT, "button_y", "Y", nullptr, 0);

        createActionSpaces();
        suggestBindings();

        XrSessionActionSetsAttachInfo attachInfo{XR_TYPE_SESSION_ACTION_SETS_ATTACH_INFO};
        attachInfo.countActionSets = 1;
        attachInfo.actionSets = &actionSet_;
        XR_CHECK(xrAttachSessionActionSets(session_, &attachInfo));
    }

    XrAction createAction(
        XrActionType type,
        const char* name,
        const char* localized,
        const XrPath* subactionPaths,
        uint32_t subactionPathCount) {
        XrActionCreateInfo info{XR_TYPE_ACTION_CREATE_INFO};
        info.actionType = type;
        strcpy(info.actionName, name);
        strcpy(info.localizedActionName, localized);
        info.countSubactionPaths = subactionPathCount;
        info.subactionPaths = subactionPaths;
        XrAction action = XR_NULL_HANDLE;
        XR_CHECK(xrCreateAction(actionSet_, &info, &action));
        return action;
    }

    void createActionSpaces() {
        XrActionSpaceCreateInfo info{XR_TYPE_ACTION_SPACE_CREATE_INFO};
        info.poseInActionSpace.orientation.w = 1.0f;
        info.action = gripPoseAction_;
        info.subactionPath = leftHandPath_;
        XR_CHECK(xrCreateActionSpace(session_, &info, &leftGripSpace_));
        info.subactionPath = rightHandPath_;
        XR_CHECK(xrCreateActionSpace(session_, &info, &rightGripSpace_));
        actionSpaces_.push_back(leftGripSpace_);
        actionSpaces_.push_back(rightGripSpace_);
    }

    XrPath path(const char* pathString) {
        XrPath p = XR_NULL_PATH;
        XR_CHECK(xrStringToPath(instance_, pathString, &p));
        return p;
    }

    void addBinding(
        std::vector<XrActionSuggestedBinding>& bindings,
        XrAction action,
        const char* pathString) {
        bindings.push_back({action, path(pathString)});
    }

    void suggestForProfile(const char* profilePath) {
        std::vector<XrActionSuggestedBinding> b;
        addBinding(b, gripPoseAction_, "/user/hand/left/input/grip/pose");
        addBinding(b, gripPoseAction_, "/user/hand/right/input/grip/pose");
        addBinding(b, triggerValueAction_, "/user/hand/left/input/trigger/value");
        addBinding(b, triggerValueAction_, "/user/hand/right/input/trigger/value");
        addBinding(b, gripValueAction_, "/user/hand/left/input/squeeze/value");
        addBinding(b, gripValueAction_, "/user/hand/right/input/squeeze/value");
        addBinding(b, thumbstickAction_, "/user/hand/left/input/thumbstick");
        addBinding(b, thumbstickAction_, "/user/hand/right/input/thumbstick");
        addBinding(b, thumbstickClickAction_, "/user/hand/left/input/thumbstick/click");
        addBinding(b, thumbstickClickAction_, "/user/hand/right/input/thumbstick/click");
        addBinding(b, thumbrestTouchAction_, "/user/hand/left/input/thumbrest/touch");
        addBinding(b, thumbrestTouchAction_, "/user/hand/right/input/thumbrest/touch");
        addBinding(b, xButtonAction_, "/user/hand/left/input/x/click");
        addBinding(b, yButtonAction_, "/user/hand/left/input/y/click");
        addBinding(b, aButtonAction_, "/user/hand/right/input/a/click");
        addBinding(b, bButtonAction_, "/user/hand/right/input/b/click");

        XrInteractionProfileSuggestedBinding suggested{
            XR_TYPE_INTERACTION_PROFILE_SUGGESTED_BINDING};
        suggested.interactionProfile = path(profilePath);
        suggested.countSuggestedBindings = static_cast<uint32_t>(b.size());
        suggested.suggestedBindings = b.data();
        XrResult result = xrSuggestInteractionProfileBindings(instance_, &suggested);
        if (XR_FAILED(result)) {
            LOGW("xrSuggestInteractionProfileBindings failed for %s: %d", profilePath, result);
        }
    }

    void suggestBindings() {
        suggestForProfile("/interaction_profiles/oculus/touch_controller");
        if (touchPlusEnabled_) {
            // XR_META_touch_controller_plus is the OpenXR 1.0 extension. Its
            // profile path predates the differently ordered OpenXR 1.1 core
            // name (/meta/touch_plus_controller).
            suggestForProfile("/interaction_profiles/meta/touch_controller_plus");
        }
    }

    void createHandTrackers() {
        if (!handTrackingEnabled_) {
            return;
        }
        xrGetInstanceProcAddr(
            instance_,
            "xrCreateHandTrackerEXT",
            reinterpret_cast<PFN_xrVoidFunction*>(&xrCreateHandTrackerEXT_));
        xrGetInstanceProcAddr(
            instance_,
            "xrDestroyHandTrackerEXT",
            reinterpret_cast<PFN_xrVoidFunction*>(&xrDestroyHandTrackerEXT_));
        xrGetInstanceProcAddr(
            instance_,
            "xrLocateHandJointsEXT",
            reinterpret_cast<PFN_xrVoidFunction*>(&xrLocateHandJointsEXT_));
        if (!xrCreateHandTrackerEXT_ || !xrDestroyHandTrackerEXT_ || !xrLocateHandJointsEXT_) {
            LOGW("hand tracking function pointers unavailable");
            return;
        }

        XrHandTrackerCreateInfoEXT info{XR_TYPE_HAND_TRACKER_CREATE_INFO_EXT};
        info.handJointSet = XR_HAND_JOINT_SET_DEFAULT_EXT;
        info.hand = XR_HAND_LEFT_EXT;
        XR_CHECK(xrCreateHandTrackerEXT_(session_, &info, &handTrackerLeft_));
        info.hand = XR_HAND_RIGHT_EXT;
        XR_CHECK(xrCreateHandTrackerEXT_(session_, &info, &handTrackerRight_));
    }

    bool getBool(XrAction action, XrPath hand = XR_NULL_PATH) {
        XrActionStateGetInfo info{XR_TYPE_ACTION_STATE_GET_INFO};
        info.action = action;
        info.subactionPath = hand;
        XrActionStateBoolean state{XR_TYPE_ACTION_STATE_BOOLEAN};
        if (!XR_CHECK(xrGetActionStateBoolean(session_, &info, &state)) || !state.isActive) {
            return false;
        }
        return state.currentState == XR_TRUE;
    }

    bool getPoseActive(XrPath hand) {
        XrActionStateGetInfo info{XR_TYPE_ACTION_STATE_GET_INFO};
        info.action = gripPoseAction_;
        info.subactionPath = hand;
        XrActionStatePose state{XR_TYPE_ACTION_STATE_POSE};
        if (!XR_CHECK(xrGetActionStatePose(session_, &info, &state))) {
            return false;
        }
        return state.isActive == XR_TRUE;
    }

    float getFloat(XrAction action, XrPath hand) {
        XrActionStateGetInfo info{XR_TYPE_ACTION_STATE_GET_INFO};
        info.action = action;
        info.subactionPath = hand;
        XrActionStateFloat state{XR_TYPE_ACTION_STATE_FLOAT};
        if (!XR_CHECK(xrGetActionStateFloat(session_, &info, &state)) || !state.isActive) {
            return 0.0f;
        }
        return state.currentState;
    }

    XrVector2f getVec2(XrAction action, XrPath hand) {
        XrActionStateGetInfo info{XR_TYPE_ACTION_STATE_GET_INFO};
        info.action = action;
        info.subactionPath = hand;
        XrActionStateVector2f state{XR_TYPE_ACTION_STATE_VECTOR2F};
        if (!XR_CHECK(xrGetActionStateVector2f(session_, &info, &state)) || !state.isActive) {
            return {0.0f, 0.0f};
        }
        return state.currentState;
    }

    void updateTelemetry(XrTime time) {
        XrActiveActionSet activeSet{actionSet_, XR_NULL_PATH};
        XrActionsSyncInfo syncInfo{XR_TYPE_ACTIONS_SYNC_INFO};
        syncInfo.countActiveActionSets = 1;
        syncInfo.activeActionSets = &activeSet;
        XR_CHECK(xrSyncActions(session_, &syncInfo));

        emitControllerTelemetry(time);
        emitHandTelemetry(time);
    }

    void emitControllerTelemetry(XrTime time) {
        XrSpaceLocation leftLocation{XR_TYPE_SPACE_LOCATION};
        XrSpaceLocation rightLocation{XR_TYPE_SPACE_LOCATION};
        xrLocateSpace(leftGripSpace_, localSpace_, time, &leftLocation);
        xrLocateSpace(rightGripSpace_, localSpace_, time, &rightLocation);

        const bool leftActive = getPoseActive(leftHandPath_);
        const bool rightActive = getPoseActive(rightHandPath_);
        const bool leftValid =
            leftActive &&
            (leftLocation.locationFlags & XR_SPACE_LOCATION_POSITION_VALID_BIT) != 0 &&
            (leftLocation.locationFlags & XR_SPACE_LOCATION_ORIENTATION_VALID_BIT) != 0;
        const bool rightValid =
            rightActive &&
            (rightLocation.locationFlags & XR_SPACE_LOCATION_POSITION_VALID_BIT) != 0 &&
            (rightLocation.locationFlags & XR_SPACE_LOCATION_ORIENTATION_VALID_BIT) != 0;

        // Keep the historical logcat surface for diagnostics during migration.
        std::ostringstream transforms;
        std::ostringstream buttons;
        bool first = true;
        if (leftValid) {
            transforms << "l:";
            appendMatrix(transforms, leftLocation.pose);
            appendButtons(buttons, true, leftActive, leftLocation.locationFlags);
            first = false;
        }
        if (rightValid) {
            if (!first) {
                transforms << '|';
                buttons << ',';
            }
            transforms << "r:";
            appendMatrix(transforms, rightLocation.pose);
            appendButtons(buttons, false, rightActive, rightLocation.locationFlags);
        }
        if (!first) {
            const std::string payload = transforms.str() + "&" + buttons.str();
            __android_log_print(ANDROID_LOG_INFO, kControllerLogTag, "%s", payload.c_str());
        }

        std::ostringstream json;
        json << "{\"schema\":\"" << kControllerSchema
             << "\",\"session_id\":\"" << providerSessionId_
             << "\",\"frame_seq\":" << controllerFrameSeq_++
             << ",\"sample_time_ns\":" << static_cast<int64_t>(time)
             << ",\"reference_space\":\"local\",\"hands\":{";
        appendControllerJson(json, true, leftActive, leftLocation);
        json << ',';
        appendControllerJson(json, false, rightActive, rightLocation);
        json << "}}";
        controllerServer_.sendLine(json.str());
    }

    void appendButtons(
        std::ostringstream& ss,
        bool left,
        bool active,
        XrSpaceLocationFlags locationFlags) {
        XrPath hand = left ? leftHandPath_ : rightHandPath_;
        XrVector2f stick = getVec2(thumbstickAction_, hand);
        float trigger = getFloat(triggerValueAction_, hand);
        float grip = getFloat(gripValueAction_, hand);
        if (left) {
            ss << "L,";
            if (getBool(xButtonAction_)) ss << "X,";
            if (getBool(yButtonAction_)) ss << "Y,";
            if (trigger > 0.5f) ss << "LTr,";
            if (grip > 0.5f) ss << "LG,";
            if (getBool(thumbrestTouchAction_, hand)) ss << "LThU,";
            if (getBool(thumbstickClickAction_, hand)) ss << "LJ,";
            ss << "leftJS " << fixed(stick.x) << " " << fixed(stick.y) << ",";
            ss << "leftTrig " << fixed(trigger) << ",";
            ss << "leftGrip " << fixed(grip);
        } else {
            ss << "R,";
            if (getBool(aButtonAction_)) ss << "A,";
            if (getBool(bButtonAction_)) ss << "B,";
            if (trigger > 0.5f) ss << "RTr,";
            if (grip > 0.5f) ss << "RG,";
            if (getBool(thumbrestTouchAction_, hand)) ss << "RThU,";
            if (getBool(thumbstickClickAction_, hand)) ss << "RJ,";
            ss << "rightJS " << fixed(stick.x) << " " << fixed(stick.y) << ",";
            ss << "rightTrig " << fixed(trigger) << ",";
            ss << "rightGrip " << fixed(grip);
        }
        const char* prefix = left ? "left" : "right";
        ss << ',' << prefix << "PoseSource 1"
           << ',' << prefix << "ControllerActive " << (active ? 1 : 0)
           << ',' << prefix << "PositionValid "
           << ((locationFlags & XR_SPACE_LOCATION_POSITION_VALID_BIT) != 0 ? 1 : 0)
           << ',' << prefix << "OrientationValid "
           << ((locationFlags & XR_SPACE_LOCATION_ORIENTATION_VALID_BIT) != 0 ? 1 : 0)
           << ',' << prefix << "PositionTracked "
           << ((locationFlags & XR_SPACE_LOCATION_POSITION_TRACKED_BIT) != 0 ? 1 : 0)
           << ',' << prefix << "OrientationTracked "
           << ((locationFlags & XR_SPACE_LOCATION_ORIENTATION_TRACKED_BIT) != 0 ? 1 : 0);
    }

    void appendControllerJson(
        std::ostringstream& ss,
        bool left,
        bool active,
        const XrSpaceLocation& location) {
        const char* side = left ? "left" : "right";
        XrPath hand = left ? leftHandPath_ : rightHandPath_;
        const bool positionValid =
            (location.locationFlags & XR_SPACE_LOCATION_POSITION_VALID_BIT) != 0;
        const bool orientationValid =
            (location.locationFlags & XR_SPACE_LOCATION_ORIENTATION_VALID_BIT) != 0;
        const bool positionTracked =
            (location.locationFlags & XR_SPACE_LOCATION_POSITION_TRACKED_BIT) != 0;
        const bool orientationTracked =
            (location.locationFlags & XR_SPACE_LOCATION_ORIENTATION_TRACKED_BIT) != 0;
        const XrVector2f stick = getVec2(thumbstickAction_, hand);
        const float trigger = getFloat(triggerValueAction_, hand);
        const float grip = getFloat(gripValueAction_, hand);

        ss << '\"' << side << "\":{\"pose_source\":\"tracked_remote\""
           << ",\"active\":";
        appendJsonBool(ss, active);
        ss << ",\"position_valid\":";
        appendJsonBool(ss, positionValid);
        ss << ",\"orientation_valid\":";
        appendJsonBool(ss, orientationValid);
        ss << ",\"position_tracked\":";
        appendJsonBool(ss, positionTracked);
        ss << ",\"orientation_tracked\":";
        appendJsonBool(ss, orientationTracked);
        ss << ",\"pose\":";
        if (active && positionValid && orientationValid) {
            appendMatrixJson(ss, location.pose);
        } else {
            ss << "null";
        }
        ss << ",\"trigger\":" << fixed(trigger)
           << ",\"grip\":" << fixed(grip)
           << ",\"joystick\":[" << fixed(stick.x) << ',' << fixed(stick.y)
           << "],\"buttons\":{\"primary\":";
        appendJsonBool(ss, left ? getBool(xButtonAction_) : getBool(aButtonAction_));
        ss << ",\"secondary\":";
        appendJsonBool(ss, left ? getBool(yButtonAction_) : getBool(bButtonAction_));
        ss << ",\"thumb_rest\":";
        appendJsonBool(ss, getBool(thumbrestTouchAction_, hand));
        ss << ",\"stick\":";
        appendJsonBool(ss, getBool(thumbstickClickAction_, hand));
        ss << "}}";
    }

    void emitHandTelemetry(XrTime time) {
        if (!xrLocateHandJointsEXT_) {
            return;
        }
        emitOneHand("Left", handTrackerLeft_, leftJoints_, time);
        emitOneHand("Right", handTrackerRight_, rightJoints_, time);
    }

    void emitOneHand(
        const char* side,
        XrHandTrackerEXT tracker,
        std::array<XrHandJointLocationEXT, XR_HAND_JOINT_COUNT_EXT>& joints,
        XrTime time) {
        if (tracker == XR_NULL_HANDLE) {
            return;
        }
        XrHandJointLocationsEXT locations{XR_TYPE_HAND_JOINT_LOCATIONS_EXT};
        locations.jointCount = static_cast<uint32_t>(joints.size());
        locations.jointLocations = joints.data();
        XrHandJointsLocateInfoEXT locateInfo{XR_TYPE_HAND_JOINTS_LOCATE_INFO_EXT};
        locateInfo.baseSpace = localSpace_;
        locateInfo.time = time;
        if (!XR_CHECK(xrLocateHandJointsEXT_(tracker, &locateInfo, &locations)) ||
            !locations.isActive) {
            return;
        }

        constexpr int streamedJoints[] = {
            1, 2, 3, 4, 5,
            7, 8, 9, 10,
            12, 13, 14, 15,
            17, 18, 19, 20,
            22, 23, 24, 25,
        };

        const auto& wrist = joints[XR_HAND_JOINT_WRIST_EXT];
        const bool wristValid =
            (wrist.locationFlags & XR_SPACE_LOCATION_POSITION_VALID_BIT) != 0 &&
            (wrist.locationFlags & XR_SPACE_LOCATION_ORIENTATION_VALID_BIT) != 0;
        if (!wristValid) {
            return;
        }

        std::ostringstream ss;
        ss << side << " wrist:, ";
        appendVec3(ss, wrist.pose.position);
        ss << ", ";
        appendQuat(ss, wrist.pose.orientation);
        ss << "\n" << side << " landmarks:";

        for (int index : streamedJoints) {
            ss << ", ";
            if (index >= 0 && index < static_cast<int>(joints.size()) &&
                (joints[index].locationFlags & XR_SPACE_LOCATION_POSITION_VALID_BIT) != 0) {
                appendVec3(ss, joints[index].pose.position);
            } else {
                ss << "0, 0, 0";
            }
        }
        handSink_.sendLine(ss.str());
    }
};

void handleCmd(android_app*, int32_t) {}

} // namespace

void android_main(android_app* app) {
    app->onAppCmd = handleCmd;

    const bool enableHandTelemetry = booleanIntentExtra(
        app, "enable_hand_telemetry", false);
    LOGI("optional hand telemetry enabled=%d", enableHandTelemetry ? 1 : 0);
    QuestTelemetryApp telemetry(app, enableHandTelemetry);
    if (!telemetry.init()) {
        LOGE("Quest telemetry failed to initialize; returning to the system shell");
        telemetry.shutdown();
        ANativeActivity_finish(app->activity);
        return;
    }

    while (!app->destroyRequested) {
        android_poll_source* source = nullptr;
        int events = 0;
        const int timeoutMs = telemetry.sessionRunning() ? 0 : 100;
        while (ALooper_pollOnce(timeoutMs, nullptr, &events, reinterpret_cast<void**>(&source)) >=
               0) {
            if (source != nullptr) {
                source->process(app, source);
            }
            if (app->destroyRequested) {
                break;
            }
        }
        telemetry.pollEvents();
        if (telemetry.sessionRunning()) {
            telemetry.frame();
        }
    }

    telemetry.shutdown();
}
