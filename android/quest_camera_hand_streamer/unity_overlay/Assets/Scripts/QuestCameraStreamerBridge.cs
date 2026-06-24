using System;
using UnityEngine;

public static class QuestCameraStreamerBridge
{
    private const string PluginClassName = "com.queststreamer.camera.QuestCameraStreamer";
    public const int DefaultPort = 9100;

    public static bool Start(int port = DefaultPort)
    {
#if UNITY_ANDROID && !UNITY_EDITOR
        try
        {
            using (AndroidJavaClass unityPlayer = new AndroidJavaClass("com.unity3d.player.UnityPlayer"))
            using (AndroidJavaObject activity =
                   unityPlayer.GetStatic<AndroidJavaObject>("currentActivity"))
            using (AndroidJavaClass plugin = new AndroidJavaClass(PluginClassName))
            {
                bool ok = plugin.CallStatic<bool>("start", activity, port);
                Debug.Log($"[QuestCameraStreamer] start port={port} ok={ok}");
                return ok;
            }
        }
        catch (Exception ex)
        {
            Debug.LogError($"[QuestCameraStreamer] start failed: {ex.Message}");
            return false;
        }
#else
        Debug.Log("[QuestCameraStreamer] Android plugin unavailable outside Android player");
        return false;
#endif
    }

    public static void Stop()
    {
#if UNITY_ANDROID && !UNITY_EDITOR
        try
        {
            using (AndroidJavaClass plugin = new AndroidJavaClass(PluginClassName))
            {
                plugin.CallStatic("stop");
                Debug.Log("[QuestCameraStreamer] stopped");
            }
        }
        catch (Exception ex)
        {
            Debug.LogError($"[QuestCameraStreamer] stop failed: {ex.Message}");
        }
#endif
    }

    public static bool IsRunning()
    {
#if UNITY_ANDROID && !UNITY_EDITOR
        try
        {
            using (AndroidJavaClass plugin = new AndroidJavaClass(PluginClassName))
            {
                return plugin.CallStatic<bool>("isRunning");
            }
        }
        catch (Exception ex)
        {
            Debug.LogError($"[QuestCameraStreamer] isRunning failed: {ex.Message}");
            return false;
        }
#else
        return false;
#endif
    }
}
