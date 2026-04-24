/*
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 * http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

package com.oculus.camerademo

import java.nio.ByteBuffer

fun getBrigthness(yPlane: ByteBuffer, width: Int, height: Int): Int {
  var sumY = 0

  // Iterate over each pixel in the Y data
  for (i in 0 until height) {
    for (j in 0 until width) {
      // Calculate the index of the current pixel in the Y data
      var idx = (i * width) + j
      sumY += yPlane[idx]
    }
  }

  return sumY / (width * height)
}
