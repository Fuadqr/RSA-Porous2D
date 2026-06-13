import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// Single-board layout: every parameter card is visible at once (no form
// tabs, no per-tab scrolling), a full-width command bar carries run/save
// controls, and the live preview keeps the right side permanently.
// Heterogeneity has its own column so its fields are never pushed below
// the fold. Polygon heterogeneities are drawn directly on the domain
// sketch (click to add a point, click a point to remove it, drag points
// or the shape to move); in multiple mode the user defines centers first
// and draws one polygon per center, advancing with the center controls.
// The previous tabbed layout is preserved in MainTabbed.qml (RSA_GUI_TABBED=1).
ApplicationWindow {
    id: root

    visible: true
    width: 1600
    height: 940
    minimumWidth: 1280
    minimumHeight: 720
    title: "RSA-Porous2D"
    color: "#eef1f6"

    property bool logOpen: false
    property string dialogText: ""
    // qualified alias for the context property: inside component bindings an
    // unqualified `backend` would resolve to the target's own property.
    readonly property var appBackend: backend
    property var formValues: backend.fieldValues
    property string activeFormTab: "Domain"

    // ---- polygon drawing state (mirrors the QtWidgets mechanism) ----------
    property bool drawingPolygon: false
    property int activeHetIndex: 0
    property bool addCentersMode: false
    readonly property bool polygonCapable: formValues["medium_type"] === "multiple"
        || (formValues["medium_type"] === "layer" && formValues["layer_shape"] === "polygon")
    readonly property bool multipleMode: formValues["medium_type"] === "multiple"
    readonly property int activePolygonPoints: Math.floor(polygonValues().length / 2)
    readonly property int centerCount: centersList().length

    property bool _loadingPolygon: false

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

        // keep the multiple-mode polygon groups in lockstep with the active
        // polygon, however it was edited (canvas interaction or typing)
        if (name === "layer_polygon" && multipleMode && !_loadingPolygon)
            syncActivePolygonToGroups(polygonValues())
        if (name === "medium_type") {
            drawingPolygon = false
            addCentersMode = false
            if (value === "multiple") {
                // start clean on the first center: the single-mode polygon
                // must not leak into group 1
                activeHetIndex = 0
                loadActivePolygonFromGroups()
            }
        }
        if (name === "heterogeneity_centers" && multipleMode) {
            var c = centersList().length
            if (activeHetIndex >= c)
                activeHetIndex = Math.max(0, c - 1)
        }
    }

    function loadActivePolygonFromGroups() {
        _loadingPolygon = true
        var groups = polygonGroups()
        var vals = activeHetIndex < groups.length ? groups[activeHetIndex] : []
        setPolygonValues(vals)
        _loadingPolygon = false
    }

    // The preview pane follows whichever card is being edited: Domain fields
    // show the domain sketch, everything else the PSD / heterogeneity
    // overview. The backend only distinguishes "Domain" from the rest.
    function focusContext(tabTitle) {
        var mode = tabTitle === "Domain" ? "Domain" : "Grains"
        viewTabs.currentIndex = 0
        if (root.activeFormTab === mode)
            return
        root.activeFormTab = mode
        backend.setActiveTab(mode)
    }

    function sectionsFor(tabTitle, names) {
        var tab = null
        for (var i = 0; i < backend.tabs.length; ++i) {
            if (backend.tabs[i].title === tabTitle) {
                tab = backend.tabs[i]
                break
            }
        }
        if (!tab)
            return []
        if (!names)
            return tab.sections
        var out = []
        for (var s = 0; s < tab.sections.length; ++s) {
            if (names.indexOf(tab.sections[s].title) !== -1)
                out.push(tab.sections[s])
        }
        return out
    }

    function tabTextColor(selected, hovered) {
        if (selected)
            return "#374151"
        return hovered ? "#202939" : "#667085"
    }

    // ---- polygon / centers helpers (domain meters; same storage scheme as
    // the QtWidgets frontend: layer_polygon = active polygon, and in multiple
    // mode heterogeneity_polygons keeps nan,nan-separated groups, one per
    // center) ---------------------------------------------------------------
    function numValue(name, fallback) {
        var v = formValues[name]
        if (v === undefined || v === null || String(v).length === 0)
            return fallback
        var n = Number(v)
        return isFinite(n) ? n : fallback
    }

    function parseTuple(text) {
        if (text === undefined || text === null)
            return []
        var parts = String(text).replace(/;/g, ",").split(",")
        var out = []
        for (var i = 0; i < parts.length; ++i) {
            var t = parts[i].trim()
            if (!t.length)
                continue
            out.push(Number(t))
        }
        return out
    }

    function fmtNum(v) {
        return String(Number(v.toPrecision(8)))
    }

    function polygonValues() {
        return parseTuple(formValues["layer_polygon"]).filter(function(v) { return isFinite(v) })
    }

    function centersList() {
        var raw = parseTuple(formValues["heterogeneity_centers"]).filter(function(v) { return isFinite(v) })
        var w = numValue("width", 0.05)
        var h = numValue("height", 0.06)
        var out = []
        for (var i = 0; i + 1 < raw.length; i += 2)
            out.push({ x: Math.max(0, Math.min(w, raw[i])), z: Math.max(0, Math.min(h, raw[i + 1])) })
        return out
    }

    function polygonGroups() {
        var raw = parseTuple(formValues["heterogeneity_polygons"])
        var groups = []
        var current = []
        for (var i = 0; i + 1 < raw.length; i += 2) {
            if (!isFinite(raw[i]) || !isFinite(raw[i + 1])) {
                groups.push(current)
                current = []
            } else {
                current.push(raw[i], raw[i + 1])
            }
        }
        groups.push(current)
        while (groups.length && groups[groups.length - 1].length === 0)
            groups.pop()
        return groups
    }

    function syncActivePolygonToGroups(vals) {
        if (!multipleMode)
            return
        var count = centersList().length
        if (count <= 0)
            return
        if (activeHetIndex >= count)
            activeHetIndex = count - 1
        var groups = polygonGroups()
        while (groups.length < count)
            groups.push([])
        groups[activeHetIndex] = vals.slice()
        groups = groups.slice(0, count)
        var parts = []
        for (var g = 0; g < groups.length; ++g) {
            if (g)
                parts.push("nan", "nan")
            for (var v = 0; v < groups[g].length; ++v)
                parts.push(fmtNum(groups[g][v]))
        }
        updateFieldValue("heterogeneity_polygons", parts.join(", "), "text")
    }

    function setPolygonValues(vals) {
        var parts = []
        for (var i = 0; i < vals.length; ++i)
            parts.push(fmtNum(vals[i]))
        updateFieldValue("layer_polygon", parts.join(", "), "text")
    }

    function setActiveCenter(index) {
        if (!multipleMode)
            return
        syncActivePolygonToGroups(polygonValues())
        var count = centersList().length
        if (count <= 0) {
            activeHetIndex = 0
            loadActivePolygonFromGroups()
            return
        }
        activeHetIndex = Math.max(0, Math.min(Math.round(index), count - 1))
        loadActivePolygonFromGroups()
    }

    function startStopDrawing() {
        if (backend.running)
            return
        if (multipleMode && centersList().length === 0) {
            messageDialog.title = "Define centers first"
            root.dialogText = "For multiple heterogeneity, define the center x,z pairs first "
                + "(type them into the centers field, or toggle \"Add centers\" and click "
                + "the domain sketch). Then draw one polygon for each center."
            messageDialog.open()
            return
        }
        root.addCentersMode = false
        var starting = !drawingPolygon
        var wasPolygon = polygonCapable
        if (!multipleMode) {
            if (formValues["medium_type"] !== "layer")
                updateFieldValue("medium_type", "layer", "combo")
            if (formValues["layer_shape"] !== "polygon")
                updateFieldValue("layer_shape", "polygon", "combo")
        }
        if (starting && !wasPolygon)
            setPolygonValues([])
        drawingPolygon = starting
        focusContext("Domain")
        if (!starting && multipleMode) {
            var npts = Math.floor(polygonValues().length / 2)
            if (npts >= 3 && activeHetIndex + 1 < centersList().length)
                setActiveCenter(activeHetIndex + 1)
        }
    }

    function undoPolygonPoint() {
        var v = polygonValues()
        if (v.length >= 2)
            setPolygonValues(v.slice(0, v.length - 2))
    }

    function clearPolygon() {
        if (!multipleMode) {
            if (formValues["medium_type"] !== "layer")
                updateFieldValue("medium_type", "layer", "combo")
            if (formValues["layer_shape"] !== "polygon")
                updateFieldValue("layer_shape", "polygon", "combo")
        }
        setPolygonValues([])
    }

    function addPolygonPoint(x, z) {
        var v = polygonValues()
        v.push(x, z)
        setPolygonValues(v)
    }

    function movePolygonPoint(index, x, z) {
        var v = polygonValues()
        if (2 * index + 1 < v.length) {
            v[2 * index] = x
            v[2 * index + 1] = z
            setPolygonValues(v)
        }
    }

    function removePolygonPoint(index) {
        var v = polygonValues()
        if (2 * index + 1 < v.length) {
            v.splice(2 * index, 2)
            setPolygonValues(v)
        }
    }

    function moveActivePolygonBy(dx, dz) {
        var v = polygonValues()
        if (!v.length)
            return [0, 0]
        var minX = 1e99, maxX = -1e99, minZ = 1e99, maxZ = -1e99
        for (var i = 0; i + 1 < v.length; i += 2) {
            minX = Math.min(minX, v[i])
            maxX = Math.max(maxX, v[i])
            minZ = Math.min(minZ, v[i + 1])
            maxZ = Math.max(maxZ, v[i + 1])
        }
        dx = Math.max(-minX, Math.min(dx, numValue("width", 0.05) - maxX))
        dz = Math.max(-minZ, Math.min(dz, numValue("height", 0.06) - maxZ))
        for (var j = 0; j + 1 < v.length; j += 2) {
            v[j] += dx
            v[j + 1] += dz
        }
        setPolygonValues(v)
        return [dx, dz]
    }

    function pointInFlatPolygon(flat, x, z) {
        var n = Math.floor(flat.length / 2)
        if (n < 3)
            return false
        var inside = false
        var j = n - 1
        for (var i = 0; i < n; ++i) {
            var xi = flat[2 * i], zi = flat[2 * i + 1]
            var xj = flat[2 * j], zj = flat[2 * j + 1]
            if ((zi > z) !== (zj > z)) {
                var xCross = (xj - xi) * (z - zi) / (zj - zi) + xi
                if (x <= xCross)
                    inside = !inside
            }
            j = i
        }
        return inside
    }

    // Right-button drag moves a polygon. Hit the active polygon first, then
    // (in multiple mode) any other group's polygon -- that group becomes the
    // active one so it can be dragged.
    function beginPolygonMoveAt(x, z) {
        if (drawingPolygon)
            return false
        if (pointInFlatPolygon(polygonValues(), x, z))
            return true
        if (multipleMode) {
            var groups = polygonGroups()
            for (var g = 0; g < groups.length; ++g) {
                if (g !== activeHetIndex && pointInFlatPolygon(groups[g], x, z)) {
                    setActiveCenter(g)
                    return true
                }
            }
        }
        return false
    }

    function canvasEmptyClicked(x, z) {
        if (multipleMode && addCentersMode && !drawingPolygon) {
            var raw = parseTuple(formValues["heterogeneity_centers"]).filter(function(v) { return isFinite(v) })
            raw.push(x, z)
            var parts = []
            for (var i = 0; i < raw.length; ++i)
                parts.push(fmtNum(raw[i]))
            updateFieldValue("heterogeneity_centers", parts.join(", "), "text")
        }
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

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 12
        spacing: 10

        // ----- command bar: run, progress, badges, save, log ---------------
        Rectangle {
            Layout.fillWidth: true
            implicitHeight: barRow.implicitHeight + 18
            radius: 10
            color: "#ffffff"
            border.width: 1
            border.color: "#e1e7f0"

            RowLayout {
                id: barRow
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                anchors.leftMargin: 12
                anchors.rightMargin: 12
                spacing: 8

                Image {
                    visible: backend.appIconSource !== ""
                    source: backend.appIconSource
                    sourceSize.width: 28
                    sourceSize.height: 28
                    Layout.preferredWidth: 28
                    Layout.preferredHeight: 28
                    fillMode: Image.PreserveAspectFit
                }

                ActionButton {
                    text: "Generate"
                    variant: "primary"
                    Layout.preferredWidth: 124
                    enabled: !backend.running
                    onClicked: backend.generate()
                }

                ActionButton {
                    text: "Stop"
                    variant: "danger"
                    Layout.preferredWidth: 82
                    enabled: backend.running
                    onClicked: backend.stop()
                }

                ColumnLayout {
                    Layout.fillWidth: true
                    Layout.minimumWidth: 150
                    spacing: 2

                    ProgressStrip {
                        caption: backend.primaryCaption
                        value: backend.primaryProgress
                        busy: backend.primaryBusy
                        detail: backend.primaryDetail
                    }

                    ProgressStrip {
                        visible: backend.secondaryVisible
                        Layout.preferredHeight: visible ? implicitHeight : 0
                        caption: backend.secondaryCaption
                        value: backend.secondaryProgress
                        busy: false
                        detail: backend.secondaryDetail
                    }
                }

                StatBadge {
                    text: backend.stateBadgeText
                    kind: backend.stateBadgeKind
                    Layout.maximumWidth: 150
                }

                StatBadge {
                    text: backend.phiBadgeText
                    kind: backend.phiBadgeKind
                    Layout.maximumWidth: 170
                }

                StatBadge {
                    text: backend.throatBadgeText
                    kind: backend.throatBadgeKind
                    Layout.maximumWidth: 240
                }

                ActionButton {
                    text: "Save Geometry"
                    Layout.preferredWidth: 132
                    enabled: backend.canSave
                    onClicked: backend.saveGeometry()
                }

                ActionButton {
                    text: "Throats CSV"
                    Layout.preferredWidth: 114
                    enabled: backend.canSave
                    onClicked: backend.saveCsv()
                }

                ActionButton {
                    text: "Figure"
                    Layout.preferredWidth: 86
                    enabled: backend.canSave
                    onClicked: backend.saveFigure()
                }

                ActionButton {
                    text: root.logOpen ? "Hide log" : "Log"
                    Layout.preferredWidth: 88
                    onClicked: root.logOpen = !root.logOpen
                }
            }
        }

        // ----- body: parameter board (left) + preview (right) --------------
        SplitView {
            Layout.fillWidth: true
            Layout.fillHeight: true
            orientation: Qt.Horizontal

            handle: Rectangle {
                implicitWidth: 7
                color: "#eef1f6"

                Rectangle {
                    anchors.centerIn: parent
                    width: 3
                    height: 44
                    radius: 2
                    color: parent.SplitHandle.pressed ? "#374151"
                         : (parent.SplitHandle.hovered ? "#b7c3d4" : "#d4dce6")
                }
            }

            Item {
                SplitView.fillWidth: true
                SplitView.minimumWidth: 760

                ScrollView {
                    id: boardScroll
                    anchors.fill: parent
                    clip: true
                    ScrollBar.horizontal.policy: ScrollBar.AlwaysOff

                    ScrollBar.vertical: ScrollBar {
                        id: boardVBar
                        policy: ScrollBar.AsNeeded
                        contentItem: Rectangle {
                            implicitWidth: 8
                            radius: 4
                            color: boardVBar.pressed ? "#94a3b8" : "#c4cedb"
                            opacity: boardVBar.size < 1.0 ? 0.9 : 0.0
                        }
                        background: Rectangle { color: "transparent" }
                    }

                    RowLayout {
                        width: Math.max(720, boardScroll.availableWidth)
                        spacing: 10

                        ColumnLayout {
                            Layout.fillWidth: true
                            Layout.preferredWidth: 10
                            Layout.alignment: Qt.AlignTop
                            spacing: 10

                            ParamCard {
                                backend: root.appBackend
                                formValues: root.formValues
                                controller: root
                                title: "Domain"
                                accent: "#374151"
                                sections: root.sectionsFor("Domain", null)
                            }

                            ParamCard {
                                backend: root.appBackend
                                formValues: root.formValues
                                controller: root
                                title: "Grains"
                                accent: "#374151"
                                sections: root.sectionsFor("Grains", null)
                            }
                        }

                        ColumnLayout {
                            Layout.fillWidth: true
                            Layout.preferredWidth: 10
                            Layout.alignment: Qt.AlignTop
                            spacing: 10

                            ParamCard {
                                backend: root.appBackend
                                formValues: root.formValues
                                controller: root
                                title: "Heterogeneity"
                                accent: "#374151"
                                contextTitle: "Heterogeneity"
                                hint: root.formValues["medium_type"] === "homogeneous"
                                      ? "pick a heterogeneity mode to unlock its options" : ""
                                sections: root.sectionsFor("Heterogeneity", null)
                            }
                        }

                        ColumnLayout {
                            Layout.fillWidth: true
                            Layout.preferredWidth: 10
                            Layout.alignment: Qt.AlignTop
                            spacing: 10

                            ParamCard {
                                backend: root.appBackend
                                formValues: root.formValues
                                controller: root
                                title: "Throat & diagnostics"
                                accent: "#374151"
                                contextTitle: "Throat & Output"
                                sections: root.sectionsFor("Throat & Output",
                                                           ["Minimum pore throat", "Diagnostics"])
                            }

                            ParamCard {
                                backend: root.appBackend
                                formValues: root.formValues
                                controller: root
                                title: "Output"
                                accent: "#374151"
                                contextTitle: "Throat & Output"
                                sections: root.sectionsFor("Throat & Output", ["Output"])
                            }

                            ParamCard {
                                backend: root.appBackend
                                formValues: root.formValues
                                controller: root
                                title: "Run"
                                accent: "#374151"
                                sections: root.sectionsFor("Run", null)
                            }
                        }
                    }
                }
            }

            Rectangle {
                SplitView.preferredWidth: Math.round(root.width * 0.36)
                SplitView.minimumWidth: 430
                color: "transparent"

                ColumnLayout {
                    anchors.fill: parent
                    anchors.leftMargin: 10
                    spacing: 8

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 8

                        TabBar {
                            id: viewTabs
                            background: Rectangle { color: "transparent" }

                            TabButton {
                                text: "Preview"
                                width: 92

                                contentItem: Text {
                                    text: parent.text
                                    horizontalAlignment: Text.AlignHCenter
                                    verticalAlignment: Text.AlignVCenter
                                    font.pixelSize: 14
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
                                width: 92

                                contentItem: Text {
                                    text: parent.text
                                    horizontalAlignment: Text.AlignHCenter
                                    verticalAlignment: Text.AlignVCenter
                                    font.pixelSize: 14
                                    font.weight: Font.DemiBold
                                    color: root.tabTextColor(parent.checked, parent.hovered)
                                }

                                background: Rectangle {
                                    color: parent.checked ? "#ffffff" : (parent.hovered ? "#e7ecf4" : "transparent")
                                    radius: 7
                                }
                            }
                        }

                        Item { Layout.fillWidth: true }

                        // manual preview-mode chips (the preview also follows
                        // whichever card is being edited)
                        Rectangle {
                            visible: viewTabs.currentIndex === 0
                            width: domainChipText.implicitWidth + 20
                            height: 26
                            radius: 13
                            color: root.activeFormTab === "Domain" ? "#e6e8ec" : "transparent"
                            border.width: 1
                            border.color: root.activeFormTab === "Domain" ? "#cbd2da" : "#d8e0ea"

                            Text {
                                id: domainChipText
                                anchors.centerIn: parent
                                text: "Domain"
                                font.pixelSize: 12
                                font.weight: Font.DemiBold
                                color: root.activeFormTab === "Domain" ? "#374151" : "#667085"
                            }

                            MouseArea {
                                anchors.fill: parent
                                cursorShape: Qt.PointingHandCursor
                                onClicked: root.focusContext("Domain")
                            }
                        }

                        Rectangle {
                            visible: viewTabs.currentIndex === 0
                            width: psdChipText.implicitWidth + 20
                            height: 26
                            radius: 13
                            color: root.activeFormTab !== "Domain" ? "#e6e8ec" : "transparent"
                            border.width: 1
                            border.color: root.activeFormTab !== "Domain" ? "#cbd2da" : "#d8e0ea"

                            Text {
                                id: psdChipText
                                anchors.centerIn: parent
                                text: "PSD"
                                font.pixelSize: 12
                                font.weight: Font.DemiBold
                                color: root.activeFormTab !== "Domain" ? "#374151" : "#667085"
                            }

                            MouseArea {
                                anchors.fill: parent
                                cursorShape: Qt.PointingHandCursor
                                onClicked: root.focusContext("Grains")
                            }
                        }
                    }

                    // ----- polygon drawing controls (Draw switches a layer to
                    // polygon shape, like the QtWidgets frontend) ------------
                    Rectangle {
                        visible: viewTabs.currentIndex === 0
                                 && (root.polygonCapable || root.formValues["medium_type"] === "layer")
                        Layout.fillWidth: true
                        implicitHeight: drawRow.implicitHeight + 12
                        radius: 8
                        color: "#ffffff"
                        border.width: 1
                        border.color: "#dfe6ef"

                        Flow {
                            id: drawRow
                            anchors.left: parent.left
                            anchors.right: parent.right
                            anchors.top: parent.top
                            anchors.leftMargin: 8
                            anchors.rightMargin: 8
                            anchors.topMargin: 6
                            spacing: 6

                            ActionButton {
                                text: root.drawingPolygon ? "Finish" : "Draw points"
                                variant: "primary"
                                width: 104
                                implicitHeight: 30
                                enabled: !backend.running
                                onClicked: root.startStopDrawing()
                            }

                            ActionButton {
                                text: "Undo"
                                width: 62
                                implicitHeight: 30
                                enabled: root.activePolygonPoints > 0
                                onClicked: root.undoPolygonPoint()
                            }

                            ActionButton {
                                text: "Clear"
                                variant: "danger"
                                width: 62
                                implicitHeight: 30
                                enabled: root.activePolygonPoints > 0
                                onClicked: root.clearPolygon()
                            }

                            ActionButton {
                                visible: root.multipleMode
                                text: "◀"
                                width: 34
                                implicitHeight: 30
                                enabled: root.centerCount > 1 && root.activeHetIndex > 0
                                onClicked: root.setActiveCenter(root.activeHetIndex - 1)
                            }

                            Item {
                                visible: root.multipleMode
                                width: centerLabel.implicitWidth + 4
                                height: 30

                                Text {
                                    id: centerLabel
                                    anchors.centerIn: parent
                                    text: root.centerCount > 0
                                          ? "center " + (root.activeHetIndex + 1) + "/" + root.centerCount
                                          : "no centers"
                                    font.pixelSize: 12
                                    font.weight: Font.DemiBold
                                    color: "#526070"
                                }
                            }

                            ActionButton {
                                visible: root.multipleMode
                                text: "▶"
                                width: 34
                                implicitHeight: 30
                                enabled: root.centerCount > 1 && root.activeHetIndex < root.centerCount - 1
                                onClicked: root.setActiveCenter(root.activeHetIndex + 1)
                            }

                            Rectangle {
                                visible: root.multipleMode
                                width: addCentersText.implicitWidth + 18
                                height: 30
                                radius: 15
                                color: root.addCentersMode ? "#ffedd5" : "transparent"
                                border.width: 1
                                border.color: root.addCentersMode ? "#fdba74" : "#d8e0ea"

                                Text {
                                    id: addCentersText
                                    anchors.centerIn: parent
                                    text: "Add centers"
                                    font.pixelSize: 12
                                    font.weight: Font.DemiBold
                                    color: root.addCentersMode ? "#c2410c" : "#667085"
                                }

                                MouseArea {
                                    anchors.fill: parent
                                    cursorShape: Qt.PointingHandCursor
                                    onClicked: {
                                        root.addCentersMode = !root.addCentersMode
                                        if (root.addCentersMode) {
                                            root.drawingPolygon = false
                                            root.focusContext("Domain")
                                        }
                                    }
                                }
                            }
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

                    StackLayout {
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        currentIndex: viewTabs.currentIndex

                        PreviewCanvas {
                            previewData: backend.previewData
                            formValues: root.formValues
                            activeTab: root.activeFormTab
                            controller: root
                            interactive: root.polygonCapable && root.activeFormTab === "Domain"
                            drawing: root.drawingPolygon
                            activeCenterIndex: root.activeHetIndex
                            locked: backend.running
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
    }

    Component.onCompleted: backend.setActiveTab("Domain")
}
