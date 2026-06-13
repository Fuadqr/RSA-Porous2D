import QtQuick
import QtQuick.Layouts

// A titled parameter card for the single-board layout. Hosts one or more
// SectionBlocks (a subset of one backend tab's sections) in a compact
// multi-column grid.
Rectangle {
    id: root

    property var backend
    property var formValues: ({})
    property var controller
    property string title: ""
    property string accent: "#374151"
    property string hint: ""
    property var sections: []
    property int columns: 2
    property string contextTitle: title

    Layout.fillWidth: true
    implicitHeight: content.implicitHeight + 22
    radius: 10
    color: "#ffffff"
    border.width: 1
    border.color: "#e1e7f0"

    ColumnLayout {
        id: content
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.margins: 11
        spacing: 7

        RowLayout {
            spacing: 7
            Layout.fillWidth: true

            Rectangle {
                width: 8
                height: 8
                radius: 4
                color: root.accent
            }

            Text {
                text: root.title
                Layout.fillWidth: true
                font.pixelSize: 13
                font.weight: Font.Bold
                color: "#243042"
                elide: Text.ElideRight
            }
        }

        Text {
            visible: root.hint.length > 0
            text: root.hint
            Layout.fillWidth: true
            Layout.preferredHeight: visible ? implicitHeight : 0
            font.pixelSize: 11
            color: "#9aa4b5"
            wrapMode: Text.WordWrap
        }

        Repeater {
            model: root.sections

            delegate: SectionBlock {
                backend: root.backend
                sectionData: modelData
                formValues: root.formValues
                controller: root.controller
                contextTitle: root.contextTitle
                columns: root.columns
                showCaption: root.sections.length > 1
            }
        }
    }
}
