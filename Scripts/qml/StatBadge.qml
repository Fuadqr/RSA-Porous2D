import QtQuick

Rectangle {
    id: root

    property string text: "-"
    property string kind: "idle"

    implicitHeight: 32
    implicitWidth: Math.max(72, badgeText.implicitWidth + 24)
    radius: 14
    color: {
        if (kind === "ok")
            return "#dcfce7"
        if (kind === "warn")
            return "#fef3c7"
        if (kind === "bad")
            return "#fee2e2"
        if (kind === "run")
            return "#e6e8ec"
        return "#e8edf4"
    }

    border.width: 1
    border.color: {
        if (kind === "ok")
            return "#bbf7d0"
        if (kind === "warn")
            return "#fde68a"
        if (kind === "bad")
            return "#fecaca"
        if (kind === "run")
            return "#cbd2da"
        return "#d8e0ea"
    }

    Text {
        id: badgeText
        anchors.centerIn: parent
        anchors.margins: 10
        text: root.text
        font.pixelSize: 14
        font.weight: Font.DemiBold
        color: {
            if (kind === "ok")
                return "#166534"
            if (kind === "warn")
                return "#92400e"
            if (kind === "bad")
                return "#991b1b"
            if (kind === "run")
                return "#374151"
            return "#667085"
        }
        elide: Text.ElideRight
    }

    Behavior on color {
        ColorAnimation { duration: 160 }
    }
}
