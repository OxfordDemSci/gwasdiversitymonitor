
window.__dictComboDebug = window.__dictComboDebug || {
    format: null,
    includePrecomputed: null,
    decodedRows: {},
    canvasFixedGeometry: false
};

function __dcDecodeStage(stagePayload, stageName) {
    if (!stagePayload) return [];
    if (stagePayload.__decoded_rows) return stagePayload.__decoded_rows;

    var columns = stagePayload.columns || [];
    var dicts = stagePayload.dicts || {};
    var codes = stagePayload.codes || {};
    var meta = stagePayload.meta || {};
    var n = meta.rowCount || 0;
    var out = new Array(n);

    for (var i = 0; i < n; i++) {
        var obj = {};
        for (var j = 0; j < columns.length; j++) {
            var c = columns[j];
            obj[c] = (dicts[c] || [])[(codes[c] || [])[i]];
        }

        if (obj.N !== undefined && obj.N !== null && obj.N !== "") obj.N = +obj.N;
        obj.__Nnum = +(obj.__Nnum !== undefined ? obj.__Nnum : obj.N) || 0;

        if (obj.__dateMS !== undefined && obj.__dateMS !== null && obj.__dateMS !== "") {
            obj.__dateMS = +obj.__dateMS;
        } else if (obj.DATE) {
            obj.__dateMS = +new Date(obj.DATE);
        }

        out[i] = obj;
    }

    out.__dcMeta = {
        minYear: meta.minDate ? new Date(meta.minDate) : (meta.minDateMS ? new Date(meta.minDateMS) : null),
        maxYear: meta.maxDate ? new Date(meta.maxDate) : (meta.maxDateMS ? new Date(meta.maxDateMS) : null),
        maxN: meta.maxN ? +meta.maxN : null,
        rowCount: n,
        includePrecomputed: !!meta.includePrecomputed
    };

    stagePayload.__decoded_rows = out;

    window.__dictComboDebug.format = "dict_columnar_v2";
    window.__dictComboDebug.includePrecomputed = !!meta.includePrecomputed;
    window.__dictComboDebug.decodedRows[stageName || "unknown"] = n;

    return out;
}

function __dcSelectData(data, replication) {
    if (data && data.__format === "dict_columnar_v2") {
        return __dcDecodeStage(replication ? data.bubblegraph_replication : data.bubblegraph_initial, replication ? "replication" : "initial");
    }

    data = replication ? data.bubblegraph_replication : data.bubblegraph_initial;
    if (Array.isArray(data)) return data;
    return Object.keys(data).map(function(k) { return data[k]; });
}

function __dcNormaliseClassValue(v) {
    return String(v || "").trim().replace(/\s+/g, "-").replace(/\//g, "-").replace(/,/g, "").toLowerCase();
}

function __dcBroaderClass(v) {
    return __dcNormaliseClassValue(v);
}

function __dcParentTermClass(v) {
    return String(v || "").replace(/, /g, ',').replace(/ /g, '-').replace(/,/g, ' ').toLowerCase();
}

function __dcClass(d) {
    return d.__class || (__dcBroaderClass(d.Broader) + " " + __dcParentTermClass(d.parentterm));
}

function __dcTrait(d) {
    return d.__trait || String(d.DiseaseOrTrait || "").replace(/ /g, '-').replace('>', 'more than').replace('<', 'less than').replace(/\(/g, '').replace(/\)/g, '').toLowerCase();
}

function __dcDiseaseClean(d) {
    return d.__DiseaseOrTraitClean || String(d.DiseaseOrTrait || "").replace('>', 'more than').replace('<', 'less than');
}

function __dcCsvEscape(value) {
    if (value === undefined || value === null) return "";

    var text = String(value);
    if (/[",\r\n]/.test(text)) {
        return '"' + text.replace(/"/g, '""') + '"';
    }

    return text;
}

function __dcDownloadText(filename, text, mimeType) {
    var blob = new Blob([text], { type: mimeType || "text/plain;charset=utf-8" });
    var url = URL.createObjectURL(blob);
    var link = document.createElement("a");

    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
}

function __dcBubbleCsvValue(row, column) {
    if (column === "cssclassname") return __dcClass(row);
    if (column === "trait") return __dcTrait(row);
    if (column === "N") return __dcN(row);
    if (column === "DiseaseOrTrait") return __dcDiseaseClean(row);

    return row[column];
}

function __dcDownloadBubbleCsv() {
    var state = window.__bubbleCanvasState;
    var points = state && state.points ? state.points : [];
    var selectedTraits = state && state.selectedTraits ? state.selectedTraits : [];
    var exportPoints = selectedTraits.length ? points.filter(function (p) { return p.traitOk; }) : points;
    var columns = ["", "Broader", "N", "PUBMEDID", "AUTHOR", "parentterm", "STAGE", "DATE", "ACCESSION", "DiseaseOrTrait", "cssclassname", "trait"];
    var lines = [columns.map(__dcCsvEscape).join(",")];

    for (var i = 0; i < exportPoints.length; i++) {
        var p = exportPoints[i];
        var row = p.d;

        lines.push(columns.map(function (column) {
            if (column === "") return p.index;
            return __dcCsvEscape(__dcBubbleCsvValue(row, column));
        }).join(","));
    }

    __dcDownloadText("bubble_df.csv", lines.join("\n") + "\n", "text/csv;charset=utf-8");
}

function __dcN(d) {
    return +(d.__Nnum !== undefined ? d.__Nnum : d.N) || 0;
}

function __dcDateValue(d) {
    return +(d.__dateMS !== undefined ? d.__dateMS : new Date(d.DATE));
}

function __dcPatchGetYearAndDataMax() {
    if (window.__dcPatchedGetYearAndDataMax) return;
    window.__dcPatchedGetYearAndDataMax = true;

    var oldGetYear = getYear;
    var oldGetDataMax = getDataMax;

    getYear = function(data) {
        if (data && data.__dcMeta && data.__dcMeta.minYear && data.__dcMeta.maxYear) {
            return { minYear: data.__dcMeta.minYear, maxYear: data.__dcMeta.maxYear };
        }

        if (data && data.__dcYearCache) return data.__dcYearCache;

        var result = oldGetYear(data);
        try {
            Object.defineProperty(data, "__dcYearCache", { value: result, enumerable: false, configurable: true });
        } catch (e) {
            data.__dcYearCache = result;
        }
        return result;
    };

    getDataMax = function(data, filters) {
        if (filters === undefined && data && data.__dcMeta && data.__dcMeta.maxN) {
            return data.__dcMeta.maxN;
        }

        var key = filters || "__NO_FILTER__";

        if (data && data.__dcMaxCache && data.__dcMaxCache[key] !== undefined) {
            return data.__dcMaxCache[key];
        }

        var result = oldGetDataMax(data, filters);

        try {
            if (!data.__dcMaxCache) {
                Object.defineProperty(data, "__dcMaxCache", { value: {}, enumerable: false, configurable: true });
            }
        } catch (e) {
            if (!data.__dcMaxCache) data.__dcMaxCache = {};
        }

        data.__dcMaxCache[key] = result;
        return result;
    };
}


function drawBubbleGraph(selector, data, replication, preserveFilters) {
    __dcPatchGetYearAndDataMax();

    var rawPayload = data;
    data = __dcSelectData(data, replication);

    var bubbleGraph = $(selector);
    if (window.__bubbleCanvasState && window.__bubbleCanvasState.resizeObserver) {
        window.__bubbleCanvasState.resizeObserver.disconnect();
    }
    $(window).off("resize.canvasBubbleLayout");

    bubbleGraph.css("position", "relative");
    bubbleGraph.find("#bubbleCanvas").remove();

    var tickMax = 8;
    var graphHeight = ($(window).width() < 480) ? 300 : 360;
    var margin = {top: 40, right: 30, bottom: 30, left: 40};
    var width = bubbleGraph.find('.left').width() - margin.left - margin.right;
    var height = graphHeight - margin.top - margin.bottom;

    if (!width || width < 200) {
        width = Math.max(320, bubbleGraph.width() - margin.left - margin.right - 260);
    }

    sanitiseSVG(selector, preserveFilters);

    let svg_id = 'bubbleSVG';
    let svg_selector = `#${svg_id}`;

    var mainSvg = d3.select(selector)
        .append("svg")
        .attr("id", svg_id)
        .attr("class", "term-all canvas-backed")
        .attr("width", width + margin.left + margin.right)
        .attr("height", height + margin.top + margin.bottom);

    makeChartResponsive(
        mainSvg,
        width + margin.left + margin.right,
        height + margin.top + margin.bottom
    );

    mainSvg.append('rect')
        .attr('class', 'white-rect')
        .attr('fill', '#ffff')
        .attr('style', 'fill: white;')
        .attr('height', height + margin.top + margin.bottom)
        .attr('width', width + margin.left + margin.right);

    var svg = mainSvg.append("g")
        .attr("class", "svg-container")
        .attr("transform", "translate(" + margin.left + "," + margin.top + ")");

    var yearExtent = getYear(data);
    var minYear = yearExtent.minYear;
    var maxYear = yearExtent.maxYear;

    const xScale = d3.scaleTime()
        .domain([minYear, maxYear])
        .range([0, width]);

    svg.append("g")
        .attr('class', 'axis-x')
        .attr("transform", "translate(0," + height + ")")
        .call(d3.axisBottom(xScale).ticks(6));

    var max = getDataMax(data);

    const yScale = d3.scaleLinear()
        .domain([0, max])
        .range([height, 0]);

    var sizeScale = d3.scalePow()
        .exponent(2)
        .domain([0, max])
        .range([5, 40]);

    var maxRadius = Math.ceil(sizeScale(max)) + 4;

    svg.append('g')
        .attr('class', 'grid')
        .call(d3.axisLeft(yScale)
            .ticks(tickMax)
            .tickSize(-width)
            .tickFormat('')
        );

    svg.append("g")
        .attr("class", "axis-y")
        .call(d3.axisLeft(yScale).ticks(tickMax, "s"));

    var bubbleDataGroup = svg.append("g").attr("id", "bubbleData");

    bubbleDataGroup.append("rect")
        .attr("class", "background")
        .attr("width", width)
        .attr("height", height)
        .attr("fill", "white")
        .attr("opacity", 0)
        .attr("onmouseover", "backgroundMouseOver(evt)")
        .attr("onclick", "clearSelected()");

    var proxyCircle = bubbleDataGroup.append("circle")
        .attr("id", "canvasProxyCircle")
        .attr("class", "canvas-proxy")
        .attr("cx", -9999)
        .attr("cy", -9999)
        .attr("r", 0)
        .style("display", "none")
        .node();

    var colourProbe = bubbleDataGroup.append("circle")
        .attr("id", "canvasColourProbe")
        .attr("cx", -9999)
        .attr("cy", -9999)
        .attr("r", 1)
        .style("visibility", "hidden")
        .node();

    var leftPanel = bubbleGraph.find(".left")[0] || bubbleGraph[0];
    leftPanel.style.position = "relative";
    leftPanel.style.overflow = "visible";

    var canvas = document.createElement("canvas");
    canvas.id = "bubbleCanvas";
    canvas.className = "bubble-canvas";

    var ctx = canvas.getContext("2d", { alpha: true });
    var dpr = window.devicePixelRatio || 1;

    var canvasW = width + 2 * maxRadius;
    var canvasH = height + 2 * maxRadius;

    canvas.style.position = "absolute";
    canvas.style.width = canvasW + "px";
    canvas.style.height = canvasH + "px";
    canvas.style.zIndex = 5;
    canvas.style.pointerEvents = "auto";
    canvas.style.cursor = "default";
    canvas.style.background = "transparent";

    canvas.width = Math.round(canvasW * dpr);
    canvas.height = Math.round(canvasH * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

    leftPanel.appendChild(canvas);

    function alignCanvasToPlot() {
        var leftRect = leftPanel.getBoundingClientRect();
        var svgRect = mainSvg.node().getBoundingClientRect();
        var viewBox = mainSvg.node().viewBox.baseVal;
        var scale = viewBox.width ? Math.min(svgRect.width / viewBox.width, svgRect.height / viewBox.height) : 1;

        canvas.style.left = (svgRect.left - leftRect.left + (margin.left - maxRadius) * scale) + "px";
        canvas.style.top = (svgRect.top - leftRect.top + (margin.top - maxRadius) * scale) + "px";
        canvas.style.width = (canvasW * scale) + "px";
        canvas.style.height = (canvasH * scale) + "px";
    }

    alignCanvasToPlot();
    requestAnimationFrame(alignCanvasToPlot);

    var state = {
        selector: selector,
        data: data,
        rawPayload: rawPayload,
        replication: replication,
        xScale: xScale,
        yScale: yScale,
        sizeScale: sizeScale,
        width: width,
        height: height,
        margin: margin,
        pad: maxRadius,
        canvasW: canvasW,
        canvasH: canvasH,
        canvas: canvas,
        ctx: ctx,
        proxyCircle: proxyCircle,
        colourProbe: colourProbe,
        points: [],
        grid: new Map(),
        cellSize: Math.max(56, maxRadius * 2),
        selectedIndex: null,
        drawCount: 0,
        visibleCount: 0
    };

    window.__bubbleCanvasState = state;
    window.__dictComboDebug.canvasFixedGeometry = true;

    if (window.ResizeObserver) {
        state.resizeObserver = new ResizeObserver(function() {
            if (state.resizeFrame) cancelAnimationFrame(state.resizeFrame);
            state.resizeFrame = requestAnimationFrame(alignCanvasToPlot);
        });
        state.resizeObserver.observe(mainSvg.node());
    }

    var initialPanelWidth = Math.round(bubbleGraph.find(".left").width());
    var initialPixelRatio = window.devicePixelRatio || 1;
    $(window).on("resize.canvasBubbleLayout", function() {
        clearTimeout(state.resizeTimer);
        state.resizeTimer = setTimeout(function() {
            var nextPanelWidth = Math.round(bubbleGraph.find(".left").width());
            var nextPixelRatio = window.devicePixelRatio || 1;

            if (Math.abs(nextPanelWidth - initialPanelWidth) > 2 ||
                Math.abs(nextPixelRatio - initialPixelRatio) > 0.01) {
                drawBubbleGraph(selector, rawPayload, replication, true);
                return;
            }

            alignCanvasToPlot();
        }, 180);
    });

    function cellKey(cx, cy) {
        return cx + "," + cy;
    }

    function addToGrid(p) {
        var cs = state.cellSize;
        var cx = Math.floor(p.x / cs);
        var cy = Math.floor(p.y / cs);
        var key = cellKey(cx, cy);

        var arr = state.grid.get(key);
        if (!arr) {
            arr = [];
            state.grid.set(key, arr);
        }
        arr.push(p);
    }

    var colourCache = {};

    function getColour(className, flags) {
        var fullClass = className || "";
        if (flags && flags.disabled) fullClass += " disabled";
        if (flags && flags.opaque) fullClass += " opaque";
        if (flags && flags.selected) fullClass += " selected";

        if (colourCache[fullClass]) return colourCache[fullClass];

        colourProbe.setAttribute("class", fullClass);
        var cs = window.getComputedStyle(colourProbe);

        var fill = cs.fill;
        var stroke = cs.stroke;
        var opacity = parseFloat(cs.opacity);

        if (!fill || fill === "none" || fill === "rgba(0, 0, 0, 0)") {
            fill = cs.color || "#4682b4";
        }

        if (!stroke || stroke === "none") {
            stroke = fill;
        }

        if (!isFinite(opacity)) opacity = 1;

        var result = { fill: fill, stroke: stroke, opacity: opacity };
        colourCache[fullClass] = result;
        return result;
    }

    function getCurrentTraitSelection() {
        var v = $(selector).find(".filter select[name='trait']").val();
        if (!v) return [];
        if (Array.isArray(v)) return v;
        return [v];
    }

    function currentParentFilter() {
        var v = $(selector).find(".filter select[name='parentTerms']").val();
        if (!v) return "all";
        return String(v);
    }

    function currentAncestryFilters() {
        var vals = [];
        $(selector).find(".ancestry-filter .option.active").each(function() {
            var f = $(this).attr("dataFilter");
            if (f && f !== "all") vals.push(String(f));
        });
        return vals;
    }

    function normaliseFilterValue(v) {
        return __dcNormaliseClassValue(v);
    }

    function rowMatchesParent(d, parentFilter) {
        if (!parentFilter || parentFilter === "all") return true;
        return __dcClass(d).indexOf(normaliseFilterValue(parentFilter)) !== -1;
    }

    function rowMatchesAncestry(d, ancestryFilters) {
        if (!ancestryFilters || ancestryFilters.length === 0) return true;

        var broader = d.__BroaderClass || __dcBroaderClass(d.Broader);
        for (var i = 0; i < ancestryFilters.length; i++) {
            var f = normaliseFilterValue(ancestryFilters[i]);
            if (broader === f) return false;
        }

        return true;
    }

    function rowMatchesTrait(d, selectedTraits) {
        if (!selectedTraits || selectedTraits.length === 0) return true;
        return selectedTraits.indexOf(__dcTrait(d)) !== -1;
    }

    function computeFilteredMax(parentFilter, ancestryFilters, selectedTraits) {
        var m = 0;

        for (var i = 0; i < data.length; i++) {
            var d = data[i];

            if (!rowMatchesParent(d, parentFilter)) continue;
            if (!rowMatchesAncestry(d, ancestryFilters)) continue;
            if (!rowMatchesTrait(d, selectedTraits)) continue;

            var n = __dcN(d);
            if (n > m) m = n;
        }

        return m || max;
    }

    function setProxyFromPoint(p) {
        var d = p.d;

        proxyCircle.setAttribute("class", p.className);
        proxyCircle.setAttribute("pubmedid", d.PUBMEDID);
        proxyCircle.setAttribute("author", d.AUTHOR);
        proxyCircle.setAttribute("accession", d.ACCESSION);
        proxyCircle.setAttribute("N", __dcN(d));
        proxyCircle.setAttribute("DiseaseOrTrait", __dcDiseaseClean(d));
        proxyCircle.setAttribute("trait", __dcTrait(d));
        proxyCircle.setAttribute("PatternSelection", "NO");
        proxyCircle.setAttribute("ancestrySelection", "NO");
        proxyCircle.setAttribute("cx", p.x - state.pad);
        proxyCircle.setAttribute("cy", p.y - state.pad);
        proxyCircle.setAttribute("r", p.r);

        return proxyCircle;
    }

    function drawOnePoint(p, flags) {
        var colour = getColour(p.className, flags || {});
        var alpha = colour.opacity;

        if (flags && flags.disabled) alpha = Math.min(alpha, 0.16);
        if (flags && flags.selected) alpha = 1;

        ctx.save();
        ctx.globalAlpha = alpha;
        ctx.beginPath();
        ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2, false);
        ctx.fillStyle = colour.fill;
        ctx.fill();

        if (flags && flags.selected) {
            ctx.lineWidth = 2.5;
            ctx.strokeStyle = "#111";
            ctx.stroke();
        }

        ctx.restore();
    }

    function rebuildPointsAndDraw() {
        var parentFilter = currentParentFilter();
        var ancestryFilters = currentAncestryFilters();
        var selectedTraits = getCurrentTraitSelection();

        state.parentFilter = parentFilter;
        state.ancestryFilters = ancestryFilters.slice();
        state.selectedTraits = selectedTraits.slice();

        ctx.clearRect(0, 0, canvasW, canvasH);
        state.points = [];
        state.grid = new Map();

        for (var i = 0; i < data.length; i++) {
            var d = data[i];

            if (!rowMatchesParent(d, parentFilter)) continue;
            if (!rowMatchesAncestry(d, ancestryFilters)) continue;
            if (!rowMatchesTrait(d, selectedTraits)) continue;

            var n = __dcN(d);
            var x = xScale(new Date(__dcDateValue(d))) + state.pad;
            var y = yScale(n) + state.pad;
            var r = sizeScale(n);

            if (!isFinite(x) || !isFinite(y) || !isFinite(r)) continue;

            var p = {
                index: i,
                d: d,
                x: x,
                y: y,
                r: r,
                className: __dcClass(d),
                traitOk: true
            };

            state.points.push(p);
            addToGrid(p);
            drawOnePoint(p, { opaque: selectedTraits.length > 0 });
        }

        if (state.selectedIndex !== null) {
            for (var a = 0; a < state.points.length; a++) {
                if (state.points[a].index === state.selectedIndex) {
                    drawOnePoint(state.points[a], { selected: true });
                    break;
                }
            }
        }

        state.visibleCount = state.points.length;
        state.drawCount += 1;
    }

    state.rebuild = rebuildPointsAndDraw;

    function hitTest(x, y) {
        var cs = state.cellSize;
        var cx = Math.floor(x / cs);
        var cy = Math.floor(y / cs);

        var best = null;
        var bestDist = Infinity;

        for (var dx = -1; dx <= 1; dx++) {
            for (var dy = -1; dy <= 1; dy++) {
                var arr = state.grid.get(cellKey(cx + dx, cy + dy));
                if (!arr) continue;

                for (var i = arr.length - 1; i >= 0; i--) {
                    var p = arr[i];

                    var ddx = x - p.x;
                    var ddy = y - p.y;
                    var dist = Math.sqrt(ddx * ddx + ddy * ddy);

                    if (dist <= p.r + 3 && dist < bestDist) {
                        best = p;
                        bestDist = dist;
                    }
                }
            }
        }

        return best;
    }

    function canvasPointFromEvent(evt) {
        var rect = canvas.getBoundingClientRect();
        return {
            x: (evt.clientX - rect.left) * canvasW / rect.width,
            y: (evt.clientY - rect.top) * canvasH / rect.height
        };
    }

    function selectPoint(p) {
        if (!p) return false;

        clearSelected();
        state.selectedIndex = p.index;

        var bg = document.querySelector(selector + " svg #bubbleData .background");
        if (bg && bg.classList) bg.classList.add("clicked");

        var proxy = setProxyFromPoint(p);
        makeSelected(proxy);
        rebuildPointsAndDraw();

        return true;
    }

    canvas.addEventListener("click", function(evt) {
        var pt = canvasPointFromEvent(evt);
        var p = hitTest(pt.x, pt.y);

        if (p) {
            selectPoint(p);
        } else {
            clearSelected();
            state.selectedIndex = null;
            rebuildPointsAndDraw();
        }
    });

    var hoverScheduled = false;
    var lastHoverEvent = null;

    canvas.addEventListener("mousemove", function(evt) {
        lastHoverEvent = evt;

        if (hoverScheduled) return;
        hoverScheduled = true;

        requestAnimationFrame(function() {
            hoverScheduled = false;

            var pt = canvasPointFromEvent(lastHoverEvent);
            var p = hitTest(pt.x, pt.y);

            canvas.style.cursor = p ? "pointer" : "default";
        });
    });

    function updateYAxisAndRedraw() {
        var parentFilter = currentParentFilter();
        var ancestryFilters = currentAncestryFilters();
        var selectedTraits = getCurrentTraitSelection();
        var maxFiltered = computeFilteredMax(parentFilter, ancestryFilters, selectedTraits);

        yScale.domain([0, maxFiltered]);
        sizeScale.domain([0, maxFiltered]);

        svg.select(".axis-y")
            .call(d3.axisLeft(yScale).ticks(tickMax, "s"));

        svg.select(".grid")
            .call(d3.axisLeft(yScale)
                .ticks(tickMax)
                .tickSize(-width)
                .tickFormat('')
            );

        rebuildPointsAndDraw();
    }

    $(selector).find(".ancestry-filter .option")
        .off("click.canvasBubble")
        .on("click.canvasBubble", function() {
            $(this).toggleClass("active");

            var filter = $(this).attr("dataFilter");
            var parentSVG = $(svg_selector);

            if (filter === "all") {
                $.each((parentSVG.attr("class") || "").split(" "), function(index, value) {
                    if (value.startsWith("ancestry-")) {
                        parentSVG.removeClass(value);
                    }
                });
                $(".ancestry-filter .option").removeClass('active');
            } else {
                parentSVG.removeClass("ancestry-all");
            }

            parentSVG.toggleClass("ancestry-" + filter);
            clearSelected();
            state.selectedIndex = null;
            updateYAxisAndRedraw();
        });

    $(selector).find(".filter select[name='parentTerms']")
        .off("change.canvasBubble")
        .on("change.canvasBubble", function() {
            var selected_ = $(this).find('option:selected');
            var parentSVG = $(svg_selector);

            $.each((parentSVG.attr("class") || "").split(" "), function(index, value) {
                if (value.indexOf("term-") !== -1) {
                    parentSVG.removeClass(value);
                }
            });

            parentSVG.addClass("term-" + selected_.attr('value'));
            clearSelected();
            state.selectedIndex = null;
            updateYAxisAndRedraw();
        });

    $(selector).find(".filter select[name='trait']")
        .off("change.canvasBubble")
        .on("change.canvasBubble", function() {
            clearSelected();
            state.selectedIndex = null;
            updateYAxisAndRedraw();
        });

    $('#cb2')
        .off("change.canvasBubble")
        .on("change.canvasBubble", function() {
            $('.ancestry-filter .btn').removeClass('active');
            clearSelected();
            drawBubbleGraph(selector, rawPayload, $(this).is(":checked"));
        });

    function updateCanvasExportImage() {
        try {
            var dataUrl = canvas.toDataURL("image/png");
            var img = mainSvg.select("#canvasExportImage");

            if (img.empty()) {
                img = mainSvg.insert("image", "g.svg-container")
                    .attr("id", "canvasExportImage");
            }

            img
                .attr("x", margin.left - state.pad)
                .attr("y", margin.top - state.pad)
                .attr("width", canvasW)
                .attr("height", canvasH)
                .attr("href", dataUrl)
                .attr("xlink:href", dataUrl);
        } catch (e) {
            console.warn("Canvas export image update failed", e);
        }
    }

    bindImageDownload('#bubble-graph-controls', selector, svg_id, function () {
        updateCanvasExportImage();
    });

    $('#bubble-graph-controls .icon-download-data').closest('a').off('click.bubbleCsvDownload').on('click.bubbleCsvDownload', function (event) {
        event.preventDefault();
        __dcDownloadBubbleCsv();
    });

    var svgs = bubbleGraph.find('svg');
    if (svgs.length > 1) {
        bubbleGraph.find(".icon-zone .icon-download-image").unbind();
    }

    $("select[name='trait']").select2({
        multiple: true,
        minimumInputLength: 3,
        placeholder: "Search for one or more traits",
        ajax: {
            url: '/api/traits',
            data: function (params) {
                return { search: params.term };
            }
        }
    });

    if (!window.__bubbleCanvasClearSelectedWrapped) {
        window.__bubbleCanvasClearSelectedWrapped = true;
        var oldClearSelected = clearSelected;

        clearSelected = function() {
            oldClearSelected();

            if (window.__bubbleCanvasState) {
                window.__bubbleCanvasState.selectedIndex = null;

                var bg = document.querySelector("#bubbleGraph svg #bubbleData .background");
                if (bg && bg.classList) bg.classList.remove("clicked");
            }
        };
    }

    rebuildPointsAndDraw();

    window.__bubbleCanvasGetFirstPoint = function() {
        if (!window.__bubbleCanvasState || !window.__bubbleCanvasState.points.length) return null;
        var p = window.__bubbleCanvasState.points[0];

        return {
            x: p.x,
            y: p.y,
            r: p.r,
            index: p.index,
            pubmedid: String(p.d.PUBMEDID || "")
        };
    };
}

function zoomBubbleChart(xScale, yScale, xAxis, yAxis) {
    // // recover the new scale
    // var newX = d3.event.transform.rescaleX(xScale);
    // var newY = d3.event.transform.rescaleY(yScale);
    //
    // // // // update axes with these new boundaries
    // xAxis.call(d3.axisBottom(newX));
    // yAxis.call(d3.axisLeft(newY));
}

function circleClick(evt) {
    clearSelected();
    $("#bubbleGraph svg #bubbleData .background").addClass("clicked");
    makeSelected(evt.target);
}

function circleMouseOver(evt) {
    if($("#bubbleGraph svg #bubbleData .background").hasClass("clicked")) {
        setTimeout(function() {
            makeSelected(evt.target);
        }, 1);
    }
}

function backgroundMouseOver(evt) {
    if (evt && evt.target && evt.target.classList) {
        evt.target.classList.remove("clicked");
    }
}

function makeSelected(node) {
    if (node && node.classList) {
        node.classList.add("selected");
        $("#bubbleGraph .details-zone").addClass('active');
        var id = node.getAttribute("pubmedid").replace('.0', '');
        var size = node.getAttribute("N").replace('.0', '');
        if($("#bubbleGraph .details .row#" + id).length > 0) {
            if($("#bubbleGraph .details .row#" + id + " .last#" + size).length > 0) {
                $("#bubbleGraph .details .row#" + id + " .last#" + size).append(
                    "<div class='last-inside'><span>" + node.getAttribute("accession") + "</span>"+
                    "<span>" + node.getAttribute("DiseaseOrTrait") + "</span></div>"
                )
            } else {
                $("#bubbleGraph .details .row#" + id).append(
                    "<div class='last' id='" + size + "'><span>Size: " + numberFormatter(size) + " Part.</span>"+
                    "<div class='last-inside'><span>" + node.getAttribute("accession") + "</span>"+
                    "<span>" + node.getAttribute("DiseaseOrTrait") + "</span></div></div>"
                )
            }
        } else {
            $("#bubbleGraph .details").append(
                "<div class='row' id='" + id + "'>"+
                "<div class='first'><a href='https://www.ncbi.nlm.nih.gov/pubmed/" + id + "' target='_blank'>PUBMEDID: " + id + "</a>"+
                "<span>First Author <strong>" + node.getAttribute("author")+"</strong></span></div>"+
                "<div class='last' id='" + size + "'><span>Size: " + numberFormatter(size) + " Part.</span>"+
                "<div class='last-inside'><span>" + node.getAttribute("accession") + "</span>"+
                "<span>" + node.getAttribute("DiseaseOrTrait") + "</span></div></div>"+
                "</div>"
            );
        }
    }
}

function clearSelected() {
    $("#bubbleGraph #bubbleData circle.selected").removeClass("selected");
    $("#bubbleGraph .details").empty();
    $("#bubbleGraph .details-zone").removeClass('active');
}

function reDrawBubbleGraph(data, filters, selector, xScale, yScale, sizeScale, tickMax){
    clearSelected()

    var svg = d3.select(selector);
    var max = getDataMax(data, filters);
    var minYear = getYear(data)['minYear'];
    var maxYear = getYear(data)['maxYear'];
    var selected=$(selector).find(".filter select[name='trait']").val()
    var disabled_switch=''


    xScale.domain([minYear, maxYear]);

    yScale.domain([0, max]);

    sizeScale.domain([0, max]);

    svg.select(".axis-y")
        .transition().duration(500).ease(d3.easeLinear)
        .call(d3.axisLeft(yScale).ticks(tickMax, "s"));

    svg.select('#bubbleData')
        .selectAll('circle')
        .data(data)
        .transition().duration(500).ease(d3.easeLinear)

        .attr("class", function (d) {
        if ((selected.length!=0) && !selected.includes(d.DiseaseOrTrait.replace(/ /g, '-').replace('>', 'more than').replace('<', 'less than').replace(/\(/g, '').replace(/\)/g, '').toLowerCase())){
            disabled_switch=' disabled'
        }else if (selected.length==0){
            disabled_switch=''
        }else{
            disabled_switch=' opaque'
        }
        return d.Broader.replace(' ', '-').replace('/', '-').replace(' ', '-').replace(' ', '-').toLowerCase() + " " +
            d.parentterm.replace(/, /g, ',').replace(/ /g, '-').replace(/,/g, ' ').toLowerCase()+ disabled_switch;
    })
        .attr("onclick", "circleClick(evt);")
        .attr("onmouseover", "circleMouseOver(evt);")
        .attr("cx", function (d) { return xScale(new Date(d.DATE)); })
        .attr("cy", function (d) { return yScale(d.N); })
        .attr("r", function(d){ return sizeScale(d.N) })
        .attr("pubmedid", function (d) { return d.PUBMEDID })
        .attr("author", function (d) { return d.AUTHOR })
        .attr("accession", function (d) { return d.ACCESSION })
        .attr("N", function (d) { return d.N })
        .attr("DiseaseOrTrait", function (d) { return d.DiseaseOrTrait.replace('>', 'more than').replace('<', 'less than') })
        .attr("trait", function (d) { return d.DiseaseOrTrait.replace(/ /g, '-').replace('>', 'more than').replace('<', 'less than').replace(/\(/g, '').replace(/\)/g, '').toLowerCase() })
        .attr("PatternSelection","NO")
        .attr("ancestrySelection","NO");
}

function getYear(data) {
    var minYear = new Date('3000-01-01');
    var maxYear = new Date('1000-01-01');

    for(i = 0; i < data.length; i++) {

        var date = new Date($(data[i])[0]['DATE']);

        if(date < minYear) {
            minYear = date;
        }
        if(date > maxYear) {
            maxYear = date;
        }

    }

    return {
        minYear: minYear,
        maxYear: maxYear
    };
}

function getDataMax(data, filters) {
    var max = 0;

    if (filters === undefined) {
        data.forEach(function(element) {
            if (parseInt(element.N) > max) {
                max = parseInt(element.N);
            }
        });
    } else {
        data.forEach(function(element) {
            var elementAncestry = 'ancestry' + element.Broader.replace(' ', '-').replace('/', '-').replace(' ', '-').replace(' ', '-').toLowerCase();
            var joinedFilter = filters.split('-').join('');
            var joinedAncestry = new RegExp('\\b' + elementAncestry.split('-').join('') + '\\b');
            var ancestryCondition = (joinedFilter.search(joinedAncestry) === -1);

            var elementTerms = element.parentterm.replace(/, /g, ',').replace(/ /g, '-').toLowerCase().split(",");
            var termCondition = false;

            elementTerms.forEach(function(element) {
                termCondition = (filters.indexOf(element) !== -1 || filters.indexOf("term-all") !== -1);
            });

            let termConditionMatchArray = elementTerms.filter(function(el) {
                if ((filters.indexOf(el) !== -1 || filters.indexOf("term-all") !== -1)) {
                    return el;
                }
            });

            if((ancestryCondition && termCondition) || (ancestryCondition && termConditionMatchArray.length > 0)) {
                if (parseInt(element.N) > max) {
                    max = parseInt(element.N);
                }
            }
        });
    }

    var rounding = 10000;
    if (max > 1000000) {
        rounding = 200000
    } else if (max > 100000) {
        rounding = 100000
    }

    return Math.ceil(max / rounding)*rounding;
}

function sanitiseSVG(selector, preserveFilters) {
    if (!preserveFilters) {
        $(selector).find(".filter select[name='trait']").val(null).trigger('change');
    }

    $(selector + " svg").remove();
    clearSelected();
    $(selector).find(".filter .option").unbind();
    if (!preserveFilters) {
        $(selector).find(".filter .option").removeClass("active");
    }
    $(selector).find(".filter select").unbind();
    if (!preserveFilters) {
        $(selector).find(".filter select[name='parentTerms']").val("all");
    }
}
