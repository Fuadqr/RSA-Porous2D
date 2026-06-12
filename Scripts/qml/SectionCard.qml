import QtQuick
import QtQuick.Layouts

Rectangle {
    id: root

    property var backend
    property var sectionData
    property var formValues: ({})
    property var controller

    Layout.fillWidth: true
    Layout.preferredHeight: visible ? implicitHeight : 0
    visible: backend.formRevision >= 0 && backend.isSectionVisible(sectionData.title)
    implicitHeight: content.implicitHeight + 24
    radius: 8
    color: "#ffffff"
    border.width: 1
    border.color: "#dfe6ef"

    ColumnLayout {
        id: content
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.margins: 12
        spacing: 9

        Text {
            text: sectionData.title.toUpperCase()
            Layout.fillWidth: true
            font.pixelSize: 13
            font.weight: Font.Bold
            color: "#7a8699"
            elide: Text.ElideRight
        }

        Repeater {
            model: sectionData.fields

            delegate: FieldRow {
                backend: root.backend
                fieldData: modelData
                formValues: root.formValues
                controller: root.controller
                visible: root.backend.formRevision >= 0 && root.backend.isFieldVisible(modelData.name)
                Layout.preferredHeight: visible ? implicitHeight : 0
            }
        }
    }
}
