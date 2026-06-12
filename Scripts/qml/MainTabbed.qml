import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ApplicationWindow {
    id: root

    visible: true
    width: 1380
    height: 880
    minimumWidth: 1040
    minimumHeight: 680
    title: "RSA Porous-Media Generator"
    color: "#f4f6fa"

    property bool logOpen: false
    property string dialogText: ""
    property var formValues: backend.fieldValues
    property string activeFormTab: "Domain"

    function tabTextColor(selected, hovered) {
        if (selected)
            return "#1d4ed8"
        return hovered ? "#202939" : "#667085"
    }

    function updateFieldValue(name, value, fieldType) {
        var next = {}
        for (var key in formValues)
            next[key] = formValues[key]
        next[name] = value
        formValues = next
        if (fieldType === "bool")
            backend.setBoolField(name, Boolean(value))
        else
            backend.setTextField(name, String(value))
    }

    Connections {
        target: backend

        function onShowMessage(title, text) {
            messageDialog.title = title
            root.dialogText = text
            messageDialog.open()
        }

        function onOpenLogRequested() {
            root.logOpen = true
        }

        function onShowResultRequested() {
            viewTabs.currentIndex = 1
        }

        function onShowPreviewRequested() {
            viewTabs.currentIndex = 0
        }
    }

    Dialog {
        id: messageDialog
        modal: true
        standardButtons: Dialog.Ok
        width: Math.min(620, root.width - 72)
        x: (root.width - width) / 2
        y: Math.max(48, (root.height - height) / 3)

        contentItem: ScrollView {
            implicitHeight: Math.min(360, messageText.implicitHeight + 12)

            Text {
                id: messageText
                width: messageDialog.width - 48
                text: root.dialogText
                wrapMode: Text.WordWrap
                color: "#202939"
                font.pixelSize: 15
            }
        }
    }

    SplitView {
        anchors.fill: parent
        orientation: Qt.Horizontal

        Rectangle {
            SplitView.preferredWidth: 490
            SplitView.minimumWidth: 430
            SplitView.maximumWidth: 640
            color: "#f7f9fc"

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 16
                spacing: 10

                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 2

                    Text {
                        text: "RSA Porous Media"
                        font.pixelSize: 26
                        font.weight: Font.Bold
                        color: "#111827"
                    }

                    Text {
                        text: "2D random-sequential-adsorption packing generator"
                        font.pixelSize: 14
                        color: "#667085"
                        elide: Text.ElideRight
                        Layout.fillWidth: true
                    }
                }

                TabBar {
                    id: formTabs
                    Layout.fillWidth: true
                    spacing: 3
                    background: Rectangle { color: "transparent" }
                    onCurrentIndexChanged: {
                        if (currentIndex < 0 || currentIndex >= backend.tabs.length)
                            return
                        var title = backend.tabs[currentIndex].title
                        root.activeFormTab = title
                        backend.setActiveTab(title)
                        viewTabs.currentIndex = 0
                    }

                    Repeater {
                        model: backend.tabs

                        delegate: TabButton {
                            text: modelData.title
                            width: Math.max(72, implicitWidth)

                            contentItem: Text {
                                text: parent.text
                                horizontalAlignment: Text.AlignHCenter
                                verticalAlignment: Text.AlignVCenter
                                font.pixelSize: 14
                                font.weight: Font.DemiBold
                                color: root.tabTextColor(parent.checked, parent.hovered)
                                elide: Text.ElideRight
                            }

                            background: Rectangle {
                                color: parent.checked ? "#eaf1ff" : (parent.hovered ? "#eef2f7" : "transparent")
                                radius: 7
                            }
                        }
                    }
                }

                StackLayout {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    currentIndex: formTabs.currentIndex

                    Repeater {
                        model: backend.tabs

                        delegate: Item {
                            property var tabData: modelData

                            ScrollView {
                                id: formScroll
                                anchors.fill: parent
                                clip: true
                                ScrollBar.horizontal.policy: ScrollBar.AlwaysOff

                                ColumnLayout {
                                    width: formScroll.availableWidth
                                    spacing: 10

                                    Repeater {
                                        model: tabData.sections

                                        delegate: SectionCard {
                                            backend: backend
                                            sectionData: modelData
                                            formValues: root.formValues
                                            controller: root
                                        }
                                    }

                                    Item {
                                        Layout.fillHeight: true
                                        Layout.fillWidth: true
                                    }
                                }
                            }
                        }
                    }
                }

                RowLayout {
                    Layout.fillWidth: true
                    spacing: 8

                    ActionButton {
                        text: "Generate"
                        variant: "primary"
                        Layout.fillWidth: true
                        enabled: !backend.running
                        onClicked: backend.generate()
                    }

                    ActionButton {
                        text: "Stop"
                        variant: "danger"
                        Layout.preferredWidth: 104
                        enabled: backend.running
                        onClicked: backend.stop()
                    }
                }

                ProgressStrip {
                    caption: backend.primaryCaption
                    value: backend.primaryProgress
                    busy: backend.primaryBusy
                }

                ProgressStrip {
                    visible: backend.secondaryVisible
                    Layout.preferredHeight: visible ? implicitHeight : 0
                    caption: backend.secondaryCaption
                    value: backend.secondaryProgress
                    busy: false
                }

                RowLayout {
                    Layout.fillWidth: true
                    spacing: 8

                    ActionButton {
                        text: "Save Geometry"
                        Layout.fillWidth: true
                        enabled: backend.canSave
                        onClicked: backend.saveGeometry()
                    }

                    ActionButton {
                        text: "Throats CSV"
                        Layout.fillWidth: true
                        enabled: backend.canSave
                        onClicked: backend.saveCsv()
                    }

                    ActionButton {
                        text: "Figure"
                        Layout.fillWidth: true
                        enabled: backend.canSave
                        onClicked: backend.saveFigure()
                    }
                }
            }
        }

        Rectangle {
            SplitView.fillWidth: true
            color: "#eef2f7"

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 14
                spacing: 10

                RowLayout {
                    Layout.fillWidth: true
                    spacing: 8

                    StatBadge {
                        text: backend.stateBadgeText
                        kind: backend.stateBadgeKind
                    }

                    StatBadge {
                        text: backend.phiBadgeText
                        kind: backend.phiBadgeKind
                    }

                    StatBadge {
                        text: backend.throatBadgeText
                        kind: backend.throatBadgeKind
                    }

                    Item { Layout.fillWidth: true }

                    ActionButton {
                        text: root.logOpen ? "Log Hide" : "Log Show"
                        Layout.preferredWidth: 104
                        onClicked: root.logOpen = !root.logOpen
                    }
                }

                Rectangle {
                    property real drawerHeight: root.logOpen ? 132 : 0

                    Layout.fillWidth: true
                    Layout.preferredHeight: drawerHeight
                    visible: drawerHeight > 1
                    radius: 8
                    color: "#ffffff"
                    border.width: 1
                    border.color: "#dfe6ef"
                    clip: true

                    Behavior on drawerHeight {
                        NumberAnimation { duration: 180; easing.type: Easing.OutCubic }
                    }

                    ScrollView {
                        anchors.fill: parent
                        anchors.margins: 10

                        TextArea {
                            text: backend.logText
                            readOnly: true
                            wrapMode: TextEdit.Wrap
                            selectByMouse: true
                            color: "#344054"
                            font.pixelSize: 14
                            background: null
                        }
                    }
                }

                TabBar {
                    id: viewTabs
                    Layout.fillWidth: true
                    background: Rectangle { color: "transparent" }

                    TabButton {
                        text: "Preview"

                        contentItem: Text {
                            text: parent.text
                            horizontalAlignment: Text.AlignHCenter
                            verticalAlignment: Text.AlignVCenter
                            font.pixelSize: 15
                            font.weight: Font.DemiBold
                            color: root.tabTextColor(parent.checked, parent.hovered)
                        }

                        background: Rectangle {
                            color: parent.checked ? "#ffffff" : (parent.hovered ? "#e7ecf4" : "transparent")
                            radius: 7
                        }
                    }

                    TabButton {
                        text: "Result"

                        contentItem: Text {
                            text: parent.text
                            horizontalAlignment: Text.AlignHCenter
                            verticalAlignment: Text.AlignVCenter
                            font.pixelSize: 15
                            font.weight: Font.DemiBold
                            color: root.tabTextColor(parent.checked, parent.hovered)
                        }

                        background: Rectangle {
                            color: parent.checked ? "#ffffff" : (parent.hovered ? "#e7ecf4" : "transparent")
                            radius: 7
                        }
                    }
                }

                StackLayout {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    currentIndex: viewTabs.currentIndex

                    PreviewCanvas {
                        previewData: backend.previewData
                        formValues: root.formValues
                        activeTab: root.activeFormTab
                    }

                    ImagePanel {
                        source: backend.resultSource
                        placeholder: "Result"
                    }
                }

                Rectangle {
                    Layout.fillWidth: true
                    Layout.preferredHeight: 30
                    radius: 8
                    color: "#ffffff"
                    border.width: 1
                    border.color: "#dfe6ef"

                    Text {
                        anchors.fill: parent
                        anchors.leftMargin: 12
                        anchors.rightMargin: 12
                        verticalAlignment: Text.AlignVCenter
                        text: backend.statusText
                        font.pixelSize: 14
                        color: "#667085"
                        elide: Text.ElideRight
                    }
                }
            }
        }
    }

    Component.onCompleted: {
        if (formTabs.currentIndex >= 0 && formTabs.currentIndex < backend.tabs.length)
            root.activeFormTab = backend.tabs[formTabs.currentIndex].title
        if (formTabs.currentIndex >= 0 && formTabs.currentIndex < backend.tabs.length)
            backend.setActiveTab(backend.tabs[formTabs.currentIndex].title)
    }
}
