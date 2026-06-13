import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: root

    objectName: fieldData && fieldData.name ? "fieldRow_" + fieldData.name : "fieldRow"

    property var backend
    property var fieldData
    property var formValues: ({})
    property var controller
    property var editorValue: fieldData && fieldData.value !== undefined ? fieldData.value : ""

    implicitHeight: 42
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

    Component.onCompleted: syncValue()

    Connections {
        target: root.backend
        function onFormRevisionChanged() {
            root.syncValue()
        }
    }

    RowLayout {
        anchors.fill: parent
        spacing: 10

        Text {
            text: fieldData.label
            Layout.preferredWidth: 176
            Layout.alignment: Qt.AlignVCenter
            horizontalAlignment: Text.AlignRight
            verticalAlignment: Text.AlignVCenter
            font.pixelSize: 14
            color: "#5f6b7a"
            elide: Text.ElideRight
        }

        Loader {
            Layout.fillWidth: true
            Layout.alignment: Qt.AlignVCenter
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
            implicitHeight: 34
            radius: 7
            border.width: 1
            border.color: textInput.activeFocus ? "#374151" : (hoverHandler.hovered ? "#c8d3df" : "#dce4ee")
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
                anchors.leftMargin: 10
                anchors.rightMargin: 10
                verticalAlignment: TextInput.AlignVCenter
                text: String(root.editorValue === undefined || root.editorValue === null ? "" : root.editorValue)
                selectByMouse: true
                clip: true
                font.pixelSize: 15
                color: "#202939"
                selectedTextColor: "#ffffff"
                selectionColor: "#374151"
                onTextChanged: parent.commitVisibleText()
                onActiveFocusChanged: if (!activeFocus) parent.commitVisibleText()
                Keys.onReturnPressed: { parent.commitVisibleText(); focus = false }
                Keys.onEnterPressed: { parent.commitVisibleText(); focus = false }
            }

            Text {
                anchors.left: textInput.left
                anchors.verticalCenter: parent.verticalCenter
                text: root.fieldData.placeholder
                visible: textInput.text.length === 0 && root.fieldData.placeholder.length > 0 && !textInput.activeFocus
                font.pixelSize: 14
                color: "#98a2b3"
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
            font.pixelSize: 15
            onActivated: {
                root.pushValue(root.optionValueFor(currentIndex))
            }

            contentItem: Text {
                text: combo.displayText
                color: "#202939"
                verticalAlignment: Text.AlignVCenter
                leftPadding: 10
                rightPadding: 26
                elide: Text.ElideRight
                font.pixelSize: 15
            }

            background: Rectangle {
                radius: 7
                border.width: 1
                border.color: parent.activeFocus ? "#374151" : (parent.hovered ? "#c8d3df" : "#dce4ee")
                color: "#f8fafc"
            }
        }
    }

    Component {
        id: boolEditor

        Switch {
            objectName: "switch_" + root.fieldData.name
            checked: Boolean(root.editorValue)
            onToggled: {
                root.pushValue(checked)
            }
        }
    }
}
