/*
 * Minimal UI: pick a StreamMode, tap Start/Stop, see server status.
 */

package com.oculus.camerademo

import android.content.Intent
import android.graphics.SurfaceTexture
import android.os.Bundle
import android.view.Surface
import android.view.TextureView
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.RowScope
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.lifecycle.ViewModelProvider
import com.oculus.camerademo.ui.theme.CameraDemoTheme

class MainActivity : ComponentActivity() {
    private val viewModel by lazy {
        ViewModelProvider.AndroidViewModelFactory(application)
            .create(CameraStreamerViewModel::class.java)
    }

    private val requestPermissionLauncher =
        registerForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) {
                requestResults ->
            viewModel.onPermissionGranted(requestResults)
        }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            CameraDemoTheme {
                Scaffold(modifier = Modifier.fillMaxSize()) { innerPadding ->
                    StreamerUi(
                        modifier = Modifier.fillMaxSize().padding(innerPadding),
                    )
                }
            }
        }

        viewModel.cameraEvents.observe(this) { event ->
            if (event is CameraEvent.NotificationEvent) {
                Toast.makeText(this, event.message, Toast.LENGTH_SHORT).show()
                viewModel.onHandleCameraEvent()
            }
        }
    }

    override fun onResume() {
        super.onResume()
        viewModel.permissionRequestState.observe(this) { permissionState ->
            val need = mutableListOf<String>()
            if (!permissionState.nativeCameraPermissionGranted) {
                need.add(PermissionManager.ANDROID_CAMERA_PERMISSION)
            }
            if (!permissionState.vendorCameraPermissionGranted) {
                need.add(PermissionManager.HZOS_CAMERA_PERMISSION)
            }
            if (need.isNotEmpty()) {
                requestPermissionLauncher.launch(need.toTypedArray())
            } else {
                viewModel.init()
            }
        }
        viewModel.onResume()
    }

    override fun onPause() {
        super.onPause()
        viewModel.onPause()
    }

    override fun onStop() {
        super.onStop()
        // Intentionally do NOT shutdown here. HMD dismount pushes the
        // Activity to onStop on Quest, and we want the TCP server + camera
        // to stay alive so a quick "take off, test from PC, put back on"
        // cycle doesn't need to re-press Start. Real shutdown only happens
        // on the Exit button or onDestroy.
    }

    override fun onDestroy() {
        super.onDestroy()
        viewModel.shutdown()
    }

    @Composable
    fun StreamerUi(modifier: Modifier = Modifier) {
        // We show a simple state-machine-ish status; toast events carry richer
        // messages. Recomposition is triggered when any of the buttons below
        // flip `modeSelection`, which is frequent enough for a minimal UI.
        val status = if (viewModel.isStreaming) {
            if (viewModel.hasClient) "streaming (client connected)"
            else "streaming (no client yet)"
        } else {
            "idle"
        }

        Column(
            modifier = modifier.padding(24.dp),
            verticalArrangement = Arrangement.Center,
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            Text("quest_camera_streamer", modifier = Modifier.padding(bottom = 16.dp))
            Text("TCP 0.0.0.0:${viewModel.serverPort}  (stereo mode)",
                modifier = Modifier.padding(bottom = 8.dp))

            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Button(onClick = { viewModel.startStreaming() }) { Text("Start streaming") }
                Button(onClick = { viewModel.stopStreaming() }) { Text("Stop streaming") }
                Button(onClick = { startActivity(Intent(this@MainActivity, TelemetryActivity::class.java)) }) {
                    Text("Start XR telemetry")
                }
                Button(onClick = { viewModel.shutdown(); finish() }) { Text("Exit") }
            }

            Spacer(Modifier.height(16.dp))
            Text(status)

            Spacer(Modifier.height(16.dp))

            // Two equal-width preview boxes. Force 4:3 aspect ratio so both
            // panes match the actual 1280x960 camera output regardless of
            // how Compose lays out the label text above.
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                CameraPreviewBox(sideTag = 'L')
                CameraPreviewBox(sideTag = 'R')
            }
        }
    }

    @Composable
    private fun RowScope.CameraPreviewBox(sideTag: Char) {
        Column(
            modifier = Modifier.weight(1f),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            Text(sideTag.toString())
            Box(
                modifier = Modifier
                    .fillMaxWidth()
                    .aspectRatio(4f / 3f),  // match 1280x960 capture
            ) {
                AndroidView(
                    modifier = Modifier.fillMaxSize(),
                    factory = { ctx ->
                        TextureView(ctx).apply {
                            surfaceTextureListener = object : TextureView.SurfaceTextureListener {
                                override fun onSurfaceTextureAvailable(
                                    st: SurfaceTexture, w: Int, h: Int,
                                ) {
                                    // Pin the producer buffer size so both
                                    // eyes render at an identical resolution.
                                    st.setDefaultBufferSize(1280, 960)
                                    viewModel.registerPreviewSurface(sideTag, Surface(st))
                                }
                                override fun onSurfaceTextureSizeChanged(
                                    st: SurfaceTexture, w: Int, h: Int,
                                ) {
                                    st.setDefaultBufferSize(1280, 960)
                                }
                                override fun onSurfaceTextureDestroyed(st: SurfaceTexture): Boolean {
                                    viewModel.registerPreviewSurface(sideTag, null)
                                    return true
                                }
                                override fun onSurfaceTextureUpdated(st: SurfaceTexture) {}
                            }
                        }
                    },
                )
            }
        }
    }
}
