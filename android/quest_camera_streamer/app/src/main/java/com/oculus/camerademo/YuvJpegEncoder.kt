/*
 * YUV_420_888 -> JPEG encoder. Uses android.graphics.YuvImage which only
 * accepts NV21 / YUY2 layouts, so we first linearize the planar
 * YUV_420_888 into NV21 (handling row/pixel strides explicitly; the
 * pixelStride on the U/V planes is almost always 2 on Android and the
 * naive byte-copy in shorter examples produces corrupted chroma).
 */

package com.oculus.camerademo

import android.graphics.ImageFormat
import android.graphics.Rect
import android.graphics.YuvImage
import android.media.Image
import java.io.ByteArrayOutputStream

object YuvJpegEncoder {
    /** Return JPEG bytes for `image`. Caller must `image.close()` afterwards. */
    fun encode(image: Image, quality: Int = 75): ByteArray {
        require(image.format == ImageFormat.YUV_420_888) {
            "Expected YUV_420_888, got ${image.format}"
        }
        val nv21 = toNv21(image)
        val yuv = YuvImage(nv21, ImageFormat.NV21, image.width, image.height, null)
        val out = ByteArrayOutputStream(image.width * image.height / 2)
        yuv.compressToJpeg(Rect(0, 0, image.width, image.height), quality, out)
        return out.toByteArray()
    }

    private fun toNv21(image: Image): ByteArray {
        val w = image.width
        val h = image.height
        val ySize = w * h
        val uvSize = ySize / 4
        val nv21 = ByteArray(ySize + uvSize * 2)

        // Y plane
        val yPlane = image.planes[0]
        val yBuf = yPlane.buffer.duplicate()
        val yRowStride = yPlane.rowStride
        if (yRowStride == w) {
            yBuf.get(nv21, 0, ySize)
        } else {
            for (row in 0 until h) {
                yBuf.position(row * yRowStride)
                yBuf.get(nv21, row * w, w)
            }
        }

        // UV planes -> NV21 layout: V then U, interleaved per-pixel in chroma plane.
        val uPlane = image.planes[1]
        val vPlane = image.planes[2]
        val uBuf = uPlane.buffer.duplicate()
        val vBuf = vPlane.buffer.duplicate()
        val uvRowStride = uPlane.rowStride
        val uvPixelStride = uPlane.pixelStride
        var dst = ySize
        for (row in 0 until h / 2) {
            for (col in 0 until w / 2) {
                val srcIdx = row * uvRowStride + col * uvPixelStride
                nv21[dst++] = vBuf.get(srcIdx)
                nv21[dst++] = uBuf.get(srcIdx)
            }
        }
        return nv21
    }
}
