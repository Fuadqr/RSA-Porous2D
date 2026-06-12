import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// Compact field editor: label above a short input. Same value plumbing as
// FieldRow (controller.updateFieldValue / backend.set*Field, debounced commit),
// plus a focus notification so the preview pane can follow the card being
// edited. Used by the single-board layout (Main.qml).
Item {
    id: root

    objectName: fieldData && fieldData.name ? "fieldCell_" + fieldData.name : "fieldCell"

    property var backend
    property var fieldData
    property var formValues: ({})
    property var controller
    property string contextTitle: ""
    property var editorValue: fieldData && fieldData.value !== undefined ? fieldData.value : ""

    implicitHeight: 46
    Layout.fillWidth: true

    function currentValue() {
        var values = root.formValues
        if (values && values[fieldData.name] !== undefined && values[fieldData.name] !== null)
            return values[fieldData.name]
        values = backend.fieldValues
        if (values && values[fieldData.name] !== undefined && values[fieldData.name] !== null)
            return values[fieldData.name]
        return fieldData.value
    }

    function pushValue(value) {
        root.editorValue = value
        if (root.controller && root.controller.updateFieldValue)
            root.controller.updateFieldValue(root.fieldData.name, value, root.fieldData.type)
        else if (root.fieldData.type === "bool")
            root.backend.setBoolField(root.fieldData.name, Boolean(value))
        else
            root.backend.setTextField(root.fieldData.name, String(value))
    }

    function syncValue() {
        editorValue = currentValue()
    }

    function optionIndexFor(value) {
        var textValue = String(value)
        var values = fieldData.values || fieldData.options
        for (var i = 0; i < values.length; ++i) {
            if (String(values[i]) === textValue)
                return i
        }
        return 0
    }

    function optionValueFor(index) {
        var values = fieldData.values || fieldData.options
        if (index >= 0 && index < values.length)
            return values[index]
        return fieldData.options[index]
    }

    function notifyFocus() {
        if (root.controller && root.controller.focusContext)
            root.controller.focusContext(root.contextTitle)
    }

    Component.onCompleted: syncValue()

    Connections {
        target: root.backend
        function onFormRevisionChanged() {
            root.syncValue()
        }
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: 3

        Text {
            text: root.fieldData.label
            Layout.fillWidth: true
            font.pixelSize: 11
            font.weight: Font.DemiBold
            color: "#71809a"
            elide: Text.ElideRight
        }

        Loader {
            Layout.fillWidth: true
            Layout.preferredHeight: 28
            sourceComponent: {
                if (fieldData.type === "bool")
                    return boolEditor
                if (fieldData.type === "combo")
                    return comboEditor
                return textEditor
            }
        }
    }

    Component {
        id: textEditor

        Rectangle {
            implicitHeight: 28
            radius: 6
            border.width: 1
            border.color: textInput.activeFocus ? "#2f6fed" : (hoverHandler.hovered ? "#c8d3df" : "#dce4ee")
            color: "#f8fafc"

            function commitVisibleText() {
                var backendValue = root.currentValue()
                var backendText = String(backendValue === undefined || backendValue === null ? "" : backendValue)
                if (textInput.text !== backendText) {
                    root.pushValue(textInput.text)
                }
            }

            HoverHandler { id: hoverHandler }

            Timer {
                interval: 150
                repeat: true
                running: root.visible
                onTriggered: parent.commitVisibleText()
            }

            TextInput {
                id: textInput
                objectName: "input_" + root.fieldData.name
                anchors.fill: parent
                anchors.leftMargin: 8
                anchors.rightMargin: 8
                verticalAlignment: TextInput.AlignVCenter
                text: String(root.editorValue === undefined || root.editorValue === null ? "" : root.editorValue)
                selectByMouse: true
                clip: true
                font.pixelSize: 13
                color: "#202939"
                selectedTextColor: "#ffffff"
                selectionColor: "#2f6fed"
                onTextChanged: parent.commitVisibleText()
                onActiveFocusChanged: {
                    if (activeFocus)
                        root.notifyFocus()
                    else
                        parent.commitVisibleText()
                }
                Keys.onReturnPressed: { parent.commitVisibleText(); focus = false }
                Keys.onEnterPressed: { parent.commitVisibleText(); focus = false }
            }

            Text {
                anchors.left: textInput.left
                anchors.right: textInput.right
                anchors.verticalCenter: parent.verticalCenter
                text: root.fieldData.placeholder
                visible: textInput.text.length === 0 && root.fieldData.placeholder.length > 0 && !textInput.activeFocus
                font.pixelSize: 12
                color: "#98a2b3"
                elide: Text.ElideRight
            }
        }
    }

    Component {
        id: comboEditor

        ComboBox {
            id: combo
            objectName: "combo_" + root.fieldData.name
            model: root.fieldData.options
            currentIndex: root.optionIndexFor(root.editorValue)
            font.pixelSize: 13
            implicitHeight: 28
            onActivated: {
                root.pushValue(root.optionValueFor(currentIndex))
            }
            onActiveFocusChanged: {
                if (activeFocus)
                    root.notifyFocus()
            }

            contentItem: Text {
                text: combo.displayText
                color: "#202939"
                verticalAlignment: Text.AlignVCenter
                leftPadding: 8
                rightPadding: 24
                elide: Text.ElideRight
                font.pixelSize: 13
            }

            background: Rectangle {
                radius: 6
                border.width: 1
                border.color: parent.activeFocus ? "#2f6fed" : (parent.hovered ? "#c8d3df" : "#dce4ee")
                color: "#f8fafc"
            }
        }
    }

    Component {
        id: boolEditor

        Item {
            implicitHeight: 28

            Switch {
                objectName: "switch_" + root.fieldData.name
                anchors.left: parent.left
                anchors.verticalCenter: parent.verticalCenter
                checked: Boolean(root.editorValue)
                scale: 0.85
                transformOrigin: Item.Left
                onToggled: {
                    root.notifyFocus()
                    root.pushValue(checked)
                }
            }
        }
    }
}
