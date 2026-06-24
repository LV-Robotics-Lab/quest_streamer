package com.queststreamer.camera;

import android.Manifest;
import android.annotation.SuppressLint;
import android.app.Activity;
import android.content.Context;
import android.content.pm.PackageManager;
import android.graphics.ImageFormat;
import android.graphics.Rect;
import android.graphics.YuvImage;
import android.hardware.camera2.CameraCaptureSession;
import android.hardware.camera2.CameraCharacteristics;
import android.hardware.camera2.CameraDevice;
import android.hardware.camera2.CameraManager;
import android.hardware.camera2.CaptureRequest;
import android.hardware.camera2.params.OutputConfiguration;
import android.hardware.camera2.params.SessionConfiguration;
import android.media.Image;
import android.media.ImageReader;
import android.os.Handler;
import android.os.HandlerThread;
import android.util.Log;
import android.view.Surface;

import java.io.ByteArrayOutputStream;
import java.io.DataOutputStream;
import java.net.InetSocketAddress;
import java.net.ServerSocket;
import java.net.Socket;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Set;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicReference;

/**
 * Unity Android plugin that streams Quest passthrough camera frames from the
 * hand-tracking-streamer Unity/OpenXR app.
 *
 * The wire format intentionally matches quest_streamer.camera.CameraStreamer:
 *
 *   [4 bytes magic "QSTR"]
 *   [1 byte side: 'L' | 'R']
 *   [2 bytes width, 2 bytes height]
 *   [4 bytes JPEG byte length]
 *   [JPEG bytes]
 */
public final class QuestCameraStreamer {
    private static final String TAG = "QuestCameraStreamer";
    private static final String HZOS_CAMERA_PERMISSION = "horizonos.permission.HEADSET_CAMERA";
    private static final int DEFAULT_WIDTH = 1280;
    private static final int DEFAULT_HEIGHT = 960;
    private static final int IMAGE_BUFFER_SIZE = 3;
    private static final int JPEG_QUALITY = 75;
    private static final int PERMISSION_REQUEST_CODE = 49100;

    private static final CameraCharacteristics.Key<Integer> KEY_POSITION =
            new CameraCharacteristics.Key<>("com.meta.extra_metadata.position", Integer.class);
    private static final CameraCharacteristics.Key<Integer> KEY_SOURCE =
            new CameraCharacteristics.Key<>("com.meta.extra_metadata.camera_source", Integer.class);

    private static CameraRuntime runtime;

    private QuestCameraStreamer() {}

    public static synchronized boolean start(Activity activity, int port) {
        if (activity == null) {
            Log.e(TAG, "start failed: Activity is null");
            return false;
        }
        if (!hasPermissions(activity)) {
            activity.runOnUiThread(new Runnable() {
                @Override
                public void run() {
                    activity.requestPermissions(
                            new String[] {Manifest.permission.CAMERA, HZOS_CAMERA_PERMISSION},
                            PERMISSION_REQUEST_CODE);
                }
            });
            Log.e(TAG, "camera permissions missing; permission request launched, press Start again");
            return false;
        }
        if (runtime == null) {
            runtime = new CameraRuntime(activity.getApplicationContext(), port);
        }
        return runtime.start();
    }

    public static synchronized void stop() {
        if (runtime != null) {
            runtime.stop();
            runtime = null;
        }
    }

    public static synchronized boolean isRunning() {
        return runtime != null && runtime.isRunning();
    }

    public static synchronized boolean hasClient() {
        return runtime != null && runtime.hasClient();
    }

    private static boolean hasPermissions(Context context) {
        return context.checkSelfPermission(Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED
                && context.checkSelfPermission(HZOS_CAMERA_PERMISSION) == PackageManager.PERMISSION_GRANTED;
    }

    private static final class CameraRuntime {
        private final Context context;
        private final CameraManager cameraManager;
        private final StreamingServer server;
        private final ConcurrentHashMap<String, OpenCamera> open = new ConcurrentHashMap<>();
        private final Set<String> closing = Collections.newSetFromMap(new ConcurrentHashMap<String, Boolean>());
        private final HandlerThread imageReaderThread = new HandlerThread("questCameraImageReader");
        private final HandlerThread cameraThread = new HandlerThread("questCameraDevice");
        private final ExecutorService sessionExecutor = Executors.newSingleThreadExecutor();
        private Handler imageReaderHandler;
        private Handler cameraHandler;

        CameraRuntime(Context context, int port) {
            this.context = context;
            this.cameraManager = (CameraManager) context.getSystemService(Context.CAMERA_SERVICE);
            this.server = new StreamingServer(port);
        }

        boolean isRunning() {
            return server.isRunning();
        }

        boolean hasClient() {
            return server.hasClient();
        }

        boolean start() {
            if (!open.isEmpty()) {
                return true;
            }
            if (!imageReaderThread.isAlive()) {
                imageReaderThread.start();
                imageReaderHandler = new Handler(imageReaderThread.getLooper());
            }
            if (!cameraThread.isAlive()) {
                cameraThread.start();
                cameraHandler = new Handler(cameraThread.getLooper());
            }

            List<CameraConfig> configs = enumeratePassthroughCameras();
            CameraConfig left = pickCamera(configs, 0);
            CameraConfig right = pickCamera(configs, 1);
            if (left == null || right == null) {
                Log.e(TAG, "stereo passthrough cameras not found left="
                        + cameraIdOrNull(left) + " right=" + cameraIdOrNull(right));
                return false;
            }

            server.start();
            openOne(left, 'L');
            openOne(right, 'R');
            Log.i(TAG, "camera streamer started on port " + server.port);
            return true;
        }

        void stop() {
            for (String cameraId : new ArrayList<>(open.keySet())) {
                closeOne(cameraId);
            }
            server.stop();
            sessionExecutor.shutdownNow();
            imageReaderThread.quitSafely();
            cameraThread.quitSafely();
            Log.i(TAG, "camera streamer stopped");
        }

        private List<CameraConfig> enumeratePassthroughCameras() {
            ArrayList<CameraConfig> configs = new ArrayList<>();
            try {
                for (String cameraId : cameraManager.getCameraIdList()) {
                    CameraCharacteristics c = cameraManager.getCameraCharacteristics(cameraId);
                    Integer source = c.get(KEY_SOURCE);
                    Integer position = c.get(KEY_POSITION);
                    if (source != null && source == 0 && position != null) {
                        configs.add(new CameraConfig(cameraId, position));
                        Log.i(TAG, "passthrough camera id=" + cameraId + " position=" + position);
                    }
                }
            } catch (Exception e) {
                Log.e(TAG, "camera enumeration failed", e);
            }
            return configs;
        }

        private CameraConfig pickCamera(List<CameraConfig> configs, int position) {
            for (CameraConfig cfg : configs) {
                if (cfg.position == position) {
                    return cfg;
                }
            }
            return null;
        }

        @SuppressLint("MissingPermission")
        private void openOne(final CameraConfig cfg, final char sideTag) {
            final ImageReader reader = ImageReader.newInstance(
                    DEFAULT_WIDTH,
                    DEFAULT_HEIGHT,
                    ImageFormat.YUV_420_888,
                    IMAGE_BUFFER_SIZE);
            reader.setOnImageAvailableListener(new ImageReader.OnImageAvailableListener() {
                @Override
                public void onImageAvailable(ImageReader r) {
                    Image image = r.acquireLatestImage();
                    if (image == null) {
                        return;
                    }
                    try {
                        if (!closing.contains(cfg.id)) {
                            byte[] jpeg = YuvJpegEncoder.encode(image, JPEG_QUALITY);
                            server.publish(sideTag, image.getWidth(), image.getHeight(), jpeg);
                        }
                    } catch (Exception e) {
                        Log.e(TAG, "encode failed for camera " + cfg.id, e);
                    } finally {
                        image.close();
                    }
                }
            }, imageReaderHandler);

            try {
                cameraManager.openCamera(cfg.id, new CameraDevice.StateCallback() {
                    @Override
                    public void onOpened(CameraDevice device) {
                        try {
                            List<OutputConfiguration> outputs = new ArrayList<>();
                            outputs.add(new OutputConfiguration(reader.getSurface()));
                            device.createCaptureSession(new SessionConfiguration(
                                    SessionConfiguration.SESSION_REGULAR,
                                    outputs,
                                    sessionExecutor,
                                    new CameraCaptureSession.StateCallback() {
                                        @Override
                                        public void onConfigured(CameraCaptureSession session) {
                                            try {
                                                CaptureRequest.Builder req = device.createCaptureRequest(
                                                        CameraDevice.TEMPLATE_PREVIEW);
                                                Surface surface = reader.getSurface();
                                                req.addTarget(surface);
                                                session.setRepeatingRequest(req.build(), null, cameraHandler);
                                                open.put(cfg.id, new OpenCamera(device, session, reader));
                                                Log.i(TAG, "camera " + cfg.id + " (" + sideTag + ") started");
                                            } catch (Exception e) {
                                                Log.e(TAG, "start repeating failed for camera " + cfg.id, e);
                                                closeOne(cfg.id);
                                            }
                                        }

                                        @Override
                                        public void onConfigureFailed(CameraCaptureSession session) {
                                            Log.e(TAG, "capture session configure failed for camera " + cfg.id);
                                            closeOne(cfg.id);
                                        }
                                    }));
                        } catch (Exception e) {
                            Log.e(TAG, "openCamera onOpened failed for " + cfg.id, e);
                            closeOne(cfg.id);
                        }
                    }

                    @Override
                    public void onDisconnected(CameraDevice device) {
                        Log.i(TAG, "camera " + device.getId() + " disconnected");
                        closeOne(device.getId());
                    }

                    @Override
                    public void onError(CameraDevice device, int error) {
                        Log.e(TAG, "camera " + device.getId() + " error " + error);
                        closeOne(device.getId());
                    }
                }, cameraHandler);
            } catch (Exception e) {
                Log.e(TAG, "openCamera failed for " + cfg.id, e);
                reader.close();
            }
        }

        private void closeOne(String cameraId) {
            OpenCamera entry = open.remove(cameraId);
            if (entry == null) {
                return;
            }
            closing.add(cameraId);
            try {
                entry.session.stopRepeating();
            } catch (Exception ignored) {
            }
            try {
                entry.session.close();
            } catch (Exception ignored) {
            }
            try {
                entry.device.close();
            } catch (Exception ignored) {
            }
            try {
                entry.reader.close();
            } catch (Exception ignored) {
            }
            closing.remove(cameraId);
        }

        private static String cameraIdOrNull(CameraConfig cfg) {
            return cfg == null ? "null" : cfg.id;
        }
    }

    private static final class CameraConfig {
        final String id;
        final int position;

        CameraConfig(String id, int position) {
            this.id = id;
            this.position = position;
        }
    }

    private static final class OpenCamera {
        final CameraDevice device;
        final CameraCaptureSession session;
        final ImageReader reader;

        OpenCamera(CameraDevice device, CameraCaptureSession session, ImageReader reader) {
            this.device = device;
            this.session = session;
            this.reader = reader;
        }
    }

    private static final class StreamingServer {
        private final int port;
        private final AtomicBoolean running = new AtomicBoolean(false);
        private final AtomicReference<Socket> currentClient = new AtomicReference<>();
        private ServerSocket serverSocket;
        private Thread acceptThread;

        StreamingServer(int port) {
            this.port = port;
        }

        boolean isRunning() {
            return running.get();
        }

        boolean hasClient() {
            return currentClient.get() != null;
        }

        void start() {
            if (!running.compareAndSet(false, true)) {
                return;
            }
            acceptThread = new Thread(new Runnable() {
                @Override
                public void run() {
                    try {
                        ServerSocket ss = new ServerSocket();
                        ss.setReuseAddress(true);
                        ss.bind(new InetSocketAddress("0.0.0.0", port));
                        serverSocket = ss;
                        Log.i(TAG, "camera server listening on 0.0.0.0:" + port);
                        while (running.get()) {
                            try {
                                Socket client = ss.accept();
                                client.setTcpNoDelay(true);
                                Socket old = currentClient.getAndSet(client);
                                closeQuietly(old);
                                Log.i(TAG, "camera client connected from " + client.getRemoteSocketAddress());
                            } catch (Exception e) {
                                if (running.get()) {
                                    Log.e(TAG, "accept loop failed", e);
                                }
                            }
                        }
                    } catch (Exception e) {
                        Log.e(TAG, "camera server failed", e);
                        running.set(false);
                    }
                }
            }, "questCameraServerAccept");
            acceptThread.setDaemon(true);
            acceptThread.start();
        }

        void stop() {
            if (!running.compareAndSet(true, false)) {
                return;
            }
            closeQuietly(serverSocket);
            serverSocket = null;
            closeQuietly(currentClient.getAndSet(null));
        }

        void publish(char side, int width, int height, byte[] jpeg) {
            Socket client = currentClient.get();
            if (client == null) {
                return;
            }
            try {
                ByteBuffer header = ByteBuffer.allocate(13).order(ByteOrder.BIG_ENDIAN);
                header.put(new byte[] {'Q', 'S', 'T', 'R'});
                header.put((byte) side);
                header.putShort((short) width);
                header.putShort((short) height);
                header.putInt(jpeg.length);
                DataOutputStream out = new DataOutputStream(client.getOutputStream());
                synchronized (client) {
                    out.write(header.array());
                    out.write(jpeg);
                    out.flush();
                }
            } catch (Exception e) {
                Log.e(TAG, "camera write failed, dropping client", e);
                if (currentClient.compareAndSet(client, null)) {
                    closeQuietly(client);
                }
            }
        }

        private static void closeQuietly(Object closeable) {
            if (closeable == null) {
                return;
            }
            try {
                if (closeable instanceof Socket) {
                    ((Socket) closeable).close();
                } else if (closeable instanceof ServerSocket) {
                    ((ServerSocket) closeable).close();
                }
            } catch (Exception ignored) {
            }
        }
    }

    private static final class YuvJpegEncoder {
        private YuvJpegEncoder() {}

        static byte[] encode(Image image, int quality) {
            if (image.getFormat() != ImageFormat.YUV_420_888) {
                throw new IllegalArgumentException("expected YUV_420_888, got " + image.getFormat());
            }
            byte[] nv21 = toNv21(image);
            YuvImage yuv = new YuvImage(nv21, ImageFormat.NV21, image.getWidth(), image.getHeight(), null);
            ByteArrayOutputStream out = new ByteArrayOutputStream(image.getWidth() * image.getHeight() / 2);
            yuv.compressToJpeg(new Rect(0, 0, image.getWidth(), image.getHeight()), quality, out);
            return out.toByteArray();
        }

        private static byte[] toNv21(Image image) {
            int width = image.getWidth();
            int height = image.getHeight();
            int ySize = width * height;
            int uvSize = ySize / 4;
            byte[] nv21 = new byte[ySize + uvSize * 2];

            Image.Plane yPlane = image.getPlanes()[0];
            ByteBuffer yBuffer = yPlane.getBuffer().duplicate();
            int yRowStride = yPlane.getRowStride();
            if (yRowStride == width) {
                yBuffer.get(nv21, 0, ySize);
            } else {
                for (int row = 0; row < height; row++) {
                    yBuffer.position(row * yRowStride);
                    yBuffer.get(nv21, row * width, width);
                }
            }

            Image.Plane uPlane = image.getPlanes()[1];
            Image.Plane vPlane = image.getPlanes()[2];
            ByteBuffer uBuffer = uPlane.getBuffer().duplicate();
            ByteBuffer vBuffer = vPlane.getBuffer().duplicate();
            int uvRowStride = uPlane.getRowStride();
            int uvPixelStride = uPlane.getPixelStride();
            int dst = ySize;
            for (int row = 0; row < height / 2; row++) {
                for (int col = 0; col < width / 2; col++) {
                    int src = row * uvRowStride + col * uvPixelStride;
                    nv21[dst++] = vBuffer.get(src);
                    nv21[dst++] = uBuffer.get(src);
                }
            }
            return nv21;
        }
    }
}
