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

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager

class PermissionManager {
  companion object {
    const val ANDROID_CAMERA_PERMISSION = Manifest.permission.CAMERA
    const val HZOS_CAMERA_PERMISSION = "horizonos.permission.HEADSET_CAMERA"

    val permissions = arrayOf(ANDROID_CAMERA_PERMISSION, HZOS_CAMERA_PERMISSION)
  }

  /** Checks that all mandatory permissions are granted */
  fun checkPermissions(context: Context, vararg permissions: String): Boolean {
    for (permission in permissions) {
      if (context.checkSelfPermission(permission) != PackageManager.PERMISSION_GRANTED) {
        return false
      }
    }
    return true
  }
}
