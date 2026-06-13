import QtQuick

Rectangle {
    id: root

    property var previewData: ({})
    property var formValues: ({})
    property string activeTab: "Domain"

    // interactive polygon editing (wired by Main.qml; mirrors the QtWidgets
    // mechanism: click empty space to add a point, click a point to remove
    // it, drag a point or the whole shape to move)
    property var controller
    property bool interactive: false
    property bool drawing: false
    property int activeCenterIndex: 0
    property bool locked: false

    // domain-sketch transform captured during the last paint, used by the
    // MouseArea to map screen <-> domain coordinates
    property bool domainActive: false
    property real domRx: 0
    property real domRy: 0
    property real domRectW: 1
    property real domRectH: 1
    property real domW: 1
    property real domH: 1

    radius: 8
    color: "#ffffff"
    border.width: 1
    border.color: "#dfe6ef"
    clip: true

    onPreviewDataChanged: plot.requestPaint()
    onFormValuesChanged: plot.requestPaint()
    onActiveTabChanged: plot.requestPaint()
    onDrawingChanged: plot.requestPaint()
    onActiveCenterIndexChanged: plot.requestPaint()

    Canvas {
        id: plot
        anchors.fill: parent
        anchors.margins: 12

        onWidthChanged: requestPaint()
        onHeightChanged: requestPaint()

        function drawArrow(ctx, x1, y1, x2, y2, color) {
            var head = 10
            var angle = Math.atan2(y2 - y1, x2 - x1)
            ctx.save()
            ctx.strokeStyle = color
            ctx.fillStyle = color
            ctx.lineWidth = 2
            ctx.beginPath()
            ctx.moveTo(x1, y1)
            ctx.lineTo(x2, y2)
            ctx.stroke()
            for (var i = 0; i < 2; ++i) {
                var a = i === 0 ? angle : angle + Math.PI
                var px = i === 0 ? x2 : x1
                var py = i === 0 ? y2 : y1
                ctx.beginPath()
                ctx.moveTo(px, py)
                ctx.lineTo(px - head * Math.cos(a - Math.PI / 6),
                           py - head * Math.sin(a - Math.PI / 6))
                ctx.lineTo(px - head * Math.cos(a + Math.PI / 6),
                           py - head * Math.sin(a + Math.PI / 6))
                ctx.closePath()
                ctx.fill()
            }
            ctx.restore()
        }

        function drawTitle(ctx, text, x, y, size, color) {
            ctx.fillStyle = color || "#111827"
            ctx.font = "700 " + size + "px Segoe UI, Arial"
            ctx.textAlign = "center"
            ctx.textBaseline = "middle"
            ctx.fillText(text, x, y)
        }

        function drawDomain(ctx, d) {
            var w = width
            var h = height
            var titleY = 38
            drawTitle(ctx, "Domain dimensions", w / 2, titleY, 34, "#000000")

            var rectW = Math.min(w * 0.58, h * 0.58 * (d.width / Math.max(d.height, 1e-12)))
            var rectH = rectW * d.height / Math.max(d.width, 1e-12)
            if (rectH > h * 0.66) {
                rectH = h * 0.66
                rectW = rectH * d.width / Math.max(d.height, 1e-12)
            }
            var rx = (w - rectW) / 2 + 20
            var ry = titleY + 92

            root.domRx = rx
            root.domRy = ry
            root.domRectW = rectW
            root.domRectH = rectH
            root.domW = Math.max(d.width, 1e-12)
            root.domH = Math.max(d.height, 1e-12)

            ctx.fillStyle = "#eef0f2"
            ctx.strokeStyle = "#374151"
            ctx.lineWidth = 4
            ctx.fillRect(rx, ry, rectW, rectH)
            ctx.strokeRect(rx, ry, rectW, rectH)

            drawArrow(ctx, rx, ry - 30, rx + rectW, ry - 30, "#344054")
            drawTitle(ctx, "width = " + d.widthMm.toFixed(1) + " mm",
                      rx + rectW / 2, ry - 62, 28, "#344054")

            drawArrow(ctx, rx - 30, ry, rx - 30, ry + rectH, "#344054")
            ctx.save()
            ctx.translate(rx - 76, ry + rectH / 2)
            ctx.rotate(-Math.PI / 2)
            drawTitle(ctx, "height = " + d.heightMm.toFixed(1) + " mm", 0, 0, 28, "#344054")
            ctx.restore()

            drawTitle(ctx, d.widthMm.toFixed(1) + " x " + d.heightMm.toFixed(1) + " mm",
                      rx + rectW / 2, ry + rectH / 2, 30, "#374151")

            if (d.layer && (d.layer.shape === "polygon" || d.layer.shape === "multipolygon")) {
                function xMap(x) { return rx + x / Math.max(d.width, 1e-12) * rectW }
                function zMap(z) { return ry + z / Math.max(d.height, 1e-12) * rectH }
                var polys = d.layer.shape === "multipolygon"
                    ? (d.layer.polygons || [])
                    : [d.layer.polygon || []]
                for (var pp = 0; pp < polys.length; ++pp) {
                    var poly = polys[pp]
                    if (!poly || !poly.length)
                        continue
                    if (poly.length >= 3) {
                        ctx.globalAlpha = 0.74
                        ctx.fillStyle = "#ccfbf1"
                        ctx.strokeStyle = "#0f766e"
                        ctx.lineWidth = 3
                        ctx.beginPath()
                        ctx.moveTo(xMap(poly[0].x), zMap(poly[0].z))
                        for (var p = 1; p < poly.length; ++p)
                            ctx.lineTo(xMap(poly[p].x), zMap(poly[p].z))
                        ctx.closePath()
                        ctx.fill()
                        ctx.globalAlpha = 1
                        ctx.stroke()
                    } else {
                        ctx.strokeStyle = "#0f766e"
                        ctx.lineWidth = 3
                        ctx.beginPath()
                        ctx.moveTo(xMap(poly[0].x), zMap(poly[0].z))
                        for (var q = 1; q < poly.length; ++q)
                            ctx.lineTo(xMap(poly[q].x), zMap(poly[q].z))
                        ctx.stroke()
                    }
                    ctx.fillStyle = "#0f766e"
                    for (var v = 0; v < poly.length; ++v) {
                        ctx.beginPath()
                        ctx.arc(xMap(poly[v].x), zMap(poly[v].z), 5, 0, 2 * Math.PI)
                        ctx.fill()
                    }
                }
                // active (in-progress) polygon on top, highlighted, so the
                // points being placed or edited are always visible -- even
                // before the shape has three points
                var act = d.layer.shape === "multipolygon" ? (d.layer.activePolygon || []) : []
                if (act.length) {
                    if (act.length >= 3) {
                        ctx.globalAlpha = 0.35
                        ctx.fillStyle = "#fdba74"
                        ctx.beginPath()
                        ctx.moveTo(xMap(act[0].x), zMap(act[0].z))
                        for (var ap = 1; ap < act.length; ++ap)
                            ctx.lineTo(xMap(act[ap].x), zMap(act[ap].z))
                        ctx.closePath()
                        ctx.fill()
                        ctx.globalAlpha = 1
                    }
                    ctx.strokeStyle = "#ea580c"
                    ctx.lineWidth = 3
                    ctx.beginPath()
                    ctx.moveTo(xMap(act[0].x), zMap(act[0].z))
                    for (var al = 1; al < act.length; ++al)
                        ctx.lineTo(xMap(act[al].x), zMap(act[al].z))
                    if (act.length >= 3)
                        ctx.closePath()
                    ctx.stroke()
                    for (var av = 0; av < act.length; ++av) {
                        ctx.beginPath()
                        ctx.arc(xMap(act[av].x), zMap(act[av].z), 6, 0, 2 * Math.PI)
                        ctx.fillStyle = "#ea580c"
                        ctx.fill()
                        ctx.strokeStyle = "#ffffff"
                        ctx.lineWidth = 1.5
                        ctx.stroke()
                    }
                }

                var centers = d.layer.centers || []
                ctx.fillStyle = "#f97316"
                for (var cc = 0; cc < centers.length; ++cc) {
                    ctx.beginPath()
                    ctx.arc(xMap(centers[cc].x), zMap(centers[cc].z), 6, 0, 2 * Math.PI)
                    ctx.fill()
                }
                var requested = d.layer.requestedCenters || []
                var activeIdx = d.layer.shape === "multipolygon" ? root.activeCenterIndex : -1
                ctx.font = "700 13px Segoe UI, Arial"
                ctx.textAlign = "left"
                ctx.textBaseline = "middle"
                for (var rc = 0; rc < requested.length; ++rc) {
                    var rxC = xMap(requested[rc].x)
                    var rzC = zMap(requested[rc].z)
                    if (rc === activeIdx) {
                        ctx.strokeStyle = "#ea580c"
                        ctx.lineWidth = 2
                        ctx.beginPath()
                        ctx.arc(rxC, rzC, 11, 0, 2 * Math.PI)
                        ctx.stroke()
                    }
                    ctx.strokeStyle = "#f97316"
                    ctx.fillStyle = "#f97316"
                    ctx.lineWidth = 3
                    ctx.beginPath()
                    ctx.moveTo(rxC - 7, rzC)
                    ctx.lineTo(rxC + 7, rzC)
                    ctx.moveTo(rxC, rzC - 7)
                    ctx.lineTo(rxC, rzC + 7)
                    ctx.stroke()
                    ctx.fillText(String(rc + 1), rxC + 8, rzC)
                }
            }
        }

        function niceNumber(v) {
            if (!isFinite(v))
                return "0"
            if (Math.abs(v) >= 10)
                return v.toFixed(0)
            if (Math.abs(v) >= 1)
                return v.toFixed(1)
            return v.toFixed(2)
        }

        function drawPsd(ctx, d, r) {
            ctx.save()
            ctx.fillStyle = "#ffffff"
            ctx.strokeStyle = "#d8e0ea"
            ctx.lineWidth = 1
            ctx.fillRect(r.x, r.y, r.w, r.h)
            ctx.strokeRect(r.x, r.y, r.w, r.h)

            drawTitle(ctx, d.title, r.x + r.w / 2, r.y + 18, 16, "#111827")

            var left = r.x + 56
            var right = r.x + r.w - 18
            var top = r.y + 42
            var bottom = r.y + r.h - 42
            var plotW = Math.max(1, right - left)
            var plotH = Math.max(1, bottom - top)
            var xs = (d.diametersMm || []).concat(d.curveDiametersMm || [])
            var ys = (d.weights || []).concat(d.curveWeights || [])
            if (!xs.length || !ys.length) {
                ctx.fillStyle = "#98a2b3"
                ctx.font = "600 15px Segoe UI, Arial"
                ctx.textAlign = "center"
                ctx.fillText("No PSD data", r.x + r.w / 2, r.y + r.h / 2)
                ctx.restore()
                return
            }
            var xMin = Math.min.apply(Math, xs)
            var xMax = Math.max.apply(Math, xs)
            var yMax = Math.max.apply(Math, ys) * 1.15
            if (xMax <= xMin) {
                xMin -= 0.5
                xMax += 0.5
            }
            if (yMax <= 0)
                yMax = 1
            var xPad = (xMax - xMin) * 0.04
            xMin -= xPad
            xMax += xPad

            function xMap(x) { return left + (x - xMin) / (xMax - xMin) * plotW }
            function yMap(y) { return bottom - y / yMax * plotH }

            ctx.strokeStyle = "#202939"
            ctx.lineWidth = 1
            ctx.beginPath()
            ctx.moveTo(left, top)
            ctx.lineTo(left, bottom)
            ctx.lineTo(right, bottom)
            ctx.stroke()

            ctx.fillStyle = "#526070"
            ctx.font = "12px Segoe UI, Arial"
            ctx.textAlign = "center"
            ctx.textBaseline = "top"
            for (var tx = 0; tx <= 4; ++tx) {
                var xv = xMin + (xMax - xMin) * tx / 4
                var xp = xMap(xv)
                ctx.strokeStyle = "#edf1f6"
                ctx.beginPath()
                ctx.moveTo(xp, top)
                ctx.lineTo(xp, bottom)
                ctx.stroke()
                ctx.fillText(niceNumber(xv), xp, bottom + 5)
            }
            ctx.textAlign = "right"
            ctx.textBaseline = "middle"
            for (var ty = 0; ty <= 3; ++ty) {
                var yv = yMax * ty / 3
                var yp = yMap(yv)
                ctx.strokeStyle = "#edf1f6"
                ctx.beginPath()
                ctx.moveTo(left, yp)
                ctx.lineTo(right, yp)
                ctx.stroke()
                ctx.fillText(niceNumber(yv), left - 6, yp)
            }

            var diam = d.diametersMm || []
            var weights = d.weights || []
            var barW = plotW / Math.max(diam.length * 1.45, 4)
            ctx.fillStyle = d.color || "#d2b48c"
            ctx.strokeStyle = "#394150"
            ctx.lineWidth = 0.6
            for (var i = 0; i < diam.length; ++i) {
                var cx = xMap(diam[i])
                var by = yMap(weights[i])
                ctx.globalAlpha = 0.86
                ctx.fillRect(cx - barW / 2, by, barW, bottom - by)
                ctx.globalAlpha = 1.0
                ctx.strokeRect(cx - barW / 2, by, barW, bottom - by)
            }

            var curveX = d.curveDiametersMm || []
            var curveY = d.curveWeights || []
            if (curveX.length > 1 && curveY.length === curveX.length) {
                ctx.strokeStyle = "#9b111e"
                ctx.lineWidth = 2
                ctx.beginPath()
                ctx.moveTo(xMap(curveX[0]), yMap(curveY[0]))
                for (var c = 1; c < curveX.length; ++c)
                    ctx.lineTo(xMap(curveX[c]), yMap(curveY[c]))
                ctx.stroke()
            }

            var cov = d.coverage || 0
            var mx = d.maxWeight || 0
            var msg = cov < 0.5 ? "coverage " + (cov * 100).toFixed(0) + "% clipped"
                                : "coverage " + (cov * 100).toFixed(0) + "%"
            if (mx > 0.8)
                msg += "  near monodisperse"
            ctx.fillStyle = cov < 0.5 || mx > 0.8 ? "#991b1b" : "#697386"
            ctx.font = "12px Segoe UI, Arial"
            ctx.textAlign = "right"
            ctx.textBaseline = "top"
            ctx.fillText(msg, right, top + 4)

            ctx.textAlign = "center"
            ctx.textBaseline = "top"
            ctx.fillStyle = "#344054"
            ctx.font = "13px Segoe UI, Arial"
            ctx.fillText("grain diameter [mm]", left + plotW / 2, bottom + 22)
            ctx.save()
            ctx.translate(r.x + 15, top + plotH / 2)
            ctx.rotate(-Math.PI / 2)
            ctx.fillText("weight", 0, 0)
            ctx.restore()
            ctx.restore()
        }

        function drawLayer(ctx, d, r) {
            ctx.save()
            ctx.fillStyle = "#ffffff"
            ctx.strokeStyle = "#d8e0ea"
            ctx.lineWidth = 1
            ctx.fillRect(r.x, r.y, r.w, r.h)
            ctx.strokeRect(r.x, r.y, r.w, r.h)
            var isPolygon = d.shape === "polygon" || d.shape === "multipolygon"
            var poly = d.polygon || []
            var polyCount = d.shape === "multipolygon"
                ? ((d.polygons && d.polygons.length) ? d.polygons.length : 0)
                : 1
            var title = isPolygon
                ? (d.shape === "multipolygon"
                   ? "Multiple heterogeneity | " + polyCount + " polygons | "
                   : "Heterogeneity polygon | " + poly.length + " points | ")
                  + d.areaPercent.toFixed(0) + "% of domain"
                : "Heterogeneity patch | " + d.wxMm.toFixed(1) + " x " + d.wzMm.toFixed(1)
                  + " mm | " + d.areaPercent.toFixed(0) + "% of domain | "
                  + d.roughEdges + " rough edge(s)"
            drawTitle(ctx, title, r.x + r.w / 2, r.y + 20, 15, "#111827")

            var left = r.x + 62
            var right = r.x + r.w - 30
            var top = r.y + 48
            var bottom = r.y + r.h - 34
            var padX = Math.max(d.width * 0.05, 1e-6)
            var padZ = Math.max(d.height * 0.05, 1e-6)
            var xMin = -padX
            var xMax = d.width + padX
            var zMin = -padZ
            var zMax = d.height + padZ
            function xMap(x) { return left + (x - xMin) / (xMax - xMin) * (right - left) }
            function zMap(z) { return top + (z - zMin) / (zMax - zMin) * (bottom - top) }

            ctx.strokeStyle = "#ef4444"
            ctx.lineWidth = 3
            ctx.strokeRect(xMap(0), zMap(0), xMap(d.width) - xMap(0), zMap(d.height) - zMap(0))

            if (isPolygon && poly.length) {
                var polys = (d.polygons && d.polygons.length) ? d.polygons : [poly]
                for (var pp = 0; pp < polys.length; ++pp) {
                    var currentPoly = polys[pp]
                    if (!currentPoly || !currentPoly.length)
                        continue
                    if (currentPoly.length >= 3) {
                        ctx.globalAlpha = 0.24
                        ctx.fillStyle = "#2dd4bf"
                        ctx.strokeStyle = "#0f766e"
                        ctx.lineWidth = 2
                        ctx.beginPath()
                        ctx.moveTo(xMap(currentPoly[0].x), zMap(currentPoly[0].z))
                        for (var pi = 1; pi < currentPoly.length; ++pi)
                            ctx.lineTo(xMap(currentPoly[pi].x), zMap(currentPoly[pi].z))
                        ctx.closePath()
                        ctx.fill()
                        ctx.globalAlpha = 1
                        ctx.stroke()
                    } else {
                        ctx.strokeStyle = "#0f766e"
                        ctx.lineWidth = 2
                        ctx.beginPath()
                        ctx.moveTo(xMap(currentPoly[0].x), zMap(currentPoly[0].z))
                        for (var pj = 1; pj < currentPoly.length; ++pj)
                            ctx.lineTo(xMap(currentPoly[pj].x), zMap(currentPoly[pj].z))
                        ctx.stroke()
                    }
                    ctx.fillStyle = "#0f766e"
                    for (var pv = 0; pv < currentPoly.length; ++pv) {
                        ctx.beginPath()
                        ctx.arc(xMap(currentPoly[pv].x), zMap(currentPoly[pv].z), 4, 0, 2 * Math.PI)
                        ctx.fill()
                    }
                }
                var centers = d.centers || []
                ctx.fillStyle = "#f97316"
                for (var pc = 0; pc < centers.length; ++pc) {
                    ctx.beginPath()
                    ctx.arc(xMap(centers[pc].x), zMap(centers[pc].z), 5, 0, 2 * Math.PI)
                    ctx.fill()
                }
                var requested = d.requestedCenters || []
                ctx.fillStyle = "#f97316"
                ctx.font = "700 12px Segoe UI, Arial"
                ctx.textAlign = "left"
                ctx.textBaseline = "middle"
                for (var rc = 0; rc < requested.length; ++rc) {
                    var cxReq = xMap(requested[rc].x)
                    var czReq = zMap(requested[rc].z)
                    ctx.strokeStyle = "#f97316"
                    ctx.lineWidth = 2
                    ctx.beginPath()
                    ctx.moveTo(cxReq - 6, czReq)
                    ctx.lineTo(cxReq + 6, czReq)
                    ctx.moveTo(cxReq, czReq - 6)
                    ctx.lineTo(cxReq, czReq + 6)
                    ctx.stroke()
                    ctx.fillText(String(rc + 1), cxReq + 7, czReq)
                }
            } else {
                ctx.globalAlpha = 0.18
                ctx.fillStyle = "#4682b4"
                ctx.fillRect(xMap(d.xs), zMap(d.zs), xMap(d.xe) - xMap(d.xs), zMap(d.ze) - zMap(d.zs))
                ctx.globalAlpha = 1
                ctx.setLineDash([4, 4])
                ctx.strokeStyle = "#667085"
                ctx.lineWidth = 1
                ctx.strokeRect(xMap(d.xs), zMap(d.zs), xMap(d.xe) - xMap(d.xs), zMap(d.ze) - zMap(d.zs))
                ctx.setLineDash([])

                ctx.strokeStyle = "#000080"
                ctx.lineWidth = 2
                var curves = d.curves || []
                for (var i = 0; i < curves.length; ++i) {
                    var curve = curves[i]
                    if (!curve.x || curve.x.length < 2)
                        continue
                    ctx.beginPath()
                    ctx.moveTo(xMap(curve.x[0]), zMap(curve.z[0]))
                    for (var j = 1; j < curve.x.length; ++j)
                        ctx.lineTo(xMap(curve.x[j]), zMap(curve.z[j]))
                    ctx.stroke()
                }
            }

            ctx.fillStyle = "#526070"
            ctx.font = "12px Segoe UI, Arial"
            ctx.textAlign = "center"
            ctx.fillText("X [m]", (left + right) / 2, bottom + 18)
            ctx.save()
            ctx.translate(left - 42, (top + bottom) / 2)
            ctx.rotate(-Math.PI / 2)
            ctx.fillText("Z [m]", 0, 0)
            ctx.restore()
            ctx.restore()
        }

        function numberValue(name, fallback) {
            var v = root.formValues ? root.formValues[name] : undefined
            if (v === undefined || v === null || String(v).length === 0)
                return fallback
            var n = Number(v)
            return isFinite(n) ? n : fallback
        }

        function stringValue(name, fallback) {
            var v = root.formValues ? root.formValues[name] : undefined
            if (v === undefined || v === null || String(v).length === 0)
                return fallback
            return String(v)
        }

        function boolValue(name, fallback) {
            var v = root.formValues ? root.formValues[name] : undefined
            if (v === undefined || v === null)
                return fallback
            if (typeof v === "boolean")
                return v
            var s = String(v).toLowerCase()
            return s === "true" || s === "1" || s === "yes"
        }

        function tupleValue(name, fallback) {
            var v = root.formValues ? root.formValues[name] : undefined
            if (v === undefined || v === null || String(v).trim().length === 0)
                return fallback || []
            if (Array.isArray(v))
                return v.map(Number)
            var parts = String(v).replace(/;/g, ",").split(",")
            var out = []
            for (var i = 0; i < parts.length; ++i) {
                var n = Number(parts[i].trim())
                if (isFinite(n))
                    out.push(n)
            }
            return out
        }

        function tupleValueWithSeparators(name, fallback) {
            var v = root.formValues ? root.formValues[name] : undefined
            if (v === undefined || v === null || String(v).trim().length === 0)
                return fallback || []
            if (Array.isArray(v))
                return v.map(Number)
            var parts = String(v).replace(/;/g, ",").split(",")
            var out = []
            for (var i = 0; i < parts.length; ++i) {
                var s = parts[i].trim()
                if (!s.length)
                    continue
                var n = Number(s)
                out.push(isFinite(n) ? n : NaN)
            }
            return out
        }

        function normalizeWeights(weights) {
            var sum = 0
            for (var i = 0; i < weights.length; ++i)
                sum += Math.max(0, weights[i])
            if (sum <= 0) {
                var even = []
                for (var j = 0; j < weights.length; ++j)
                    even.push(1 / Math.max(1, weights.length))
                return even
            }
            var out = []
            for (var k = 0; k < weights.length; ++k)
                out.push(Math.max(0, weights[k]) / sum)
            return out
        }

        function psdFromForm(layer) {
            var p = layer ? "layer_" : ""
            var dist = stringValue(layer ? "layer_dist_type" : "dist_type", "lognormal")
            var rMin = numberValue(p + "r_min", layer ? 0.0005 : 0.0008)
            var rMax = numberValue(p + "r_max", layer ? 0.0008 : 0.0018)
            var n = Math.max(1, Math.round(numberValue(p + "num_sizes", layer ? 10 : 24)))
            var sigma = numberValue(p + "ln_sigma", layer ? 0.21 : 0.35)
            var median = numberValue(p + "ln_median", layer ? 0.0006 : 0.0009)
            var mean = layer ? numberValue("layer_nm_mean", 0.00065) : numberValue("nm_mean", 0.0012)
            var std = layer ? numberValue("layer_nm_std", 0.00005) : numberValue("nm_std", 0.0003)
            var radii = []
            var rawWeights = []

            if (!layer && dist === "custom") {
                radii = tupleValue("custom_radii", [])
                rawWeights = tupleValue("custom_weights", [])
                if (!radii.length || rawWeights.length !== radii.length) {
                    radii = [rMin, rMax]
                    rawWeights = [1, 1]
                }
            } else {
                for (var i = 0; i < n; ++i) {
                    var t = n === 1 ? 0.5 : i / (n - 1)
                    if (dist === "lognormal")
                        radii.push(Math.exp(Math.log(Math.max(rMin, 1e-12)) * (1 - t)
                                            + Math.log(Math.max(rMax, 1e-12)) * t))
                    else
                        radii.push(rMin + (rMax - rMin) * t)
                }
                for (var j = 0; j < radii.length; ++j) {
                    var r = Math.max(radii[j], 1e-12)
                    var pdf = 1
                    if (dist === "lognormal") {
                        var z = (Math.log(r) - Math.log(Math.max(median, 1e-12))) / Math.max(sigma, 1e-9)
                        pdf = Math.exp(-0.5 * z * z) / (r * Math.max(sigma, 1e-9))
                    } else if (dist === "normal") {
                        var zn = (r - mean) / Math.max(std, 1e-12)
                        pdf = Math.exp(-0.5 * zn * zn)
                    } else {
                        pdf = 1
                    }
                    rawWeights.push(pdf)
                }
            }

            var weights = normalizeWeights(rawWeights)
            var curveR = []
            var curveW = []
            var curveRaw = []
            for (var c = 0; c < 160; ++c) {
                var tc = c / 159
                var cr = rMin + (rMax - rMin) * tc
                if (dist === "lognormal")
                    cr = Math.exp(Math.log(Math.max(rMin, 1e-12)) * (1 - tc)
                                  + Math.log(Math.max(rMax, 1e-12)) * tc)
                curveR.push(cr)
                var cp = 1
                if (dist === "lognormal") {
                    var cz = (Math.log(Math.max(cr, 1e-12)) - Math.log(Math.max(median, 1e-12))) / Math.max(sigma, 1e-9)
                    cp = Math.exp(-0.5 * cz * cz) / Math.max(cr * sigma, 1e-12)
                } else if (dist === "normal") {
                    var cn = (cr - mean) / Math.max(std, 1e-12)
                    cp = Math.exp(-0.5 * cn * cn)
                } else {
                    cp = 1
                }
                curveRaw.push(cp)
            }
            var maxCurve = Math.max.apply(Math, curveRaw)
            var maxWeight = Math.max.apply(Math, weights)
            for (var q = 0; q < curveRaw.length; ++q)
                curveW.push(maxCurve > 0 ? curveRaw[q] / maxCurve * maxWeight : 0)

            return {
                title: layer ? "Heterogeneity grains PSD (sampled)" : "Matrix PSD (sampled)",
                color: layer ? "#4682b4" : "#d2b48c",
                diametersMm: radii.map(function(r) { return 2 * r * 1e3 }),
                weights: weights,
                curveDiametersMm: curveR.map(function(r) { return 2 * r * 1e3 }),
                curveWeights: curveW,
                coverage: 1.0,
                maxWeight: maxWeight
            }
        }

        function layerFromForm() {
            var width = numberValue("width", 0.05)
            var height = numberValue("height", 0.06)
            var medium = stringValue("medium_type", "homogeneous")
            var multiple = medium === "multiple"
            var shape = multiple ? "polygon" : stringValue("layer_shape", "rectangle")
            if (shape === "polygon") {
                var raw = tupleValue("layer_polygon", [])
                var poly = []
                for (var pi = 0; pi + 1 < raw.length; pi += 2) {
                    poly.push({
                        x: Math.max(0, Math.min(width, raw[pi])),
                        z: Math.max(0, Math.min(height, raw[pi + 1]))
                    })
                }
                function polygonArea(points) {
                    if (!points || points.length < 3)
                        return 0
                    var total = 0
                    for (var ai = 0; ai < points.length; ++ai) {
                        var an = points[(ai + 1) % points.length]
                        total += points[ai].x * an.z - an.x * points[ai].z
                    }
                    return Math.abs(total) * 0.5
                }
                function polygonCenter(points) {
                    if (!points || !points.length)
                        return null
                    if (points.length < 3) {
                        var sx = 0
                        var sz = 0
                        for (var ci = 0; ci < points.length; ++ci) {
                            sx += points[ci].x
                            sz += points[ci].z
                        }
                        return { x: sx / points.length, z: sz / points.length }
                    }
                    var area2 = 0
                    var cx = 0
                    var cz = 0
                    for (var gi = 0; gi < points.length; ++gi) {
                        var gn = points[(gi + 1) % points.length]
                        var cross = points[gi].x * gn.z - gn.x * points[gi].z
                        area2 += cross
                        cx += (points[gi].x + gn.x) * cross
                        cz += (points[gi].z + gn.z) * cross
                    }
                    if (Math.abs(area2) < 1e-18) {
                        var ax = 0
                        var az = 0
                        for (var bi = 0; bi < points.length; ++bi) {
                            ax += points[bi].x
                            az += points[bi].z
                        }
                        return { x: ax / points.length, z: az / points.length }
                    }
                    return { x: cx / (3 * area2), z: cz / (3 * area2) }
                }
                function translatedPolygon(points, center) {
                    var base = polygonCenter(points)
                    if (!base || !center)
                        return []
                    var dx = center.x - base.x
                    var dz = center.z - base.z
                    var moved = []
                    var minX = 1e99
                    var maxX = -1e99
                    var minZ = 1e99
                    var maxZ = -1e99
                    for (var ti = 0; ti < points.length; ++ti) {
                        var mx = points[ti].x + dx
                        var mz = points[ti].z + dz
                        moved.push({ x: mx, z: mz })
                        minX = Math.min(minX, mx)
                        maxX = Math.max(maxX, mx)
                        minZ = Math.min(minZ, mz)
                        maxZ = Math.max(maxZ, mz)
                    }
                    if (minX < 0)
                        dx -= minX
                    if (maxX > width)
                        dx -= maxX - width
                    if (minZ < 0)
                        dz -= minZ
                    if (maxZ > height)
                        dz -= maxZ - height
                    var out = []
                    for (var oi = 0; oi < points.length; ++oi) {
                        out.push({
                            x: Math.max(0, Math.min(width, points[oi].x + dx)),
                            z: Math.max(0, Math.min(height, points[oi].z + dz))
                        })
                    }
                    return out
                }

                var polygons = []
                var centers = []
                var centerRaw = tupleValue("heterogeneity_centers", [])
                var requestedCenters = []
                for (var ri = 0; ri + 1 < centerRaw.length; ri += 2) {
                    requestedCenters.push({
                        x: Math.max(0, Math.min(width, centerRaw[ri])),
                        z: Math.max(0, Math.min(height, centerRaw[ri + 1]))
                    })
                }
                if (multiple) {
                    var groupedRaw = tupleValueWithSeparators("heterogeneity_polygons", [])
                    var currentGroup = []
                    function pushGroup() {
                        if (currentGroup.length >= 3 && polygonArea(currentGroup) > 0) {
                            polygons.push(currentGroup)
                            centers.push(polygonCenter(currentGroup))
                        }
                        currentGroup = []
                    }
                    for (var gi = 0; gi + 1 < groupedRaw.length; gi += 2) {
                        if (!isFinite(groupedRaw[gi]) || !isFinite(groupedRaw[gi + 1])) {
                            pushGroup()
                        } else {
                            currentGroup.push({
                                x: Math.max(0, Math.min(width, groupedRaw[gi])),
                                z: Math.max(0, Math.min(height, groupedRaw[gi + 1]))
                            })
                        }
                    }
                    pushGroup()
                    if (requestedCenters.length && polygons.length > requestedCenters.length) {
                        polygons = polygons.slice(0, requestedCenters.length)
                        centers = centers.slice(0, requestedCenters.length)
                    }
                } else {
                    if (poly.length >= 3 && polygonArea(poly) > 0) {
                        var singleCenter = polygonCenter(poly)
                        if (singleCenter)
                            centers.push(singleCenter)
                    }
                }

                var displayPolys = multiple ? polygons : [poly]
                var xsP = 0
                var xeP = 0
                var zsP = 0
                var zeP = 0
                var totalArea = 0
                var havePoly = false
                for (var dp = 0; dp < displayPolys.length; ++dp) {
                    var displayPoly = displayPolys[dp]
                    if (!displayPoly || !displayPoly.length)
                        continue
                    totalArea += polygonArea(displayPoly)
                    for (var bp = 0; bp < displayPoly.length; ++bp) {
                        if (!havePoly) {
                            xsP = xeP = displayPoly[bp].x
                            zsP = zeP = displayPoly[bp].z
                            havePoly = true
                        } else {
                            xsP = Math.min(xsP, displayPoly[bp].x)
                            xeP = Math.max(xeP, displayPoly[bp].x)
                            zsP = Math.min(zsP, displayPoly[bp].z)
                            zeP = Math.max(zeP, displayPoly[bp].z)
                        }
                    }
                }
                return {
                    shape: multiple ? "multipolygon" : "polygon",
                    width: width, height: height,
                    polygon: poly,
                    activePolygon: poly,
                    polygons: multiple ? polygons : [],
                    centers: centers,
                    requestedCenters: requestedCenters,
                    missingCount: multiple ? Math.max(0, requestedCenters.length - polygons.length) : 0,
                    xs: xsP, xe: xeP, zs: zsP, ze: zeP,
                    wxMm: (xeP - xsP) * 1e3, wzMm: (zeP - zsP) * 1e3,
                    areaPercent: width * height > 0 ? 100 * totalArea / (width * height) : 0,
                    roughEdges: 0,
                    curves: []
                }
            }
            var xs = Math.max(0, Math.min(width, numberValue("layer_x_start", 0.020)))
            var xe = Math.max(0, Math.min(width, numberValue("layer_x_end", 0.030)))
            var zs = Math.max(0, Math.min(height, numberValue("layer_z_start", 0)))
            var ze = Math.max(0, Math.min(height, numberValue("layer_z_end", 0.060)))
            if (xe < xs) { var tx = xs; xs = xe; xe = tx }
            if (ze < zs) { var tz = zs; zs = ze; ze = tz }
            var amp = numberValue("interface_amplitude", 0)
            var freqsManual = tupleValue("interface_freqs", [])
            var rough = boolValue("rough_interface", true)
            // adaptive interface (mirrors make_layer_geometry): amplitude <= 0
            // -> mean matrix grain radius capped at a quarter of the band's
            // narrow side; blank freqs -> wavelength ~ 8 matrix radii per edge
            var rMean = 0
            if (rough && (amp <= 0 || !freqsManual.length)) {
                var psd = psdFromForm(false)
                var wSum = 0
                for (var ri = 0; ri < psd.diametersMm.length; ++ri) {
                    rMean += (psd.diametersMm[ri] / 2000.0) * psd.weights[ri]
                    wSum += psd.weights[ri]
                }
                rMean = wSum > 0 ? rMean / wSum : 0
            }
            if (rough && amp <= 0 && rMean > 0) {
                var narrowSide = Math.min(xe - xs, ze - zs)
                if (narrowSide <= 0)
                    narrowSide = Math.max(xe - xs, ze - zs)
                amp = narrowSide > 0 ? Math.min(rMean, 0.25 * narrowSide) : rMean
            }
            function edgeFreqs(length) {
                if (freqsManual.length)
                    return freqsManual
                if (!rough || rMean <= 0 || length <= 0)
                    return []
                var n1 = Math.min(Math.max(length / (8.0 * rMean), 1.0), 60.0)
                return [n1, 2.6 * n1, 5.3 * n1]
            }
            var tol = 1e-9
            var edgeInternal = {
                x_lo: xs > tol,
                x_hi: xe < width - tol,
                z_lo: zs > tol,
                z_hi: ze < height - tol
            }
            function taper(t) { return Math.pow(Math.sin(Math.PI * t), 2) }
            function offset(coord, lo, hi) {
                if (!rough || hi <= lo)
                    return 0
                var freqs = edgeFreqs(hi - lo)
                if (!freqs.length)
                    return 0
                var t = (coord - lo) / (hi - lo)
                var raw = 0
                for (var i = 0; i < freqs.length; ++i)
                    raw += Math.sin(2 * Math.PI * freqs[i] * t + i * 1.7) / freqs.length
                return Math.abs(raw) * amp * taper(t)
            }
            function sample(lo, hi, n) {
                var out = []
                for (var i = 0; i < n; ++i)
                    out.push(lo + (hi - lo) * i / (n - 1))
                return out
            }
            var curves = []
            var vals
            if (edgeInternal.x_lo) {
                vals = sample(zs, ze, 120)
                curves.push({ x: vals.map(function(z) { return xs + offset(z, zs, ze) }), z: vals })
            }
            if (edgeInternal.x_hi) {
                vals = sample(zs, ze, 120)
                curves.push({ x: vals.map(function(z) { return xe - offset(z, zs, ze) }), z: vals })
            }
            if (edgeInternal.z_lo) {
                vals = sample(xs, xe, 120)
                curves.push({ x: vals, z: vals.map(function(x) { return zs + offset(x, xs, xe) }) })
            }
            if (edgeInternal.z_hi) {
                vals = sample(xs, xe, 120)
                curves.push({ x: vals, z: vals.map(function(x) { return ze - offset(x, xs, xe) }) })
            }
            var area = Math.max(0, xe - xs) * Math.max(0, ze - zs)
            return {
                shape: "rectangle",
                width: width, height: height, xs: xs, xe: xe, zs: zs, ze: ze,
                wxMm: (xe - xs) * 1e3, wzMm: (ze - zs) * 1e3,
                areaPercent: width * height > 0 ? 100 * area / (width * height) : 0,
                roughEdges: Number(edgeInternal.x_lo) + Number(edgeInternal.x_hi)
                            + Number(edgeInternal.z_lo) + Number(edgeInternal.z_hi),
                curves: curves
            }
        }

        function livePreviewData() {
            var width = numberValue("width", 0.05)
            var height = numberValue("height", 0.06)
            if (root.activeTab === "Domain") {
                var domainMode = stringValue("medium_type", "homogeneous")
                var domainIsLayer = domainMode === "layer" || domainMode === "multiple"
                return {
                    mode: "domain",
                    width: width,
                    height: height,
                    widthMm: width * 1e3,
                    heightMm: height * 1e3,
                    layer: domainIsLayer ? layerFromForm() : null
                }
            }
            var mode = stringValue("medium_type", "homogeneous")
            var isLayer = mode === "layer" || mode === "multiple"
            var out = {
                mode: "combined",
                isLayer: isLayer,
                matrix: psdFromForm(false)
            }
            if (isLayer) {
                out.fine = psdFromForm(true)
                out.layer = layerFromForm()
            }
            return out
        }

        onPaint: {
            var ctx = getContext("2d")
            ctx.reset()
            ctx.clearRect(0, 0, width, height)
            ctx.fillStyle = "#ffffff"
            ctx.fillRect(0, 0, width, height)
            var d = livePreviewData()
            root.domainActive = (d.mode === "domain")
            if (!d.mode) {
                drawTitle(ctx, "Preview", width / 2, height / 2, 20, "#98a2b3")
                return
            }
            if (d.mode === "domain") {
                drawDomain(ctx, d)
                return
            }
            if (d.isLayer) {
                var gap = 14
                var topH = Math.max(150, height * 0.34)
                drawPsd(ctx, d.matrix, { x: 0, y: 0, w: (width - gap) / 2, h: topH })
                drawPsd(ctx, d.fine, { x: (width + gap) / 2, y: 0, w: (width - gap) / 2, h: topH })
                drawLayer(ctx, d.layer, { x: 0, y: topH + gap, w: width, h: height - topH - gap })
            } else {
                drawPsd(ctx, d.matrix, { x: width * 0.08, y: height * 0.08,
                                         w: width * 0.84, h: height * 0.82 })
            }
        }

        // ---- interactive polygon editing on the domain sketch -------------
        MouseArea {
            id: editArea
            anchors.fill: parent
            enabled: root.interactive && !root.locked
            acceptedButtons: Qt.LeftButton | Qt.RightButton
            cursorShape: (root.drawing || (root.controller && root.controller.addCentersMode))
                         ? Qt.CrossCursor : Qt.ArrowCursor

            property int dragPointIndex: -1
            property bool dragStarted: false
            property real pressX: 0
            property real pressY: 0
            property bool shapeDragging: false
            property real lastDomX: 0
            property real lastDomZ: 0

            function toDomainX(mx) {
                return Math.max(0, Math.min(root.domW,
                    (mx - root.domRx) / Math.max(root.domRectW, 1) * root.domW))
            }

            function toDomainZ(my) {
                return Math.max(0, Math.min(root.domH,
                    (my - root.domRy) / Math.max(root.domRectH, 1) * root.domH))
            }

            function toScreenX(x) { return root.domRx + x / root.domW * root.domRectW }
            function toScreenZ(z) { return root.domRy + z / root.domH * root.domRectH }

            function nearestPointIndex(mx, my, maxPx) {
                var vals = root.controller ? root.controller.polygonValues() : []
                var best = -1
                var bestD = maxPx
                for (var i = 0; i + 1 < vals.length; i += 2) {
                    var dx = toScreenX(vals[i]) - mx
                    var dy = toScreenZ(vals[i + 1]) - my
                    var d = Math.sqrt(dx * dx + dy * dy)
                    if (d <= bestD) {
                        bestD = d
                        best = i / 2
                    }
                }
                return best
            }

            onPressed: function(mouse) {
                dragPointIndex = -1
                dragStarted = false
                shapeDragging = false
                if (!root.domainActive || !root.controller)
                    return
                var x = toDomainX(mouse.x)
                var z = toDomainZ(mouse.y)

                // right button: hold and drag to move a polygon (the active
                // one, or any other polygon -- it becomes the active one)
                if (mouse.button === Qt.RightButton) {
                    if (root.controller.beginPolygonMoveAt(x, z)) {
                        shapeDragging = true
                        lastDomX = x
                        lastDomZ = z
                    }
                    return
                }

                var hit = nearestPointIndex(mouse.x, mouse.y, 16)
                if (hit >= 0) {
                    dragPointIndex = hit
                    pressX = mouse.x
                    pressY = mouse.y
                    return
                }
                if (root.drawing) {
                    root.controller.addPolygonPoint(x, z)
                    return
                }
                root.controller.canvasEmptyClicked(x, z)
            }

            onPositionChanged: function(mouse) {
                if (!root.domainActive || !root.controller)
                    return
                if (dragPointIndex >= 0) {
                    if (!dragStarted) {
                        var dx = mouse.x - pressX
                        var dy = mouse.y - pressY
                        if (dx * dx + dy * dy < 25)
                            return
                        dragStarted = true
                    }
                    root.controller.movePolygonPoint(dragPointIndex,
                                                     toDomainX(mouse.x), toDomainZ(mouse.y))
                    return
                }
                if (shapeDragging) {
                    var x = toDomainX(mouse.x)
                    var z = toDomainZ(mouse.y)
                    var moved = root.controller.moveActivePolygonBy(x - lastDomX, z - lastDomZ)
                    lastDomX += moved[0]
                    lastDomZ += moved[1]
                }
            }

            onReleased: function(mouse) {
                if (root.controller && dragPointIndex >= 0) {
                    if (!dragStarted)
                        root.controller.removePolygonPoint(dragPointIndex)
                    else
                        root.controller.movePolygonPoint(dragPointIndex,
                                                         toDomainX(mouse.x), toDomainZ(mouse.y))
                }
                dragPointIndex = -1
                dragStarted = false
                shapeDragging = false
            }
        }
    }
}
