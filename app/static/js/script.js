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

function imagePopup(id, container, svg_id) {
	document.getElementById(id).classList.add('active');
	d3.select('#button_svg').on('click', function () {downloadImage(container, svg_id, false); hidePopup(id); }).text(`${container.replace('#', '')}.svg`)
	d3.select('#button_png').on('click', function () {downloadImage(container, svg_id, true); hidePopup(id); }).text(`${container.replace('#', '')}.png`)
}

function launchPopup(id) {
	document.getElementById(id).classList.add('active');
}

function hidePopup(id) {
	document.getElementById(id).classList.remove('active');
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

function downloadImage(selector, svg_selector, png) {
	let graph = $(selector);
	let svg = graph.find(`#${svg_selector}`);
	var width = svg.width();
	var height = svg.height();
	var heightPos = height+100;

	d3.select("#downloadShell svg").remove();
	var downloadSvg = d3.select("#downloadShell").append(function() {
		return document.getElementById(svg_selector).cloneNode(true);
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
		downloadSvg.attr('height', '550').attr('width', width+240); //from 950 to width+240
		downloadSvg.select('rect.white-rect').attr('height', '550').attr('width', width+240);//from 950 to width+240
	    heightPos = 470; //add this line
	}

	if(graph.attr('id') === 'timeSeries') {
		downloadSvg.attr('height', '550').attr('width', width+250); //from 950 to width+250
		downloadSvg.select('rect.white-rect').attr('height', '550').attr('width', width+250); //from 950 to width+250
		downloadSvg.selectAll('.line path').attr('style', 'stroke-dasharray: 3; fill: none; stroke: #78909c; stroke-width: 2px;');
		heightPos = 450;
	}

	if(graph.attr('id') === 'heatMap') {

		downloadSvg.attr('height', '650').attr('width', '550');
		downloadSvg.select('rect.white-rect').attr('height', '600').attr('width', '550');
		downloadSvg.selectAll('.heat-map-x-axis-legend-item').attr("style", "font-size: 13px; fill: #4a4a4a;");
		downloadSvg.selectAll('.log-colour-scale text').attr("style", "font-size: 12px; fill: #4a4a4a;");
		downloadSvg.selectAll('.heat-map-y-axis-legend-item').attr("style", "font-size: 13px; color: #4a4a4a;");
		downloadSvg.selectAll('.heat-map-x-axis-legend-item-0').attr("x", "-1");//from 5 to -1
		downloadSvg.selectAll('.heat-map-x-axis-legend-item-1').attr("x", "6");//from 7 to 6
		//downloadSvg.selectAll('.heat-map-x-axis-legend-item-1 tspan').attr("x", "5");
		downloadSvg.selectAll('.heat-map-x-axis-legend-item-2').attr("x", "3"); //from 5 to 3
		//downloadSvg.selectAll('.heat-map-x-axis-legend-item-2 tspan').attr("x", "5");
		downloadSvg.selectAll('.heat-map-x-axis-legend-item-3').attr("x", "67");//from 75 to 67
		downloadSvg.selectAll('.heat-map-x-axis-legend-item-3 tspan').attr("x", "59"); // from 73 to 59
		downloadSvg.selectAll('.heat-map-x-axis-legend-item-4').attr("x", "31"); //from 40 to 31
		downloadSvg.selectAll('.heat-map-x-axis-legend-item-4 tspan').attr("x", "77"); //from 80 to 77
		downloadSvg.selectAll('.heat-map-x-axis-legend-item-5').attr("x", "-5"); //from 2 to -5
		heightPos = 400+125; //from height+150 to 400+150=550

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
		downloadSvg.attr('height', '750').attr('width', '801');//height from 850 to 750, width from 1000 to 801
		downloadSvg.select('rect.white-rect').attr('height', '750').attr('width', '801');//height from 850 to 750, width from 1000 to 801
		downloadSvg.select('.back-rect').attr('height', '540').attr('width', '800').attr('transform', 'translate(-50,0)');
		downloadSvg.select('.countries').attr('transform', 'translate(-50,130),scale('+800/width+')');//add scale('+800/width+')'
		//add the if sentence
		if (height+150*700/width >=600){
		    downloadSvg.select('.legend').attr('transform', 'translate(0,'+(180*700/width-50)+')');
		}else{
		    downloadSvg.select('.legend').attr('transform', 'translate(0,'+180*800/width+')');
		}
		downloadSvg.selectAll('.wm-legend-text').attr("style", "font-size: 13px; fill: #4a4a4a;");
		heightPos = 650; //from 350 to 530+120=650
	}

	if(graph.attr('id') === 'doughnutGraph') {
		downloadSvg.attr('height', '550').attr('width', '400');
		downloadSvg.select('rect.white-rect').attr('height', '550').attr('width', '400');
		downloadSvg.selectAll('.doughnut-legend-rect:nth-child(n+4)').attr("x", "180");
		downloadSvg.selectAll('.doughnut-legend-text[data-title="african-american-or-afro-caribbean"], .doughnut-legend-text[data-title="hispanic-or-latin-american"], .doughnut-legend-text[data-title="other-mixed"], .doughnut-legend-text[data-title="african-american-or-afro-caribbean"] tspan, .doughnut-legend-text[data-title="hispanic-or-latin-american"] tspan, .doughnut-legend-text[data-title="other-mixed"] tspan').attr("x", "220");
		downloadSvg.selectAll('.doughnut-legend-text[data-title="african-american-or-afro-caribbean"] .tspan-percentage').attr("x", "318");//x from 305 to 318
		downloadSvg.selectAll('.doughnut-legend-text[data-title="hispanic-or-latin-american"] .tspan-percentage').attr("x", "279"); //x from 275 to 279
		downloadSvg.selectAll('.doughnut-legend-text').attr("style", "font-size: 13px; fill: #4a4a4a;");
		downloadSvg.select('.doughnut-main-title').attr("style", "font-size: 16px; fill: #4a4a4a;");
		downloadSvg.select('.doughnut-association-title').attr("style", "font-size: 16px; fill: #4a4a4a;");
		downloadSvg.select('.svg-container').attr('transform', 'translate(130,200)');
		downloadSvg.select('.main-title').text(graph.find('.doughnut-graph-header > h3').text());
		downloadSvg.select('.sub-title').text(graph.find('.doughnut-graph-header > small').text() + ' - ' + parentTerm + ' - ' + year);
		downloadSvg.select('.doughnut-legend').attr('transform', 'translate(0,100)');
		heightPos = 444;//from height+120 to 324+120=444
		if (graph.find('.doughnut-graph-filter-association-title').hasClass('active')) {
		    if( height>= 350){
                downloadSvg.select('.svg-container-2').attr("style", "transform: translate(520px,200px) "); //remains to be edited
            }else{
                downloadSvg.select('.svg-container-2').attr('transform', 'translate(520,200)');
            }
			downloadSvg.attr('height', '550').attr('width', '700');
			downloadSvg.select('rect.white-rect').attr('height', '550').attr('width', '700');
			downloadSvg.select('.doughnut-legend').attr('transform', 'translate(200,100)');
			downloadSvg.selectAll('.doughnut-legend-rect:nth-child(n+4)').attr("x", "210");
			downloadSvg.selectAll('.doughnut-legend-text[data-title="african-american-or-afro-caribbean"], .doughnut-legend-text[data-title="hispanic-or-latin-american"], .doughnut-legend-text[data-title="other-mixed"], .doughnut-legend-text[data-title="african-american-or-afro-caribbean"] tspan, .doughnut-legend-text[data-title="hispanic-or-latin-american"] tspan, .doughnut-legend-text[data-title="other-mixed"] tspan').attr("x", "245");
			downloadSvg.selectAll('.doughnut-legend-text[data-title="african-american-or-afro-caribbean"] .tspan-percentage').attr("x", "343");//x from 330 to 343
			downloadSvg.selectAll('.doughnut-legend-text[data-title="african-american-or-afro-caribbean"] .tspan-association').attr("x", "378"); //x from 365 to 378
			downloadSvg.selectAll('.doughnut-legend-text[data-title="hispanic-or-latin-american"] .tspan-percentage').attr("x", "304"); //x from 330 to 304
			downloadSvg.selectAll('.doughnut-legend-text[data-title="hispanic-or-latin-american"] .tspan-association').attr("x", "341");//x from 335 to 341
			downloadSvg.selectAll('.doughnut-legend-text[data-title="other-mixed"] .tspan-association').attr("x", "280");//x from 280 to 282
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

	//citetexts[2].lines = d3.selectAll('#d3plus-textBox-0 text').size() + citetexts[1].lines

	//svgwidth = downloadSvg.node().getBBox().width
	svgwidth = downloadSvg.attr("width");
	new d3plus.TextBox()
	  .data([citetexts[2]])
	  .select(downloadSvg.node())
	  .fontSize(13)
	  .fontFamily('timesnewroman')
	  .fontColor('4a4a4a')
	  .width(parseInt(svgwidth))
	  //.x(function(d, i) { return i * 250; })
	  //.y(function(d, i) { return ((d.lines) * 20) + heightPos; })
	  .y(heightPos)
	  .padding(10)
	  .overflow(false)
	  .render();

	var html = downloadSvg.node().outerHTML;
	var svgBlob = new Blob([html], {type: "image/svg+xml;charset=utf-8"});
	var svgUrl = URL.createObjectURL(svgBlob);
	var downloadLink = document.createElement("a");

	if (png) {
	  var canvas = document.getElementById('downloadCanvas');
      var ctx = canvas.getContext('2d');
      //var bbox = document.getElementById(svg_selector).getBBox();
      //  canvas.width = bbox.width;
      //  canvas.height = bbox.height;
      canvas.width = downloadSvg.attr("width");
      canvas.height = downloadSvg.attr("height");
      //var data = (new XMLSerializer()).serializeToString(svg);
      //var DOMURL = window.URL || window.webkitURL || window;

      var img = new Image();
      //var svgBlob = new Blob([data], {type: 'image/svg+xml;charset=utf-8'});
      //var url = DOMURL.createObjectURL(svgBlob);

      img.onload = function () {
        ctx.drawImage(img, 0, 0);
        //DOMURL.revokeObjectURL(url);
        URL.revokeObjectURL(svgUrl)

        var imgURI = canvas
            .toDataURL('image/png')
            .replace('image/png', 'image/octet-stream');

		downloadLink.href = imgURI;
		downloadLink.download = graph.attr('id') + ".png";
		document.body.appendChild(downloadLink);
		downloadLink.click();
		document.body.removeChild(downloadLink);
      };

      img.src = svgUrl;
	} else {
		downloadLink.href = svgUrl;
		downloadLink.download = graph.attr('id') + ".svg";
		document.body.appendChild(downloadLink);
		downloadLink.click();
		document.body.removeChild(downloadLink);
	}

}

