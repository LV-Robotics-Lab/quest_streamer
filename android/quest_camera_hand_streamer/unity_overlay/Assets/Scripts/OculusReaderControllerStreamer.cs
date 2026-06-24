using System.Globalization;
using System.Text;
using UnityEngine;

public sealed class OculusReaderControllerStreamer : MonoBehaviour
{
    private const string LogMarker = "wE9ryARX";
    private const float DefaultFrequencySeconds = 1.0f / 60.0f;

    private readonly StringBuilder _transforms = new StringBuilder(512);
    private readonly StringBuilder _buttons = new StringBuilder(256);
    private float _timer;

    [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.AfterSceneLoad)]
    private static void Install()
    {
        GameObject go = new GameObject("OculusReaderControllerStreamer");
        DontDestroyOnLoad(go);
        go.AddComponent<OculusReaderControllerStreamer>();
    }

    private void Update()
    {
        _timer += Time.unscaledDeltaTime;
        if (_timer < DefaultFrequencySeconds)
        {
            return;
        }
        _timer = 0f;

        OVRInput.Controller connected = OVRInput.GetConnectedControllers();
        bool hasLeft = (connected & OVRInput.Controller.LTouch) != 0;
        bool hasRight = (connected & OVRInput.Controller.RTouch) != 0;
        if (!hasLeft && !hasRight)
        {
            return;
        }

        _transforms.Length = 0;
        _buttons.Length = 0;

        bool first = true;
        if (hasLeft)
        {
            AppendController('l', OVRInput.Controller.LTouch, first);
            first = false;
        }
        if (hasRight)
        {
            AppendController('r', OVRInput.Controller.RTouch, first);
        }

        Debug.Log($"{LogMarker}: {_transforms}&{_buttons}");
    }

    private void AppendController(char side, OVRInput.Controller controller, bool first)
    {
        if (!first)
        {
            _transforms.Append('|');
            _buttons.Append(',');
        }

        Matrix4x4 localFromHead = HeadPoseMatrix().inverse * ControllerPoseMatrix(controller);
        _transforms.Append(side).Append(':');
        AppendMatrix(_transforms, localFromHead);
        AppendButtons(_buttons, side);
    }

    private static Matrix4x4 ControllerPoseMatrix(OVRInput.Controller controller)
    {
        Vector3 position = OVRInput.GetLocalControllerPosition(controller);
        Quaternion rotation = OVRInput.GetLocalControllerRotation(controller);
        return Matrix4x4.TRS(position, rotation, Vector3.one);
    }

    private static Matrix4x4 HeadPoseMatrix()
    {
        Camera cam = Camera.main;
        if (cam == null)
        {
            return Matrix4x4.identity;
        }
        Transform t = cam.transform;
        return Matrix4x4.TRS(t.localPosition, t.localRotation, Vector3.one);
    }

    private static void AppendMatrix(StringBuilder sb, Matrix4x4 matrix)
    {
        for (int row = 0; row < 4; row++)
        {
            for (int col = 0; col < 4; col++)
            {
                if (row != 0 || col != 0)
                {
                    sb.Append(' ');
                }
                sb.Append(matrix[row, col].ToString("F6", CultureInfo.InvariantCulture));
            }
        }
    }

    private static void AppendButtons(StringBuilder sb, char side)
    {
        if (side == 'r')
        {
            sb.Append("R,");
            AppendIf(sb, OVRInput.Get(OVRInput.RawButton.A), "A");
            AppendIf(sb, OVRInput.Get(OVRInput.RawButton.B), "B");
            AppendIf(sb, OVRInput.Get(OVRInput.RawButton.RIndexTrigger), "RTr");
            AppendIf(sb, OVRInput.Get(OVRInput.RawButton.RHandTrigger), "RG");
            AppendIf(sb, OVRInput.Get(OVRInput.RawTouch.RThumbRest), "RThU");
            AppendIf(sb, OVRInput.Get(OVRInput.RawButton.RThumbstick), "RJ");
            AppendAxis2D(sb, "rightJS", OVRInput.Get(OVRInput.RawAxis2D.RThumbstick));
            AppendAxis1D(sb, "rightTrig", OVRInput.Get(OVRInput.RawAxis1D.RIndexTrigger));
            AppendAxis1D(sb, "rightGrip", OVRInput.Get(OVRInput.RawAxis1D.RHandTrigger), trailingComma: false);
        }
        else
        {
            sb.Append("L,");
            AppendIf(sb, OVRInput.Get(OVRInput.RawButton.X), "X");
            AppendIf(sb, OVRInput.Get(OVRInput.RawButton.Y), "Y");
            AppendIf(sb, OVRInput.Get(OVRInput.RawButton.LIndexTrigger), "LTr");
            AppendIf(sb, OVRInput.Get(OVRInput.RawButton.LHandTrigger), "LG");
            AppendIf(sb, OVRInput.Get(OVRInput.RawTouch.LThumbRest), "LThU");
            AppendIf(sb, OVRInput.Get(OVRInput.RawButton.LThumbstick), "LJ");
            AppendAxis2D(sb, "leftJS", OVRInput.Get(OVRInput.RawAxis2D.LThumbstick));
            AppendAxis1D(sb, "leftTrig", OVRInput.Get(OVRInput.RawAxis1D.LIndexTrigger));
            AppendAxis1D(sb, "leftGrip", OVRInput.Get(OVRInput.RawAxis1D.LHandTrigger), trailingComma: false);
        }
    }

    private static void AppendIf(StringBuilder sb, bool enabled, string token)
    {
        if (enabled)
        {
            sb.Append(token).Append(',');
        }
    }

    private static void AppendAxis2D(StringBuilder sb, string key, Vector2 value)
    {
        sb.Append(key)
            .Append(' ')
            .Append(value.x.ToString("F6", CultureInfo.InvariantCulture))
            .Append(' ')
            .Append(value.y.ToString("F6", CultureInfo.InvariantCulture))
            .Append(',');
    }

    private static void AppendAxis1D(
        StringBuilder sb,
        string key,
        float value,
        bool trailingComma = true
    )
    {
        sb.Append(key)
            .Append(' ')
            .Append(value.ToString("F6", CultureInfo.InvariantCulture));
        if (trailingComma)
        {
            sb.Append(',');
        }
    }
}
