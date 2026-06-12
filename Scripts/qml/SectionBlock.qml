import QtQuick
import QtQuick.Layouts

// One form section (caption + grid of compact FieldCells) for the
// single-board layout. Visibility follows the same backend rules as the
// tabbed layout (isSectionVisible / isFieldVisible, re-evaluated on
// formRevision), so conditional sections collapse in place.
ColumnLayout {
    id: root

    property var backend
    property var sectionData
    property var formValues: ({})
    property var controller
    property string contextTitle: ""
    property int columns: 2
    property bool showCaption: true

    spacing: 4
    visible: backend.formRevision >= 0 && backend.isSectionVisible(sectionData.title)
    Layout.fillWidth: true
    Layout.preferredHeight: visible ? implicitHeight : 0

    function isWide(field) {
        if (field.placeholder && field.placeholder.length > 0)
            return true
        return field.name === "out_dir"
    }

    Text {
        visible: root.showCaption
        text: sectionData.title
        Layout.fillWidth: true
        font.pixelSize: 11
        font.weight: Font.Bold
        color: "#9aa4b5"
        elide: Text.ElideRight
    }

    GridLayout {
        Layout.fillWidth: true
        columns: root.columns
        columnSpacing: 10
        rowSpacing: 4

        Repeater {
            model: sectionData.fields

            delegate: FieldCell {
                backend: root.backend
                fieldData: modelData
                formValues: root.formValues
                controller: root.controller
                contextTitle: root.contextTitle
                visible: root.backend.formRevision >= 0 && root.backend.isFieldVisible(modelData.name)
                Layout.fillWidth: true
                Layout.preferredWidth: 10
                Layout.columnSpan: root.isWide(modelData) ? root.columns : 1
                Layout.preferredHeight: visible ? implicitHeight : 0
            }
        }
    }
}
