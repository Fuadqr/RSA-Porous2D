import QtQuick
import QtQuick.Controls

Button {
    id: control

    property string variant: "normal"

    implicitHeight: variant === "primary" ? 40 : 36
    implicitWidth: Math.max(112, label.implicitWidth + 28)
    hoverEnabled: true

    contentItem: Text {
        id: label
        text: control.text
        horizontalAlignment: Text.AlignHCenter
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
        font.pixelSize: 15
        font.weight: variant === "primary" ? Font.DemiBold : Font.Medium
        color: {
            if (!control.enabled)
                return variant === "primary" ? "#e6e8ec" : "#a5afbd"
            if (variant === "danger")
                return "#b42318"
            if (variant === "primary")
                return "#ffffff"
            return "#293241"
        }
    }

    background: Rectangle {
        radius: 8
        border.width: variant === "primary" ? 0 : 1
        border.color: control.enabled
                      ? (variant === "danger" ? "#f1b7b1" : "#d8e0ea")
                      : "#e7ecf2"
        color: {
            if (!control.enabled)
                return variant === "primary" ? "#c2c7cf" : "#f3f6fa"
            if (variant === "primary")
                return control.down ? "#374151" : (control.hovered ? "#374151" : "#374151")
            if (variant === "danger")
                return control.down ? "#fee4e2" : (control.hovered ? "#fff1f0" : "#ffffff")
            return control.down ? "#edf1f6" : (control.hovered ? "#f7f9fc" : "#ffffff")
        }

        Behavior on color {
            ColorAnimation { duration: 120 }
        }
    }
}
