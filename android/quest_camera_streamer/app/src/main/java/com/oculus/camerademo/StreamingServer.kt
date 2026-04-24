/*
 * TCP server that accepts a single client and publishes tagged JPEG frames.
 *
 * Wire format (big-endian), one message per frame:
 *
 *     [4 bytes magic "QSTR"]
 *     [1 byte side:  'L' | 'R' | 'W']   (W = wide stereo-packed)
 *     [2 bytes width, 2 bytes height]
 *     [4 bytes JPEG byte length]
 *     [JPEG bytes]
 *
 * Bound to 0.0.0.0 so that both:
 *   - `adb reverse tcp:9100 tcp:9100` (wired) and
 *   - direct WiFi connection to the headset's LAN IP
 * work against the same socket.
 *
 * Single-client: a new connection displaces any previous one (old socket closed).
 * Intended for low-friction PC-side prototyping, not production multicast.
 */

package com.oculus.camerademo

import java.io.DataOutputStream
import java.net.InetSocketAddress
import java.net.ServerSocket
import java.net.Socket
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicReference

class StreamingServer(val port: Int) {
    private val running = AtomicBoolean(false)
    private val currentClient = AtomicReference<Socket?>(null)
    private var serverSocket: ServerSocket? = null
    private var acceptThread: Thread? = null

    val isRunning: Boolean get() = running.get()
    val hasClient: Boolean get() = currentClient.get() != null

    fun start() {
        if (!running.compareAndSet(false, true)) return
        // `ServerSocket.bind()` is blocking network I/O; Android forbids that
        // on the main thread, so we do both bind and accept on a worker.
        acceptThread = Thread({
            try {
                val ss = ServerSocket().apply {
                    reuseAddress = true
                    bind(InetSocketAddress("0.0.0.0", port))
                }
                serverSocket = ss
                logv("StreamingServer: listening on 0.0.0.0:$port")
                while (running.get()) {
                    try {
                        val client = ss.accept()
                        client.tcpNoDelay = true
                        val old = currentClient.getAndSet(client)
                        old?.runCatching { close() }
                        logv("StreamingServer: client connected from ${client.remoteSocketAddress}")
                    } catch (e: Exception) {
                        if (running.get()) loge("StreamingServer: accept loop: $e")
                        else break
                    }
                }
            } catch (e: Exception) {
                loge("StreamingServer: server thread failed: $e")
                running.set(false)
            }
        }, "StreamingServer-accept").apply { isDaemon = true; start() }
    }

    fun stop() {
        if (!running.compareAndSet(true, false)) return
        try { serverSocket?.close() } catch (_: Exception) {}
        serverSocket = null
        currentClient.getAndSet(null)?.runCatching { close() }
        logv("StreamingServer: stopped")
    }

    /**
     * Publish one JPEG frame to the currently-connected client. No-op if no
     * client is connected or the write fails (in which case the client slot
     * is cleared, so the next accepted connection becomes active).
     */
    fun publish(side: Char, width: Int, height: Int, jpeg: ByteArray) {
        val client = currentClient.get() ?: return
        try {
            val header = ByteBuffer.allocate(13).order(ByteOrder.BIG_ENDIAN)
            header.put("QSTR".toByteArray(Charsets.US_ASCII))
            header.put(side.code.toByte())
            header.putShort(width.toShort())
            header.putShort(height.toShort())
            header.putInt(jpeg.size)
            val out = DataOutputStream(client.getOutputStream())
            synchronized(out) {
                out.write(header.array())
                out.write(jpeg)
                out.flush()
            }
        } catch (e: Exception) {
            loge("StreamingServer: write failed, dropping client: $e")
            if (currentClient.compareAndSet(client, null)) {
                client.runCatching { close() }
            }
        }
    }

}
