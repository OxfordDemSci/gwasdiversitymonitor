function drawHeatMap(data, withMetric, withStage, ancestriesOrdered, preservedState) {

    window.withMetric = withMetric;

    let margin = {top: 0, right: 0, bottom: 0, left: 0},
        width = 295 - margin.left - margin.right,
        height = 387 - margin.top - margin.bottom;

	let selector = "#heatMap"
	let svg_id = 'heatmapSVG'
    let svg_selector = `#${svg_id}`

    d3.select("#heatMap svg").remove();

    let mainSvg = d3.select("#heatMap")
        .append("svg")
        .attr("id", svg_id)
        .attr("width", width)
        .attr("height", height);

    makeChartResponsive(mainSvg, width, height);

    mainSvg.append('rect').attr('class', 'white-rect').attr('fill', '#ffff').attr('style', 'fill: white;').attr('height', '600').attr('width', '550');

    let svg = mainSvg.append('g')
        .attr('class', 'heatmap svg-container');

    let nextButtons = document.querySelectorAll('.heat-map-change-year button.next');
    let previousButtons = document.querySelectorAll('.heat-map-change-year button.previous');

    // Define the div for the tooltip
    d3.selectAll('.d3-tooltip.heat-map-tooltip').remove();
    let d3Tooltip = d3.select("body").append("div")
        .attr("class", "d3-tooltip heat-map-tooltip")
        .style("opacity", 0);

    let specificData;
    let dataKeys;
    let currentYear;
    let dataKey;

    if (!withMetric && withStage) {
        dataKey = 'heatmap_replication_participants';
    } else if (!withMetric && !withStage) {
        dataKey = 'heatmap_discovery_participants';
    } else if (withMetric && withStage) {
        dataKey = 'heatmap_replication_studies';
    } else if (withMetric && !withStage) {
        dataKey = 'heatmap_discovery_studies';
    }

    setUpSettings(dataKey);

    // Functions changing dates
    window.hmpreviousYear = function() {
        moveYear(-1);
    };

    window.hmnextYear = function() {
        moveYear(1);
    };

    window.hmfirstYear = function() {
        if (!dataKeys.length) return;
        currentYear = dataKeys[0];
        updateGraph();
    };

    window.hmlastestYear = function() {
        if (!dataKeys.length) return;
        currentYear = dataKeys[dataKeys.length-1];
        updateGraph();
    };

    let dateSpan = document.querySelector('.heat-map-change-year span');
    updateGraph();

	bindImageDownload('#heat-map-controls', selector, svg_id);

    function drawLogScaleColour(range, logColour_scale, maxValue) {
        let legend = svg.append('g').attr('class', 'log-colour-scale');

        let gradient = legend.append("defs")
            .append("svg:linearGradient")
            .attr("id", "heatmapgradient")
            .attr("x1", "0%")
            .attr("y1", "100%")
            .attr("x2", "100%")
            .attr("y2", "100%")
            .attr("spreadMethod", "pad");

        gradient.append("stop")
            .attr("offset", "0%")
            .attr("stop-color", logColour_scale(range[0]))
            .attr("stop-opacity", 1);

        gradient.append("stop")
            .attr("offset", "50%")
            .attr("stop-color", logColour_scale((range[1] + range[0]) / 2))
            .attr("stop-opacity", 1);

        gradient.append("stop")
            .attr("offset", "100%")
            .attr("stop-color", logColour_scale(range[1]))
            .attr("stop-opacity", 1);

        if (window.innerWidth < 1500 && window.innerWidth >= 1200) {
            drawLegendResponsive(legend, maxValue, 125);
        } else {
            drawLegendResponsive(legend, maxValue, 100);
        }

        let legendDupArray = document.querySelectorAll('.log-colour-scale');
        if (isIE()) {
            if (legendDupArray.length > 1) {
                let child = legendDupArray[0];
                legendDupArray[0].parentNode.removeChild(child);
            }
        } else {
            legendDupArray.length > 1 ? legendDupArray[0].remove() : '';
        }
    }

    function drawLegendResponsive(legend, maxValue, xRectValue) {
        let legendStartX = width - xRectValue;
        let legendEndX = width - 6;

        legend.append("rect")
            .attr("x", legendStartX)
            .attr("y", height - 64)
            .attr("width", legendEndX - legendStartX)
            .attr("height", 19)
            .style("fill", "url(#heatmapgradient)");

        legend.append('text')
            .attr('class', 'heat-map-legend-end-text')
            .text(numberFormatter(maxValue))
            .attr('x', legendEndX)
            .attr('text-anchor', 'end')
            .attr('y', height-35);

        legend.append('text')
            .attr('class', 'heat-map-legend-start-text')
            .attr('x', legendStartX)
            .attr('y', height-35)
            .attr('font-size', 11)
            .text('0');
    }

    function setUpSettings(key) {
        specificData = data && data[key] ? data[key] : {};
        dataKeys = Object.keys(specificData).filter(function(year) {
            return specificData[year] &&
                Object.keys(specificData[year]).length > 0;
        }).sort(function(a, b) {
            return Number(a) - Number(b);
        });
        var requestedYear = preservedState && String(preservedState.year);
        currentYear = requestedYear && dataKeys.indexOf(requestedYear) !== -1 ?
            requestedYear :
            dataKeys[dataKeys.length-1];
    }

    function setYearButtonsDisabled(disabled) {
        Array.prototype.forEach.call(nextButtons, function(button) {
            button.disabled = disabled;
        });
        Array.prototype.forEach.call(previousButtons, function(button) {
            button.disabled = disabled;
        });
    }

    function drawEmptyState() {
        mainSvg.selectAll('.heat-map-empty-state').remove();
        mainSvg.append('text')
            .attr('class', 'heat-map-empty-state')
            .attr('x', width / 2)
            .attr('y', height / 2)
            .attr('text-anchor', 'middle')
            .attr('fill', '#516777')
            .attr('font-size', 13)
            .attr('font-weight', 700)
            .append('tspan')
            .attr('x', width / 2)
            .attr('dy', '-0.3em')
            .text('No heat-map data');
        mainSvg.select('.heat-map-empty-state')
            .append('tspan')
            .attr('x', width / 2)
            .attr('dy', '1.3em')
            .text('for this selection');
    }

    function updateGraph() {
        mainSvg.selectAll('.heat-map-empty-state').remove();

        if (!dataKeys.length) {
            dateSpan.textContent = 'No data';
            setYearButtonsDisabled(true);
            drawEmptyState();
            return;
        }

        dateSpan.textContent = currentYear;
        let currentIndex = dataKeys.indexOf(String(currentYear));
        Array.prototype.forEach.call(nextButtons, function(button) {
            button.disabled = currentIndex >= dataKeys.length - 1;
        });
        Array.prototype.forEach.call(previousButtons, function(button) {
            button.disabled = currentIndex <= 0;
        });

        getGraphPerYear(specificData, currentYear);
    }

    function moveYear(offset) {
        if (!dataKeys.length) return;
        let currentIndex = dataKeys.indexOf(String(currentYear));
        let nextIndex = Math.max(
            0,
            Math.min(dataKeys.length - 1, currentIndex + offset)
        );
        currentYear = dataKeys[nextIndex];
        updateGraph();
    }

    function drawGraph(val, category, containerClass, rectClass, x, logColour_scale, logScale) {
        let array = val.filter(function(el) { return el.ancestry === category });
        let container = svg.append('g').attr('class', containerClass);
        let avoidDupArray = document.querySelectorAll('.'+containerClass);
        if (isIE()) {
            // let child = avoidDupArray[0];
            // child.parentNode.removeChild(child);
        } else {
            avoidDupArray.length > 1 ? avoidDupArray[0].remove() : '';
        }

        container.selectAll('.'+rectClass)
            .data(array)
            .enter()
            .append("rect")
            .attr('class', rectClass)
            .attr('width', 27)
            .attr('height', 17)
            .attr("x", function () { return x; })
            .attr("y", function (d, i) { return height-110-(i*17); })
            .attr("fill", function(d){
                if (d.value === '0' || d.value === '0.0') {
                    return "#5c6bc0";
                } else {
                    return logColour_scale(logScale(d.value));
                }
            })
            .attr('stroke', '#ffffff')
            .attr('stroke-width', 1)
            .on('mouseover', function (d) {
                d3Tooltip.transition()
                    .duration(200)
                    .style("opacity", 1);
                d3Tooltip.html("<div><p>"+d.term+",</p><p>"+d.ancestry+"</p><span>"+numberFormatter(d.value)+"</span></div>")
                    .style("left", (d3.event.pageX) + "px")
                    .style("top", (d3.event.pageY - 58) + "px");
            })
            .on('mouseout', function () {
                d3Tooltip.transition()
                    .duration(500)
                    .style("opacity", 0);
            });
    }

    function getGraphPerYear(data, year) {
        let yearData = data && data[year] ? data[year] : {};
        if (!Object.keys(yearData).length) {
            drawEmptyState();
            return;
        }

        let valArray = Object.keys(yearData).map(function (_) {
            return yearData[_];
        });
        let maxValue = Math.max.apply(Math, valArray.map(function(o) {
            return Number(o.value) || 0;
        }));
        maxValue = Math.max(1, maxValue);

        let val = yearData;

        val = Object.keys(val).map(function (_) {
            return val[_];
        });

        let domain = [1, maxValue];
        let range = [0, 100];

        let logScale =  d3.scaleLog().domain(domain).range(range);
        let logColour_scale = d3.scaleLinear().domain([0, 50, 100]).range(['#5c6bc0', '#EDEE39', '#ff3683']);

        drawLogScaleColour(range, logColour_scale, maxValue);

        let xLegendArray = [];
        for (let i in ancestriesOrdered) {
            xLegendArray.push(ancestriesOrdered[i]);
        }

        drawGraph(val, xLegendArray[0], 'european-container', 'european-rect', 0, logColour_scale, logScale);
        drawGraph(val, xLegendArray[1], 'asian-container', 'asian-rect', 27, logColour_scale, logScale);
        drawGraph(val, xLegendArray[2], 'african-container', 'african-rect', 54, logColour_scale, logScale);
        drawGraph(val, xLegendArray[3], 'afr-ame-carr-container', 'afr-ame-carr-rect', 81, logColour_scale, logScale);
        drawGraph(val, xLegendArray[4], 'his-lat-ame-container', 'his-lat-ame-rect', 108, logColour_scale, logScale);
        drawGraph(val, xLegendArray[5], 'other-mixed-container', 'other-mixed-rect', 135, logColour_scale, logScale);

        // X and Y axis legends
        let axisLegendsContainer = svg.append('g')
            .attr('class', 'axis-legend-container');
        let axisLegendsAvoidDupArray = document.querySelectorAll('.axis-legend-container');
        if(isIE()) {
            if (axisLegendsAvoidDupArray.length > 1) {
                let child = axisLegendsAvoidDupArray[0];
                child.parentNode.removeChild(child);
            }
        } else {
            axisLegendsAvoidDupArray.length > 1 ? axisLegendsAvoidDupArray[0].remove() : '';
        }

        axisLegendsContainer.selectAll('.heat-map-x-axis-legend-item')
            .data(xLegendArray)
            .enter()
            .append('text')
            .text(function(d) { return d })
            .attr('class', function(d, i) { return 'heat-map-x-axis-legend-item'+' '+'heat-map-x-axis-legend-item-'+i})
            .attr('x', 13)
            .attr('y', height-1)
            .attr('transform', function(d, i) {
                return 'translate('+(i*28+15)+', '+(-80+this.getBBox().width+5)+') rotate(-90 0 '+height+')'
            });

        axisLegendsContainer.select('.heat-map-x-axis-legend-item-4')
            .text(xLegendArray[4].match(/.{1,17}(\s|$)/g)[0])
            .attr('x', 56).attr('dy', -4)
            .append('tspan').text(xLegendArray[4].match(/.{1,17}(\s|$)/g)[1]).attr('x', 89).attr('dy', 10);

        axisLegendsContainer.select('.heat-map-x-axis-legend-item-3')
            .text(xLegendArray[3].match(/.{1,17}(\s|$)/g)[0])
            .attr('x', 94).attr('dy', -4)
            .append('tspan').text(xLegendArray[3].match(/.{1,17}(\s|$)/g)[1]).attr('x', 91).attr('dy', 10);

        let yLegendArray = val.map(function(el) { return el.term });
        yLegendArray = uniq(yLegendArray);

        if(isIE()){
            axisLegendsContainer.selectAll('.heat-map-y-axis-legend-item')
                .data(yLegendArray)
                .enter()
                .append('text')
                .text(function(d) { return d })
                .attr('class', 'heat-map-y-axis-legend-item')
                .attr('x', 168)
                .attr('y', function (d, i) {
                    return height-96-(i*17);
                });
        } else {
            axisLegendsContainer.selectAll('.heat-map-y-axis-legend-item')
                .data(yLegendArray)
                .enter()
                .append('foreignObject')
                .attr("width", 160)
                .attr("height", 17)
                .attr('x', 168)
                .attr('y', function (d, i) {
                    return height-110-(i*17);
                })
                .html(function(d) { return '<p xmlns="http://www.w3.org/1999/xhtml" class="heat-map-y-axis-legend-item">'+d+'</p>' });

            let yItems = document.querySelectorAll('.heat-map-y-axis-legend-item');

            if (window.innerWidth >= 1200 && window.innerWidth <= 1550) {
                for (let i=0; i < yItems.length; i++) {
                    yItems[i].classList.add('ellipsis');
                    yItems[i].style.maxWidth = document.querySelector('#heatMap svg').clientWidth-162-10+'px';
                }
            } else {
                for (let i=0; i < yItems.length; i++) {
                    yItems[i].classList.remove('ellipsis');
                    yItems[i].style.maxWidth = 'inherit';
                }
            }
        }

    }

    function uniq(a) {
        let prims = {"boolean":{}, "number":{}, "string":{}}, objs = [];

        return a.filter(function(item) {
            let type = typeof item;
            if(type in prims) {
                return prims[type].hasOwnProperty(item) ? false : (prims[type][item] = true);
            } else {
                return objs.indexOf(item) >= 0 ? false : objs.push(item);
            }
        });
    }
}

function isIE() {
    let ua = window.navigator.userAgent; //Check the userAgent property of the window.navigator object
    let msie = ua.indexOf('MSIE '); // IE 10 or older
    let trident = ua.indexOf('Trident/'); //IE 11

    return (msie > 0 || trident > 0);
}
