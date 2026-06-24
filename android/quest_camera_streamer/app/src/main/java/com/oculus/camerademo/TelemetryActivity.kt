package com.oculus.camerademo

import android.app.NativeActivity
import android.content.pm.PackageManager
import android.os.Bundle
import android.widget.Toast

class TelemetryActivity : NativeActivity() {
    private var cameraStreamer: CameraStreamerViewModel? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        startCameraStreamerWhenPermitted()
    }

    override fun onDestroy() {
        cameraStreamer?.shutdown()
        cameraStreamer = null
        super.onDestroy()
    }

    override fun onRequestPermissionsResult(
        requestCode: Int,
        permissions: Array<out String>,
        grantResults: IntArray,
    ) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode != CAMERA_PERMISSION_REQUEST) return

        val resultMap = permissions.zip(grantResults.toTypedArray()).associate {
            it.first to (it.second == PackageManager.PERMISSION_GRANTED)
        }
        cameraStreamer?.onPermissionGranted(resultMap)
        startCameraStreamerWhenPermitted()
    }

    private fun startCameraStreamerWhenPermitted() {
        val missing = PermissionManager.permissions.filter {
            checkSelfPermission(it) != PackageManager.PERMISSION_GRANTED
        }
        if (missing.isNotEmpty()) {
            requestPermissions(missing.toTypedArray(), CAMERA_PERMISSION_REQUEST)
            return
        }

        if (cameraStreamer == null) {
            cameraStreamer = CameraStreamerViewModel(application).apply {
                init()
                startStreaming()
            }
            Toast.makeText(this, "Camera streamer listening on TCP 9100", Toast.LENGTH_SHORT).show()
        }
    }

    private companion object {
        const val CAMERA_PERMISSION_REQUEST = 201
    }
}
