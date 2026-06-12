import QtQuick
import QtQuick.Layouts

Item {
    id: root

    property string caption: ""
    property real value: 0.0
    property bool busy: false
    property string detail: ""

    implicitHeight: 28
    Layout.fillWidth: true

    RowLayout {
        anchors.fill: parent
        spacing: 10

        Text {
            text: root.caption
            Layout.preferredWidth: 96
            horizontalAlignment: Text.AlignLeft
            verticalAlignment: Text.AlignVCenter
            font.pixelSize: 14
            font.weight: Font.DemiBold
            color: "#697386"
            elide: Text.ElideRight
        }

        Rectangle {
            id: track
            Layout.fillWidth: true
            Layout.preferredHeight: 9
            radius: 5
            color: "#e6ebf2"
            clip: true

            Rectangle {
                id: fill
                visible: !root.busy
                anchors.left: parent.left
                anchors.top: parent.top
                anchors.bottom: parent.bottom
                width: Math.max(0, Math.min(1, root.value)) * parent.width
                radius: 5
                color: "#2f6fed"

                Behavior on width {
                    NumberAnimation { duration: 160; easing.type: Easing.OutCubic }
                }
            }

            Rectangle {
                id: busyBlock
                visible: root.busy
                width: Math.max(44, track.width * 0.28)
                height: track.height
                radius: 5
                color: "#2f6fed"
                opacity: 0.72

                NumberAnimation on x {
                    from: -busyBlock.width
                    to: track.width
                    duration: 900
                    loops: Animation.Infinite
                    running: root.busy
                }
            }
        }

        Text {
            visible: root.detail.length > 0
            text: root.detail
            verticalAlignment: Text.AlignVCenter
            font.pixelSize: 12
            font.family: "Consolas"
            color: "#526070"
        }
    }
}
