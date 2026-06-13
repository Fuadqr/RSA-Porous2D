import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Rectangle {
    id: root

    property url source
    property string placeholder: "Preview"
    property real zoom: 1.0

    radius: 8
    color: "#ffffff"
    border.width: 1
    border.color: "#dfe6ef"
    clip: true

    Flickable {
        id: flick
        anchors.fill: parent
        anchors.margins: 14
        clip: true
        contentWidth: Math.max(width, figure.width + 28)
        contentHeight: Math.max(height, figure.height + 28)
        visible: root.source != ""

        Image {
            id: figure
            source: root.source
            fillMode: Image.PreserveAspectFit
            asynchronous: true
            cache: false
            smooth: true
            mipmap: true
            width: Math.max(1, flick.width) * root.zoom
            height: Math.max(1, flick.height) * root.zoom
            x: Math.max(0, (flick.contentWidth - width) / 2)
            y: Math.max(0, (flick.contentHeight - height) / 2)
        }

        WheelHandler {
            target: null
            acceptedDevices: PointerDevice.Mouse | PointerDevice.TouchPad
            onWheel: function(event) {
                var step = event.angleDelta.y > 0 ? 1.10 : 0.90
                root.zoom = Math.max(0.5, Math.min(4.0, root.zoom * step))
            }
        }
    }

    Text {
        anchors.centerIn: parent
        text: root.placeholder
        visible: root.source == ""
        font.pixelSize: 18
        font.weight: Font.DemiBold
        color: "#98a2b3"
    }

    Rectangle {
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.margins: 14
        visible: root.source != ""
        radius: 8
        color: "#f8fafc"
        border.width: 1
        border.color: "#d8e0ea"
        implicitWidth: zoomRow.implicitWidth + 10
        implicitHeight: zoomRow.implicitHeight + 8

        RowLayout {
            id: zoomRow
            anchors.centerIn: parent
            spacing: 2

            ToolButton {
                text: "-"
                implicitWidth: 28
                implicitHeight: 26
                onClicked: root.zoom = Math.max(0.5, root.zoom / 1.2)
            }

            Text {
                text: Math.round(root.zoom * 100) + "%"
                Layout.preferredWidth: 42
                horizontalAlignment: Text.AlignHCenter
                verticalAlignment: Text.AlignVCenter
                font.pixelSize: 13
                font.weight: Font.DemiBold
                color: "#526070"
            }

            ToolButton {
                text: "+"
                implicitWidth: 28
                implicitHeight: 26
                onClicked: root.zoom = Math.min(4.0, root.zoom * 1.2)
            }

            ToolButton {
                text: "Reset"
                implicitWidth: 54
                implicitHeight: 26
                onClicked: root.zoom = 1.0
            }
        }
    }
}
