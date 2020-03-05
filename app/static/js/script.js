// Menu

var header = $('#header');
var burger = header.find('#burger-menu');
var nav = header.find('#nav-menu');

burger.click(function() {
    $(this).toggleClass('active');
    nav.toggleClass('active');
});


// Scroll

$(window).scroll(function() {
    var scroll = $(window).scrollTop();
    if(scroll > 20) {
        header.addClass('hide-logo');
    } else {
        header.removeClass('hide-logo');
    }
});


// Pop up

var footerPopup = $('#popup-footer');

function launchPopup() {
    footerPopup.addClass('active');
}

function hidePopup() {
    footerPopup.removeClass('active');
}


// Set description

function setDescription() {
    var box = $('.box:not(.summary)');
    for(i = 0; i < box.length; i++) {
        var id = $(box[i]).attr('id');
        var title = $(box[i]).find('h3');
        var secondTitle = '';
        if($(title[1]).text()) {
            secondTitle = ' - ' + $(title[1]).text();
        }
        var titleText = $(title[0]).text() + secondTitle;
        var svg = d3.select('#' + id + ' svg');

        if(svg.select('desc').empty()) {
            svg.append('desc').text(titleText);
        } else {
            svg.select('desc').text(titleText);
        }
    }
}


// Download image

function downloadImage() {
    var box = $('.box');

    box.find(".icon-zone .icon-download-image").click(function () {
        var graph = $(this).parents('.box');
        var svg = graph.find('svg');
        var width = svg.width();
        var height = svg.height();
        var heightPos = height+100;

        d3.select("#downloadShell svg").remove();
        var downloadSvg = d3.select("#downloadShell").append(function() {
            return svg[0].cloneNode(true);
        });

        downloadSvg
            .attr("version", 1.1)
            .attr("xmlns", "http://www.w3.org/2000/svg")
            .append("style").text(".heat-map-y-axis-legend-item { margin: 0;} svg { width: 100%; height: 100%; overflow: hidden; display: -webkit-box; display: -webkit-flex; display: -moz-flex; display: -ms-flexbox; display: flex; -webkit-box-flex: 1; -webkit-flex: 1; -moz-box-flex: 1; -moz-flex: 1; -ms-flex: 1; flex: 1; } svg .grid line { stroke-dasharray: 10; fill: none; stroke: #e9edee; stroke-width: 2px; } svg .line path { stroke-dasharray: 3; fill: none; stroke: #78909c; stroke-width: 2px; } svg .grid path, svg .grid-y path, svg .axis path, svg .axis-y path { stroke: none; } svg .axis-x .tick text, svg .axis-y .tick text { font-size: 13px; fill: #78909c; } svg .axis-x path, svg .axis-x .tick line { fill: none; stroke: #e9edee; stroke-width: 2px; } svg .axis-x .tick text { -moz-transform: translateY(8px); -o-transform: translateY(8px); -ms-transform: translateY(8px); -webkit-transform: translateY(8px); transform: translateY(8px); } svg .axis-x .tick:last-child line { stroke: none; } svg .axis-y .tick text { -moz-transform: translateX(-4px); -o-transform: translateX(-4px); -ms-transform: translateX(-4px); -webkit-transform: translateX(-4px); transform: translateX(-4px); } svg .axis-y .tick line { stroke: none; } svg .axis-y .tick:first-of-type text { display: none; } circle.african, .circle.african { fill: #f59c44; background: #f59c44; } circle.african-american-or-afro-caribbean, .circle.african-american-or-afro-caribbean { fill: #fece59; background: #fece59; } circle.asian, .circle.asian { fill: #489ed8; background: #489ed8; } circle.european, .circle.european { fill: #ed7892; background: #ed7892; } circle.hispanic-or-latin-american, .circle.hispanic-or-latin-american { fill: #9767ab; background: #9767ab; } circle.other-mixed, .circle.other-mixed { fill: #54bdbe; background: #54bdbe; } circle.in-part-not-recorded, .circle.in-part-not-recorded { fill: #78909c; background: #78909c; } .back-rect { fill: #d9dbdf; } svg#bubbleSVG .svg-container circle { opacity: .7; display: none; } svg#bubbleSVG circle.opaque { opacity: 1; } svg#bubbleSVG circle.disabled { fill: rgba(128, 128, 128, .4); pointer-events: none; } svg#bubbleSVG circle.selected { stroke: #78909c; stroke-width: 2; stroke-dasharray: 3; pointer-events: none; } svg#bubbleSVG.term-all circle, svg#bubbleSVG.term-other-measurement circle.other-measurement, svg#bubbleSVG.term-cardiovascular-measurement circle.cardiovascular-measurement, svg#bubbleSVG.term-neurological-disorder circle.neurological-disorder, svg#bubbleSVG.term-digestive-system-disorder circle.digestive-system-disorder, svg#bubbleSVG.term-cancer circle.cancer, svg#bubbleSVG.term-cardiovascular-disease circle.cardiovascular-disease, svg#bubbleSVG.term-metabolic-disorder circle.metabolic-disorder, svg#bubbleSVG.term-other-disease circle.other-disease, svg#bubbleSVG.term-biological-process circle.biological-process, svg#bubbleSVG.term-immune-system-disorder circle.immune-system-disorder, svg#bubbleSVG.term-response-to-drug circle.response-to-drug, svg#bubbleSVG.term-other-trait circle.other-trait, svg#bubbleSVG.term-body-measurement circle.body-measurement, svg#bubbleSVG.term-hematological-measurement circle.hematological-measurement, svg#bubbleSVG.term-lipid-or-lipoprotein-measurement circle.lipid-or-lipoprotein-measurement, svg#bubbleSVG.term-nflammatory-measurement circle.inflammatory-measurement, svg#bubbleSVG.term-liver-enzyme-measurement circle.liver-enzyme-measurement { display: block; } svg#bubbleSVG.ancestry-african .svg-container circle.african, svg#bubbleSVG.ancestry-african-american-or-afro-caribbean .svg-container circle.african-american-or-afro-caribbean, svg#bubbleSVG.ancestry-asian .svg-container circle.asian, svg#bubbleSVG.ancestry-european .svg-container circle.european, svg#bubbleSVG.ancestry-hispanic-or-latin-american .svg-container circle.hispanic-or-latin-american, svg#bubbleSVG.ancestry-other-mixed .svg-container circle.other-mixed { display: none; }");

        downloadSvg.append("g")
            .attr("class", "title")
            .attr("transform", "translate(60,40)")
            .append("text")
            .attr("class", "main-title")
            .attr("x", 0)
            .attr("y", 0)
            .attr("dy", "1em")
            .attr("style", "font-size: 16px; font-weight: bold; fill: #212c8f;")
            .text(graph.find("[class*='-header'] h3, .header h3").text());

        downloadSvg.select(".title")
            .append("text")
            .attr("class", "sub-title")
            .attr("x", 0)
            .attr("y", 0)
            .attr("dy", "2.5em")
            .attr("style", "font-size: 13px; fill: #4a4a4a;")
            .text(graph.find("[class*='-header'] small, .header small").text());

        var legend = downloadSvg.append("g").attr("class", "legend").attr("transform", "translate(" + (width+25) + ", 100)");
        var offset = 0;
        var parentTerm = graph.find('.gwas-select-container-single option:selected').attr('name');
        var year = graph.find('[class*="-change-year"] span').text();

        if(parentTerm == 'all') {
            parentTerm = 'All parent terms'
        }

        if (graph.find("circle.european").length > 0) {
            var european = legend.append("g")
                .attr("class", "european")
                .attr("transform", "translate(0, " + offset + ")");
            european.append("circle")
                .attr("class", "european")
                .attr("r", 5);
            european.append("text")
                .text("European")
                .attr("style", "font-size: 13px; fill: #4a4a4a;")
                .attr("x", 10)
                .attr("y", 5);
            offset += 20;
        }

        if (graph.find("circle.african").length > 0) {
            var african = legend.append("g")
                .attr("class", "african")
                .attr("transform", "translate(0, " + offset + ")");
            african.append("circle")
                .attr("class", "african")
                .attr("r", 5);
            african.append("text")
                .text("African")
                .attr("style", "font-size: 13px; fill: #4a4a4a;")
                .attr("x", 10)
                .attr("y", 5);
            offset += 20;
        }

        if (graph.find("circle.african-american-or-afro-caribbean").length > 0) {
            var africanAmCaribbean = legend.append("g")
                .attr("class", "african-american-or-afro-caribbean")
                .attr("transform", "translate(0, " + offset + ")");
            africanAmCaribbean.append("circle")
                .attr("class", "african-american-or-afro-caribbean")
                .attr("r", 5);
            africanAmCaribbean.append("text")
                .text("African American or Caribbean")
                .attr("style", "font-size: 13px; fill: #4a4a4a;")
                .attr("x", 10)
                .attr("y", 5);
            offset += 20;
        }

        if (graph.find("circle.other-mixed").length > 0) {
            var otherMixed = legend.append("g")
                .attr("class", "other-mixed")
                .attr("transform", "translate(0, " + offset + ")");
            otherMixed.append("circle")
                .attr("class", "other-mixed")
                .attr("r", 5);
            otherMixed.append("text")
                .text("Other/Mixed")
                .attr("style", "font-size: 13px; fill: #4a4a4a;")
                .attr("x", 10)
                .attr("y", 5);
            offset += 20;
        }

        if (graph.find("circle.asian").length > 0) {
            var asian = legend.append("g")
                .attr("class", "asian")
                .attr("transform", "translate(0, " + offset + ")");
            asian.append("circle")
                .attr("class", "asian")
                .attr("r", 5);
            asian.append("text")
                .text("Asian")
                .attr("style", "font-size: 13px; fill: #4a4a4a;")
                .attr("x", 10)
                .attr("y", 5);
            offset += 20;
        }

        if (graph.find("circle.hispanic-or-latin-american").length > 0) {
            var hispanicLatinAmerican = legend.append("g")
                .attr("class", "hispanic-or-latin-american")
                .attr("transform", "translate(0, " + offset + ")");
            hispanicLatinAmerican.append("circle")
                .attr("class", "hispanic-or-latin-american")
                .attr("r", 5);
            hispanicLatinAmerican.append("text")
                .text("Hispanic or Latin American")
                .attr("style", "font-size: 13px; fill: #4a4a4a;")
                .attr("x", 10)
                .attr("y", 5);
            offset += 20;
        }

        if (graph.find("circle.in-part-not-recorded").length > 0) {
            var inPart = legend.append("g")
                .attr("class", "in-part-not-recorded")
                .attr("transform", "translate(0, " + offset + ")");
            inPart.append("circle")
                .attr("class", "in-part-not-recorded")
                .attr("r", 5);
            inPart.append("text")
                .text("In Part Not Recorded")
                .attr("style", "font-size: 13px; fill: #4a4a4a;")
                .attr("x", 10)
                .attr("y", 5);
            offset += 20;
        }

        var logo = d3.select("#watermark svg").node().cloneNode(true);
        downloadSvg.node().appendChild(logo);
        downloadSvg.select("svg");

        if(graph.attr('id') === 'worldMap' || graph.attr('id') === 'heatMap') {
            downloadSvg.select('.sub-title')
                .text(graph.find("[class*='-header'] small, .header small").text() + ' - ' + year);
        } else if(graph.attr('id') === 'bubbleGraph') {
            downloadSvg.select('.sub-title')
                .text(graph.find("[class*='-header'] small, .header small").text() + ' - ' + parentTerm);
        }

        if(graph.attr('id') === 'bubbleGraph') {
            downloadSvg.attr('height', '550').attr('width', '950');
        }

        if(graph.attr('id') === 'timeSeries') {
            downloadSvg.attr('height', '550').attr('width', '700');
            downloadSvg.selectAll('.line path').attr('style', 'stroke-dasharray: 3; fill: none; stroke: #78909c; stroke-width: 2px;');
            heightPos = height+70;
        }

        if(graph.attr('id') === 'heatMap') {
            downloadSvg.attr('height', '600').attr('width', '550');
            downloadSvg.selectAll('.heat-map-x-axis-legend-item').attr("style", "font-size: 13px; fill: #4a4a4a;");
            downloadSvg.selectAll('.log-colour-scale text').attr("style", "font-size: 12px; fill: #4a4a4a;");
            downloadSvg.selectAll('.heat-map-y-axis-legend-item').attr("style", "font-size: 13px; color: #4a4a4a;");
            downloadSvg.selectAll('.heat-map-x-axis-legend-item-0').attr("x", "5");
            downloadSvg.selectAll('.heat-map-x-axis-legend-item-1').attr("x", "7");
            downloadSvg.selectAll('.heat-map-x-axis-legend-item-1 tspan').attr("x", "5");
            downloadSvg.selectAll('.heat-map-x-axis-legend-item-2').attr("x", "5");
            downloadSvg.selectAll('.heat-map-x-axis-legend-item-2 tspan').attr("x", "5");
            downloadSvg.selectAll('.heat-map-x-axis-legend-item-3').attr("x", "75");
            downloadSvg.selectAll('.heat-map-x-axis-legend-item-3 tspan').attr("x", "73");
            downloadSvg.selectAll('.heat-map-x-axis-legend-item-4').attr("x", "40");
            downloadSvg.selectAll('.heat-map-x-axis-legend-item-4 tspan').attr("x", "80");
            downloadSvg.selectAll('.heat-map-x-axis-legend-item-5').attr("x", "2");
            heightPos = height+150;

            // Redraw y-axis
            var yAxisLegend = svg.find('.heat-map-y-axis-legend-item');
            var yAxisArray = [];
            offset = 18;
            for(i = 0; i < yAxisLegend.length; i++) {
                var yAxisText = $(yAxisLegend[i]).text();
                yAxisArray.push(yAxisText);
            }
            yAxisArray.reverse();
            downloadSvg.selectAll('foreignObject').remove();
            jQuery.each(yAxisArray, function(key, value) {
                downloadSvg.select('.axis-legend-container')
                    .append('text')
                    .text(value)
                    .attr('class', 'heat-map-y-axis-legend-item')
                    .attr("style", "font-size: 13px; fill: #4a4a4a;")
                    .attr('x', 168)
                    .attr('y', offset);
                offset += 17;
            });
        }

        if(graph.attr('id') === 'worldMap') {
            var svgContainer = $('#worldMap .svg-container');
            downloadSvg.attr('height', '850').attr('width', '1000');
            downloadSvg.select('.back-rect').attr('height', '530').attr('width', '800').attr('transform', 'translate(-50,0)');
            downloadSvg.select('.countries').attr('transform', 'translate(0,150)');
            downloadSvg.select('.legend').attr('transform', 'translate(0, 155)');
            downloadSvg.selectAll('.wm-legend-text').attr("style", "font-size: 13px; fill: #4a4a4a;");
            heightPos = height+350;
        }

        if(graph.attr('id') === 'doughnutGraph') {
            downloadSvg.attr('height', '550').attr('width', '400');
            downloadSvg.selectAll('.doughnut-legend-rect:nth-child(n+4)').attr("x", "180");
            downloadSvg.selectAll('.doughnut-legend-text[data-title="african-american-or-afro-caribbean"], .doughnut-legend-text[data-title="hispanic-or-latin-american"], .doughnut-legend-text[data-title="other-mixed"], .doughnut-legend-text[data-title="african-american-or-afro-caribbean"] tspan, .doughnut-legend-text[data-title="hispanic-or-latin-american"] tspan, .doughnut-legend-text[data-title="other-mixed"] tspan').attr("x", "220");
            downloadSvg.selectAll('.doughnut-legend-text[data-title="african-american-or-afro-caribbean"] .tspan-percentage').attr("x", "305");
            downloadSvg.selectAll('.doughnut-legend-text[data-title="hispanic-or-latin-american"] .tspan-percentage').attr("x", "275");
            downloadSvg.selectAll('.doughnut-legend-text').attr("style", "font-size: 13px; fill: #4a4a4a;");
            downloadSvg.select('.doughnut-main-title').attr("style", "font-size: 16px; fill: #4a4a4a;");
            downloadSvg.select('.doughnut-association-title').attr("style", "font-size: 16px; fill: #4a4a4a;");
            downloadSvg.select('.svg-container').attr('transform', 'translate(130,200)');
            downloadSvg.select('.svg-container-2').attr('transform', 'translate(520,200)');
            downloadSvg.select('.main-title').text(graph.find('.doughnut-graph-header > h3').text());
            downloadSvg.select('.sub-title').text(graph.find('.doughnut-graph-header > small').text() + ' - ' + parentTerm + ' - ' + year);
            downloadSvg.select('.doughnut-legend').attr('transform', 'translate(0,100)');
            heightPos = height+120;
            if (graph.find('.doughnut-graph-filter-association-title').hasClass('active')) {
                downloadSvg.attr('height', '550').attr('width', '700');
                downloadSvg.select('.doughnut-legend').attr('transform', 'translate(200,100)');
                downloadSvg.selectAll('.doughnut-legend-rect:nth-child(n+4)').attr("x", "210");
                downloadSvg.selectAll('.doughnut-legend-text[data-title="african-american-or-afro-caribbean"], .doughnut-legend-text[data-title="hispanic-or-latin-american"], .doughnut-legend-text[data-title="other-mixed"], .doughnut-legend-text[data-title="african-american-or-afro-caribbean"] tspan, .doughnut-legend-text[data-title="hispanic-or-latin-american"] tspan, .doughnut-legend-text[data-title="other-mixed"] tspan').attr("x", "245");
                downloadSvg.selectAll('.doughnut-legend-text[data-title="african-american-or-afro-caribbean"] .tspan-percentage').attr("x", "330");
                downloadSvg.selectAll('.doughnut-legend-text[data-title="african-american-or-afro-caribbean"] .tspan-association').attr("x", "365");
                downloadSvg.selectAll('.doughnut-legend-text[data-title="hispanic-or-latin-american"] .tspan-percentage').attr("x", "300");
                downloadSvg.selectAll('.doughnut-legend-text[data-title="hispanic-or-latin-american"] .tspan-association').attr("x", "335");
                downloadSvg.selectAll('.doughnut-legend-text[data-title="other-mixed"] .tspan-association').attr("x", "280");
                downloadSvg.append("g")
                    .attr("class", "second-title")
                    .attr("transform", "translate(400,40)")
                    .append("text")
                    .attr("x", 0)
                    .attr("y", 0)
                    .attr("dy", "1em")
                    .attr("style", "font-size: 16px; font-weight: bold; fill: #212c8f;")
                    .text(graph.find(".doughnut-graph-filter-association-title h3").text());
                downloadSvg.select(".second-title")
                    .append("text")
                    .attr("x", 0)
                    .attr("y", 0)
                    .attr("dy", "2.5em")
                    .attr("style", "font-size: 13px; fill: #4a4a4a;")
                    .text(graph.find(".doughnut-graph-filter-association-title small").text() + ' - ' + parentTerm + ' - ' + year);
            } else {
                downloadSvg.select('.legend').attr('style', 'transform: translate(0,100px);');
            }
        } else {
            downloadSvg.select(".svg-container").attr("transform", "translate(50,100)");
        }

        let popup = $('#popup-footer p');
		let citetexts = Array.from(popup.map(i => ({'text': popup[i].textContent})))

		//citetexts[1].lines = 0
		//new d3plus.TextBox()
        //  .data([citetexts[0]])
        //  .select(downloadSvg.node())
        //  .fontSize(13)
        //  .fontFamily('')
        //  .fontColor('4a4a4a')
        //  .width(width)
        //  //.x(function(d, i) { return i * 250; })
        //  //.y(function(d, i) { return i * 20 + heightPos; })
        //  .y(heightPos)
        //  .x(10)
        //  .render();
//
		//citetexts[1].lines = d3.selectAll('#d3plus-textBox-0 text').size()
		//new d3plus.TextBox()
        //  .data([citetexts[1]])
        //  .select(downloadSvg.node())
        //  .fontSize(13)
        //  .fontFamily('')
        //  .fontColor('4a4a4a')
        //  .width(width)
        //  //.x(function(d, i) { return i * 250; })
        //  //.y(function(d, i) { return i * 20 + heightPos; })
        //  .y(heightPos)
        //  .x(10)
        //  .render();
//
        //citetexts[2].lines = d3.selectAll('#d3plus-textBox-0 text').size() + citetexts[1].lines

		new d3plus.TextBox()
          .data([citetexts[2]])
          .select(downloadSvg.node())
          .fontSize(13)
          .fontFamily('')
          .fontColor('4a4a4a')
          .width(width)
          //.x(function(d, i) { return i * 250; })
          //.y(function(d, i) { return ((d.lines) * 20) + heightPos; })
          .y(heightPos)
          .x(10)
          .render();

        var html = downloadSvg.node().outerHTML;
        var svgBlob = new Blob([html], {type: "image/svg+xml;charset=utf-8"});
        var svgUrl = URL.createObjectURL(svgBlob);
        var downloadLink = document.createElement("a");

        downloadLink.href = svgUrl;
        downloadLink.download = graph.attr('id') + ".svg";
        document.body.appendChild(downloadLink);
        downloadLink.click();
        document.body.removeChild(downloadLink);
    });
}
