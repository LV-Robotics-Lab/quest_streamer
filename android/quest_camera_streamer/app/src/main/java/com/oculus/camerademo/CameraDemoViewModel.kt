/*
 * CameraStreamerViewModel — enumerates the Meta passthrough cameras and
 * streams JPEG frames over TCP to a PC-side consumer.
 *
 * Two modes:
 *   - STEREO: opens the two 1280x960 per-eye cameras (positions Left+Right)
 *             concurrently, tags frames 'L' / 'R'.
 *   - WIDE:   opens the single stereo-packed camera (the one with the
 *             widest supported width, e.g. 3840x1920 on Quest 3S), tags
 *             frames 'W'. Downstream receivers split into L/R halves.
 *
 * Kept in the original CameraDemoViewModel.kt file to minimise changes to
 * the rest of the scaffolding; the class was renamed in-place from
 * XrCameraDemoViewModel -> CameraStreamerViewModel (MainActivity updated
 * accordingly).
 */

package com.oculus.camerademo

import android.app.Application
import android.content.Context.CAMERA_SERVICE
import android.graphics.ImageFormat
import android.hardware.camera2.CameraCaptureSession
import android.hardware.camera2.CameraCharacteristics
import android.hardware.camera2.CameraDevice
import android.hardware.camera2.CameraManager
import android.hardware.camera2.params.OutputConfiguration
import android.hardware.camera2.params.SessionConfiguration
import android.media.ImageReader
import android.os.Handler
import android.os.HandlerThread
import android.os.Looper
import android.view.Surface
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.LiveData
import androidx.lifecycle.MutableLiveData
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.Executors

enum class StreamMode { STEREO, WIDE }

class CameraStreamerViewModel(application: Application) : AndroidViewModel(application) {
    companion object {
        const val IMAGE_BUFFER_SIZE = 3
        const val DEFAULT_PORT = 9100
        const val JPEG_QUALITY = 75

        // STEREO mode prefers the per-eye 1280x960 sources.
        const val STEREO_TARGET_WIDTH = 1280
        const val STEREO_TARGET_HEIGHT = 960

        // WIDE mode: pick widest capture the camera supports, matching the
        // dumpsys entry for the stereo-packed device.
        const val WIDE_TARGET_WIDTH = 3840
        const val WIDE_TARGET_HEIGHT = 1920
    }

    private val permissionManager = PermissionManager()

    private val cameraManager: CameraManager by
        lazy(LazyThreadSafetyMode.NONE) {
            application.applicationContext.getSystemService(CAMERA_SERVICE) as CameraManager
        }

    private val cameraConfigs = ArrayList<CameraConfig>()

    private var _uiState = MutableLiveData(CameraUiState())
    val uiState: LiveData<CameraUiState> = _uiState

    private var _permissionRequestState =
        MutableLiveData(
            PermissionRequestState(
                nativeCameraPermissionGranted =
                    permissionManager.checkPermissions(
                        application,
                        PermissionManager.ANDROID_CAMERA_PERMISSION,
                    ),
                vendorCameraPermissionGranted =
                    permissionManager.checkPermissions(
                        application,
                        PermissionManager.HZOS_CAMERA_PERMISSION,
                    ),
            )
        )
    val permissionRequestState: LiveData<PermissionRequestState> = _permissionRequestState

    // Per-camera state, keyed by cameraId.
    private data class OpenCamera(
        val id: String,
        val sideTag: Char,
        val width: Int,
        val height: Int,
        val device: CameraDevice,
        val session: CameraCaptureSession,
        val reader: ImageReader,
    )

    private val open = ConcurrentHashMap<String, OpenCamera>()
    // Frames for a closing camera must not reach the server, so we keep a
    // generation counter and a set of ids being torn down.
    private val closing = java.util.Collections.synchronizedSet(mutableSetOf<String>())

    // On-headset preview surfaces. `'L'` for the left-eye TextureView, `'R'`
    // for the right, `'W'` for the stereo-packed wide TextureView. Filled
    // lazily from MainActivity as each TextureView's SurfaceTexture becomes
    // available. Only surfaces present at `startStreaming()` time are wired
    // into the capture session.
    private val previewSurfaces = ConcurrentHashMap<Char, Surface>()

    private val imageReaderThread = HandlerThread("imageReaderThread").apply { start() }
    private val imageReaderHandler = Handler(imageReaderThread.looper)
    private val cameraThread = HandlerThread("cameraThread").apply { start() }
    private val cameraHandler = Handler(cameraThread.looper)
    private val mainHandler = Handler(Looper.getMainLooper())
    private val cameraSessionExecutor = Executors.newSingleThreadExecutor()

    private val _eventLiveData = MutableLiveData<CameraEvent>(CameraEvent.Empty)
    val cameraEvents: LiveData<CameraEvent> = _eventLiveData

    private val streamingServer = StreamingServer(DEFAULT_PORT)
    val serverPort: Int get() = streamingServer.port
    val isStreaming: Boolean get() = streamingServer.isRunning
    val hasClient: Boolean get() = streamingServer.hasClient

    // Meta PCA exposes cameras 50 + 51 (per-eye 1280×1280) to apps. The
    // stereo-packed "wide" camera (id 60 on Quest 3S) is visible in
    // `dumpsys media.camera` but is NOT returned by `cameraIdList`, so WIDE
    // mode has nothing to open on current Horizon OS. Keep STEREO as the
    // default; WIDE is kept as a placeholder that auto-falls-back to STEREO
    // with a toast.
    var mode: StreamMode = StreamMode.STEREO

    fun init() {
        cameraConfigs.clear()
        logv("Init")
        cameraManager.cameraIdList.forEach { cameraId ->
            val c = cameraManager.getCameraCharacteristics(cameraId)
            val pixelSize = c.get(CameraCharacteristics.SENSOR_INFO_PIXEL_ARRAY_SIZE)
            val position = Position.fromInt(c.get(KEY_POSITION))
            val lensRotation = c.get(CameraCharacteristics.LENS_POSE_ROTATION)
            val lensTranslation = c.get(CameraCharacteristics.LENS_POSE_TRANSLATION)
            val cameraSource = c.get(KEY_SOURCE)
            cameraConfigs.add(
                CameraConfig(
                    id = cameraId,
                    width = pixelSize?.width ?: 0,
                    height = pixelSize?.height ?: 0,
                    lensRotation = lensRotation ?: floatArrayOf(),
                    lensTranslation = lensTranslation ?: floatArrayOf(),
                    position = position,
                    isPassthrough = cameraSource == CAMERA_SOURCE_PASSTHROUGH,
                )
            )
        }
        logConfigs(cameraConfigs)
    }

    fun startStreaming() {
        val state = _permissionRequestState.value
        if (state == null || !state.nativeCameraPermissionGranted ||
            !state.vendorCameraPermissionGranted) {
            postEvent("Missing camera permissions")
            return
        }
        if (open.isNotEmpty()) return

        streamingServer.start()

        when (mode) {
            StreamMode.STEREO -> {
                val left = pickCamera(position = Position.Left, widthHint = STEREO_TARGET_WIDTH)
                val right = pickCamera(position = Position.Right, widthHint = STEREO_TARGET_WIDTH)
                if (left == null || right == null) {
                    postEvent("Stereo cameras not found (left=${left?.id} right=${right?.id})")
                    streamingServer.stop()
                    return
                }
                openOne(left, 'L', STEREO_TARGET_WIDTH, STEREO_TARGET_HEIGHT)
                openOne(right, 'R', STEREO_TARGET_WIDTH, STEREO_TARGET_HEIGHT)
                postEvent("Streaming stereo on port $DEFAULT_PORT")
            }
            StreamMode.WIDE -> {
                val wide = pickWide()
                if (wide == null) {
                    // Meta PCA doesn't expose the 3840-wide composite to apps.
                    // Fall back to STEREO so the user still gets frames.
                    postEvent("Wide camera unavailable, falling back to STEREO")
                    val left = pickCamera(Position.Left, STEREO_TARGET_WIDTH)
                    val right = pickCamera(Position.Right, STEREO_TARGET_WIDTH)
                    if (left == null || right == null) {
                        postEvent("Stereo cameras not found either; giving up")
                        streamingServer.stop()
                        return
                    }
                    openOne(left, 'L', STEREO_TARGET_WIDTH, STEREO_TARGET_HEIGHT)
                    openOne(right, 'R', STEREO_TARGET_WIDTH, STEREO_TARGET_HEIGHT)
                    return
                }
                openOne(wide, 'W', WIDE_TARGET_WIDTH, WIDE_TARGET_HEIGHT)
                postEvent("Streaming wide on port $DEFAULT_PORT")
            }
        }
    }

    fun stopStreaming() {
        if (open.isEmpty()) return
        open.keys.toList().forEach { closeOne(it) }
        streamingServer.stop()
        postEvent("Streaming stopped")
    }

    fun shutdown() {
        stopStreaming()
    }

    fun onResume() { /* no-op: we don't auto-start */ }

    fun onPause() {
        // Keep streaming across onPause (the app goes paused as the headset
        // sleeps; we let the user stop explicitly).
    }

    fun resetState() {
        stopStreaming()
    }

    fun onHandleCameraEvent() {
        _eventLiveData.value = CameraEvent.Empty
    }

    fun registerPreviewSurface(side: Char, surface: Surface?) {
        if (surface == null) previewSurfaces.remove(side)
        else previewSurfaces[side] = surface
        logv("preview surface ${if (surface == null) "cleared" else "registered"} for $side")
    }

    fun onPermissionGranted(requestResult: Map<String, Boolean>) {
        val androidOk =
            requestResult.getOrDefault(
                PermissionManager.ANDROID_CAMERA_PERMISSION,
                _permissionRequestState.value?.nativeCameraPermissionGranted ?: false,
            )
        val vendorOk =
            requestResult.getOrDefault(
                PermissionManager.HZOS_CAMERA_PERMISSION,
                _permissionRequestState.value?.vendorCameraPermissionGranted ?: false,
            )
        _permissionRequestState.value =
            _permissionRequestState.value?.copy(
                nativeCameraPermissionGranted = androidOk,
                vendorCameraPermissionGranted = vendorOk,
            )
    }

    // --- camera selection ------------------------------------------------

    private fun pickCamera(position: Position, widthHint: Int): CameraConfig? {
        // Prefer a passthrough camera at the requested position whose native
        // pixel array matches the hint (so we skip the stereo-packed one).
        val exact = cameraConfigs.firstOrNull {
            it.isPassthrough && it.position == position && it.width <= widthHint + 64
        }
        if (exact != null) return exact
        return cameraConfigs.firstOrNull { it.isPassthrough && it.position == position }
    }

    private fun pickWide(): CameraConfig? =
        cameraConfigs.filter { it.isPassthrough }.maxByOrNull { it.width }
            ?.takeIf { it.width >= 3000 }  // heuristic; excludes per-eye devices

    // --- camera lifecycle -----------------------------------------------

    private fun openOne(cfg: CameraConfig, sideTag: Char, reqW: Int, reqH: Int) {
        // Choose an actual capture size: clamp request to the camera's native
        // pixel array so we never ask for more than it can produce.
        val w = minOf(reqW, if (cfg.width > 0) cfg.width else reqW)
        val h = minOf(reqH, if (cfg.height > 0) cfg.height else reqH)

        val reader = ImageReader.newInstance(w, h, ImageFormat.YUV_420_888, IMAGE_BUFFER_SIZE)
        reader.setOnImageAvailableListener({ r ->
            val img = r.acquireLatestImage() ?: return@setOnImageAvailableListener
            try {
                if (closing.contains(cfg.id)) return@setOnImageAvailableListener
                val jpeg = YuvJpegEncoder.encode(img, JPEG_QUALITY)
                streamingServer.publish(sideTag, img.width, img.height, jpeg)
            } catch (e: Exception) {
                loge("encode failed on ${cfg.id}: ${e.message}")
            } finally {
                img.close()
            }
        }, imageReaderHandler)

        val previewSurface = previewSurfaces[sideTag]

        cameraManager.openCamera(
            cfg.id,
            object : CameraDevice.StateCallback() {
                override fun onOpened(device: CameraDevice) {
                    try {
                        val outputs = mutableListOf(OutputConfiguration(reader.surface))
                        if (previewSurface != null) {
                            outputs.add(OutputConfiguration(previewSurface))
                        }
                        device.createCaptureSession(
                            SessionConfiguration(
                                SessionConfiguration.SESSION_REGULAR,
                                outputs,
                                cameraSessionExecutor,
                                object : CameraCaptureSession.StateCallback() {
                                    override fun onConfigured(session: CameraCaptureSession) {
                                        val req = device.createCaptureRequest(CameraDevice.TEMPLATE_PREVIEW)
                                        req.addTarget(reader.surface)
                                        if (previewSurface != null) req.addTarget(previewSurface)
                                        session.setRepeatingRequest(req.build(), null, cameraHandler)
                                        open[cfg.id] = OpenCamera(cfg.id, sideTag, w, h, device, session, reader)
                                        postEvent("Camera ${cfg.id} (${sideTag}) started at ${w}x${h}")
                                    }
                                    override fun onConfigureFailed(session: CameraCaptureSession) {
                                        loge("Failed to start session for camera ${cfg.id}")
                                    }
                                },
                            )
                        )
                    } catch (e: Exception) {
                        loge("openCamera(${cfg.id}): ${e.message}")
                    }
                }

                override fun onDisconnected(device: CameraDevice) {
                    logv("Camera ${device.id} disconnected")
                    closeOne(cfg.id)
                }
                override fun onError(device: CameraDevice, error: Int) {
                    val msg = when (error) {
                        ERROR_CAMERA_DEVICE -> "device"
                        ERROR_CAMERA_DISABLED -> "disabled"
                        ERROR_CAMERA_IN_USE -> "in-use"
                        ERROR_CAMERA_SERVICE -> "service"
                        ERROR_MAX_CAMERAS_IN_USE -> "max-in-use"
                        else -> "unknown($error)"
                    }
                    loge("Camera ${device.id} error: $msg")
                    closeOne(cfg.id)
                }
            },
            cameraHandler,
        )
    }

    private fun closeOne(cameraId: String) {
        val entry = open.remove(cameraId) ?: return
        closing.add(cameraId)
        try { entry.session.stopRepeating() } catch (_: Exception) {}
        try { entry.session.close() } catch (_: Exception) {}
        try { entry.device.close() } catch (_: Exception) {}
        try { entry.reader.close() } catch (_: Exception) {}
        closing.remove(cameraId)
    }

    // --- misc ------------------------------------------------------------

    private fun arrayToString(arr: FloatArray?): String = arr?.joinToString(",") ?: "[]"

    private fun logConfigs(configs: List<CameraConfig>) {
        for (c in configs) {
            logv("***** Camera ID ****** ${c.id}")
            logv("  width=${c.width} height=${c.height} pos=${c.position} passthrough=${c.isPassthrough}")
            logv("  lensTrans=${arrayToString(c.lensTranslation)} lensRot=${arrayToString(c.lensRotation)}")
        }
    }

    private fun postEvent(message: String) {
        mainHandler.post { _eventLiveData.value = CameraEvent.NotificationEvent(message) }
    }
}
