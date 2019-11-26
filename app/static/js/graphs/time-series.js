// Define the div for the tooltip
d3.select('.d3-tooltip.time-series').remove();
let d3TooltipLineGraph = d3.select("body").append("div")
    .attr("class", "d3-tooltip time-series")
    .style("opacity", 0);

function drawTimeSeries(json, id, studies, replication) {
    var timeSeries = $(id);
    var margin = {top: 30, bottom: 30, left: 40, right: 30};
    var width = timeSeries.width() - margin.left - margin.right;
    var height = 340 - margin.top - margin.bottom;
    var mainSvg = d3.select(id).append('svg');
    var tickMax = 6;

    if(studies) {
        if(replication) {
            var data = JSON.parse(JSON.stringify(json.ts_recorded_replication_studies));
        } else {
            var data = JSON.parse(JSON.stringify(json.ts_recorded_discovery_studies));
        }
    } else {
        if(replication) {
            var data = JSON.parse(JSON.stringify(json.ts_recorded_replication_participants));
        } else {
            var data = JSON.parse(JSON.stringify(json.ts_recorded_discovery_participants));
        }
    }

    mainSvg.append('rect').attr('class', 'white-rect').attr('fill', '#ffff').attr('style', 'fill: white;').attr('height', '550').attr('width', '700');

    // Graph
    var svg = mainSvg.append('g')
        .attr('class', 'svg-container')
        .attr('transform', 'translate(' + margin.left + ',' + margin.top + ')');

    // Add X axis
    var xScale = getScaleX(data, width);
    var xAxis = d3.axisBottom(xScale)
                  .ticks(4)
                  .tickFormat(d3.timeFormat("%Y"));
    svg.append('g').attr('class', 'axis-x').attr('transform', 'translate(0,' + height + ')').call(xAxis);

    // Add Y axis
    var yAxis = getAxisY(data, height, tickMax);
    svg.append('g')
       .attr('class', 'axis-y')
       .call(
           yAxis['yAxis']
               .ticks(tickMax)
       );

    // Add the Y gridlines
    addGrid(svg, width, tickMax, yAxis);

    // Add graph
    addGraph(data, svg, width, yAxis, xScale);

    // Filters
    var ancestriesFilter = $('#ancestries-filter');
    var recordFilter = $('#record-filter');
    var insideFilters = $('.gwas-filter');
    var outsideFilters = $('.gwas-switch');

    insideFilters.change(function() {
        var data = filterRecord(json, recordFilter, studies, replication);
        filterAncestries(data, ancestriesFilter);
        redrawTimeSeries(data, svg, width, height, tickMax, xScale);
    });

    outsideFilters.change(function() {
        var data = filterRecord(json, recordFilter, studies, replication);
        recordFilter.prop('checked', false);
        ancestriesFilter.val(ancestriesFilter.find('option:first').val());
        redrawTimeSeries(data, svg, width, height, tickMax, xScale);
    });

    var svgs = timeSeries.find('svg');
    if (svgs.length > 1) {
        svgs[0].parentNode.removeChild(svgs[0].parentNode.childNodes[5]);
    }
}

function addGrid(svg, width, tickMax, yAxis) {
    svg.append('g')
        .attr('class', 'grid')
        .call(d3.axisLeft(yAxis['yScale'])
            .ticks(tickMax)
            .tickSize(-width)
            .tickFormat('')
        )
}

function addGraph(data, svg, width, yAxis, xScale) {
    jQuery.each(data, function(key, val) {
        val = Object.keys(val).map(function(_) { return val[_]; });
        var slug = key.replace(' ', '-').replace('/', '-').replace(' ', '-').replace(' ', '-').toLowerCase();
        var yScale = yAxis['yScale'];

        // Add line
        var line = d3.line()
            .x(function(d) { return xScale(new Date(d.year)); })
            .y(function(d) { return yScale(d.value); });
        svg.append('g')
            .attr('class', 'line')
            .append('path')
            .datum(val)
            .attr('class', 'el')
            .attr('d', line);

        // Add dots
        svg.append('g')
            .selectAll('dot')
            .data(val)
            .enter()
            .append('circle')
            .attr('class', 'dot el ' + slug)
            .attr('cx', function (d) { return xScale(new Date(d.year)); } )
            .attr('cy', function (d) { return yScale(d.value); } )
            .attr('r', 5)
            .on('mouseover', function (d) {
                d3TooltipLineGraph.transition()
                    .duration(200)
                    .style("opacity", 1);
                d3TooltipLineGraph.html("<div><p>"+key+"</p><span>"+parseFloat(parseFloat(d.value).toFixed(2))+"%</span></div>")
                    .style("left", (d3.event.pageX) + "px")
                    .style("top", (d3.event.pageY - 58) + "px");
            })
            .on('mouseout', function () {
                d3TooltipLineGraph.transition()
                    .duration(500)
                    .style("opacity", 0);
            });
    });
}

function filterRecord(json, recordFilter, studies, replication) {
    if(recordFilter[0]['checked'] == true) {
        if(studies) {
            if(replication) {
                var data = JSON.parse(JSON.stringify(json.ts_notrecorded_replication_studies));
            } else {
                var data = JSON.parse(JSON.stringify(json.ts_notrecorded_discovery_studies));
            }
        } else {
            if(replication) {
                var data  = JSON.parse(JSON.stringify(json.ts_notrecorded_replication_participants));
            } else {
                var data  = JSON.parse(JSON.stringify(json.ts_notrecorded_discovery_participants));
            }
        }
    } else {
        if(studies) {
            if(replication) {
                var data = JSON.parse(JSON.stringify(json.ts_recorded_replication_studies));
            } else {
                var data = JSON.parse(JSON.stringify(json.ts_recorded_discovery_studies));
            }
        } else {
            if(replication) {
                var data = JSON.parse(JSON.stringify(json.ts_recorded_replication_participants));
            } else {
                var data = JSON.parse(JSON.stringify(json.ts_recorded_discovery_participants));
            }
        }
    }
    return data;
}

function filterAncestries(data, ancestriesFilter) {
    var selected = ancestriesFilter.find('option:selected');
    var thisFilter = selected.attr('name');

    jQuery.each(data, function(key) {
        if(thisFilter !== 'all') {
            if(thisFilter !== key) {
                delete data[key];
            }
        }
    });
}

function redrawTimeSeries(data, svg, width, height, tickMax, xScale) {
    var el = $('#timeSeries .el');
    var grid = $('#timeSeries .grid');
    var newMax = getMinMax(data)['valueMax'];
    var yAxis = getAxisY(data, height, tickMax);
    el.remove();
    grid.remove();
    rescaleAxis(svg, newMax, tickMax, yAxis);
    addGrid(svg, width, tickMax, yAxis);
    addGraph(data, svg, width, yAxis, xScale);
}

function rescaleAxis(svg, newMax, tickMax, yAxis) {
    yAxis['yScale'].domain([0, newMax]);
    svg.select('.axis-y')
       .transition()
       .call(
           yAxis['yAxis']
               .ticks(tickMax)
       );
}

function getMinMax(data) {
    var array = Object.keys(data).map(function(_) { return data[_]; });
    var yearMin = 3000;
    var yearMax = 0;
    var valueMax = 0;

    jQuery.each(array, function(key, val) {
        jQuery.each(val, function(k, v) {
            if(parseInt(v['year']) < yearMin) {
                yearMin = parseInt(v['year']);
            }
            if(parseInt(v['year']) > yearMax) {
                yearMax = parseInt(v['year']);
            }
            if(v['value'] > valueMax) {
                valueMax = Math.ceil(v['value']);
            }
        });
    });

    if(valueMax > 80) {
        valueMax = 100;
    } else if(valueMax > 10) {
        valueMax = Math.ceil(valueMax / 10) * 10;
    } else if(valueMax > 5) {
        valueMax = Math.ceil(valueMax / 5) * 5;
    } else {
        valueMax = Math.ceil(valueMax / 2) * 2;
    }

    return {
        yearMin: yearMin,
        yearMax: yearMax,
        valueMax: valueMax
    };
}

function getScaleX(data, width) {
    var min = new Date(getMinMax(data)['yearMin'], 1, 1);
    var max = new Date(getMinMax(data)['yearMax'], 1, 1);
    var xScale = d3.scaleTime()
        .domain([min, max])
        .range([0, width]);

    return xScale;
}

function getAxisY(data, height, tickMax) {
    var yScale = d3.scaleLinear()
        .domain([0, getMinMax(data)['valueMax']])
        .range([height, 0]);
    var yAxis = d3.axisLeft(yScale)
        .tickFormat(function(d) { return (d + '%') })
        .tickValues(yScale.ticks(tickMax).concat(yScale.domain()));

    return {
        yScale: yScale,
        yAxis: yAxis
    };
}