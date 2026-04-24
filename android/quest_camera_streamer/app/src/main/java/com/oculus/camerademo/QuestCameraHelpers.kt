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

import android.hardware.camera2.CameraCharacteristics.Key

/** These helpers demonstrate Quest-specific APIs required to access a Passthrough camera */
const val KEY_CAMERA_POSITION = "com.meta.extra_metadata.position"
const val KEY_CAMERA_SOURCE = "com.meta.extra_metadata.camera_source"

/**
 * Use this custom CameraCharacterics.Key to obtain camera position. Camera position can be Left or
 * Right (see [com.oculus.camerademo.Position])
 */
val KEY_POSITION = Key(KEY_CAMERA_POSITION, Int::class.java)

/**
 * Use this custom CameraCharacterics.Key to obtain camera source. Source value for the Passthrough
 * camera is 0
 */
val KEY_SOURCE = Key(KEY_CAMERA_SOURCE, Int::class.java)
const val CAMERA_SOURCE_PASSTHROUGH = 0
