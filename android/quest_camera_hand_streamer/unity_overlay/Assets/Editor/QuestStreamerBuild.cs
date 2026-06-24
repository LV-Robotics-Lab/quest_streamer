using System;
using System.IO;
using System.Linq;
using UnityEditor;
using UnityEditor.Build;

public static class QuestStreamerBuild
{
    private const string DefaultOutput = "Builds/quest_controller_hand_streamer.apk";
    private const string ApplicationId = "com.rail.oculus.teleop";

    public static void BuildAndroid()
    {
        EditorUserBuildSettings.SwitchActiveBuildTarget(BuildTargetGroup.Android, BuildTarget.Android);
        PlayerSettings.SetApplicationIdentifier(NamedBuildTarget.Android, ApplicationId);
        PlayerSettings.bundleVersion = "1.1.0-combined.1";
        PlayerSettings.Android.bundleVersionCode = 101;

        string output = Environment.GetEnvironmentVariable("QUEST_STREAMER_APK_OUT");
        if (string.IsNullOrWhiteSpace(output))
        {
            output = DefaultOutput;
        }

        string outputDir = Path.GetDirectoryName(output);
        if (!string.IsNullOrEmpty(outputDir))
        {
            Directory.CreateDirectory(outputDir);
        }

        string[] scenes = EditorBuildSettings.scenes
            .Where(scene => scene.enabled)
            .Select(scene => scene.path)
            .ToArray();
        if (scenes.Length == 0)
        {
            throw new InvalidOperationException("No enabled scenes found in EditorBuildSettings.");
        }

        BuildPlayerOptions options = new BuildPlayerOptions
        {
            scenes = scenes,
            locationPathName = output,
            target = BuildTarget.Android,
            options = BuildOptions.None,
        };

        var report = BuildPipeline.BuildPlayer(options);
        if (report.summary.result != UnityEditor.Build.Reporting.BuildResult.Succeeded)
        {
            throw new InvalidOperationException(
                $"Android build failed: {report.summary.result} ({report.summary.totalErrors} errors)");
        }

        UnityEngine.Debug.Log($"quest_streamer combined APK written to {Path.GetFullPath(output)}");
    }
}
