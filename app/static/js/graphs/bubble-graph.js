function drawBubbleGraph(selector, data, replication) {
    if(replication) {
        data = data.bubblegraph_replication;
    } else {
        data = data.bubblegraph_initial;
    }
    data = Object.keys(data).map(function(_) { return data[_]; });

    var bubbleGraph = $(selector);
    var tickMax = 8;

    if($(window).width() < 480) {
        var graphHeight = 300;
    } else {
        var graphHeight = 360;
    }

    var margin = {top: 40, right: 30, bottom: 30, left: 40},
        width = bubbleGraph.find('.left').width() - margin.left - margin.right,
        height = graphHeight - margin.top - margin.bottom;

    sanitiseSVG(selector);
    let svg_id = 'bubbleSVG'
    let svg_selector = `#${svg_id}`

    var mainSvg = d3.select(selector)
        .append("svg")
        .attr("id", svg_id)
        .attr("class", "term-all")
        .attr("height", height + margin.top + margin.bottom);

    mainSvg.append('rect').attr('class', 'white-rect').attr('fill', '#ffff').attr('style', 'fill: white;').attr('height', '550').attr('width', '950');

    var svg = mainSvg.append("g")
        .attr("class", "svg-container")
        .attr("transform", "translate(" + margin.left + "," + margin.top + ")");

    // Add X axis
    var minYear = getYear(data)['minYear'];
    var maxYear = getYear(data)['maxYear'];

    const xScale = d3.scaleTime()
        .domain([minYear, maxYear])
        .range([0, width]);

    const xAxis = svg.append("g")
        .attr('class', 'axis-x')
        .attr("transform", "translate(0," + height + ")")
        .call(d3.axisBottom(xScale).ticks(6));

    var max = getDataMax(data);

    // Add Y axis
    const yScale = d3.scaleLinear()
        .domain([0, max])
        .range([height, 0]);

    var sizeScale = d3.scalePow()
        .exponent(2)
        .domain([0, max])
        .range([5, 40]);

    // Add the Y gridlines
    svg.append('g')
        .attr('class', 'grid')
        .call(d3.axisLeft(yScale)
            .ticks(tickMax)
            .tickSize(-width)
            .tickFormat('')
        );


    const yAxis = svg.append("g")
        .attr("class", "axis-y")
        .call(d3.axisLeft(yScale).ticks(tickMax, "s"));

    // Set the zoom and Pan features: how much you can zoom, on which part, and what to do when there is a zoom
    var zoom = d3.zoom()
        .scaleExtent([1, 20])  // This control how much you can unzoom (x0.5) and zoom (x20)
        .translateExtent([[0, 0], [width, height]])
        .extent([[0, 0], [width, height]])
        .on("zoom", zoomBubbleChart(xScale, yScale, xAxis, yAxis));

    var bubbleDataGroup = svg.append("g").attr("id", "bubbleData");
    bubbleDataGroup.append("rect")
            .attr("class", "background")
            .attr("width", "100%")
            .attr("height", "100%")
            .attr("fill", "white")
            .attr("opacity", 0)
            .attr("onmouseover", "backgroundMouseOver(evt)")
            .attr("onclick", "clearSelected()").call(zoom);

    bubbleDataGroup.selectAll("bubble")
        .data(data)
        .enter()
        .append("circle")
        .attr("class", function (d) {
            return d.Broader.replace(' ', '-').replace('/', '-').replace(' ', '-').replace(' ', '-').toLowerCase() + " " +
                d.parentterm.replace(/, /g, ',').replace(/ /g, '-').replace(/,/g, ' ').toLowerCase();
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

    $(selector).find(".ancestry-filter .option").click(function() {
        $(this).toggleClass("active"); //if active exists, then delete ; if no, add it
        var filter = $(this).attr("dataFilter");
        var parentSVG = $(svg_selector);

        if (filter === "all") {
            $.each(parentSVG.attr("class").split(" "), function(index, value) {
                if(value.startsWith("ancestry-")) {
                    parentSVG.removeClass(value);
                    $(".ancestry-filter .option").removeClass('active');
                }
            });
        } else {
            parentSVG.removeClass("ancestry-all");
        }

        parentSVG.toggleClass("ancestry-" + filter);

        var filters = parentSVG.attr("class");

        reDrawBubbleGraph(data, filters, selector, xScale, yScale, sizeScale, tickMax);//add xScale

        //redraw the selected data
        var controller = ""
        if(parentSVG.attr("class").search("ancestry-" + filter) != -1){
            controller = "delete";
        }else{
             controller = "add";
        }

        var selected=$(selector).find(".filter select[name='trait']").val()

        if(selected.length>0){

            //remove the bubbles added before
                var selectedBefore = $(`${selector} #bubbleData circle`).not( function(index, element) {
                if( element.getAttribute("ancestrySelection").search("ANCESTRY") !== -1 ){ return false;}
                else{ return true;}
               });


                $(selectedBefore).remove();

                var selectedBefore_pattern = $(`${selector} #bubbleData circle`).not(function(index, element) {
                if( element.getAttribute("PatternSelection").search("NO") == -1 ){ return false;}
                else{ return true;}
               });

                $(selectedBefore_pattern).remove();

               //var total=$(`${selector} #bubbleData circle`)




            var selectedBubbles = $(`${svg_selector} #bubbleData circle`).not(function(index, element) {
                if(selected.includes(element.getAttribute("trait"))&& (controller == "delete") && (element.getAttribute("class").search(filter) == -1)){

                    return false;}
                 else if (selected.includes(element.getAttribute("trait")) && (controller == "add") && (element.getAttribute("class").search("disabled") == -1)){
                   return false;}
                else{ return true;}

            });


            for(i = 0; i < selectedBubbles.length; i++) {
                var classAttr = $(selectedBubbles[i]).attr('class');
                /*
                if (classAttr.search("opaque") == -1){
                    classAttr= classAttr +" opaque";
                    window.alert("opaque added")
                    }
                    */
                classAttr = [...new Set(classAttr.split(" "))].join(" ");

                var cxAttr = $(selectedBubbles[i]).attr('cx');
                //var cyAttr = $(selectedBubbles[i]).attr('cy');
                //var rAttr = $(selectedBubbles[i]).attr('r');
                var pubmedidAttr = $(selectedBubbles[i]).attr('pubmedid');
                var authorAttr = $(selectedBubbles[i]).attr('author');
                var accessionAttr = $(selectedBubbles[i]).attr('accession');
                var nAttr = $(selectedBubbles[i]).attr('N');
                var diseaseOrTraitAttr = $(selectedBubbles[i]).attr('DiseaseOrTrait');
                var traitAttr = $(selectedBubbles[i]).attr('trait');
                var cyAttr = yScale(nAttr);
                var rAttr = sizeScale(nAttr);
                var patternAttr=$(selectedBubbles[i]).attr('PatternSelection');

                bubbleDataGroup.append("circle")
                    .attr("class", classAttr)
                    .attr("onclick", "circleClick(evt);")
                    .attr("onmouseover", "circleMouseOver(evt);")
                    .attr("cx", cxAttr)
                    //.attr("cy", function (nAttr) { return yScale(nAttr); })
                    //.attr("r", function(nAttr){ return sizeScale(nAttr) })
                    .attr("cy", cyAttr)
                    .attr("r", rAttr)
                    .attr("pubmedid", pubmedidAttr)
                    .attr("author", authorAttr)
                    .attr("accession", accessionAttr)
                    .attr("N", nAttr)
                    .attr("DiseaseOrTrait", diseaseOrTraitAttr)
                    .attr("trait", traitAttr)
                    .attr("PatternSelection",patternAttr)
                    .attr("ancestrySelection","ANCESTRY");
                //$(selectedBubbles[i]).remove();

            }

        }


    });

    $(selector).find(".filter select[name='parentTerms']").change(function() {
        var selected_ = $(this).find('option:selected');
        var parentSVG = $(svg_selector);

        $.each(parentSVG.attr("class").split(" "), function(index, value) {
            if(value.indexOf("term-") !== -1) {
                parentSVG.removeClass(value);
            }
        });

        parentSVG.addClass("term-" + selected_.attr('value'));

        var filters = parentSVG.attr("class");

        reDrawBubbleGraph(data, filters, selector, xScale, yScale, sizeScale, tickMax); //Add xScale

        //redraw the selected data

        var selected=$(selector).find(".filter select[name='trait']").val()


        if(selected.length>0 || (filters.search("all")!= -1)){

            //remove the bubbles added before
                var selectedBefore_ancestry = $(`${selector} #bubbleData circle`).not(function(index, element) {
                if( element.getAttribute("ancestrySelection").search("ANCESTRY") !== -1 ){ return false;}
                else{ return true;}
               });

                $(selectedBefore_ancestry).remove();

                var selectedBefore = $(`${selector} #bubbleData circle`).not(function(index, element) {
                if( element.getAttribute("PatternSelection").search("NO") == -1 ){ return false;}
                else{ return true;}
               });

                $(selectedBefore).remove();

            var all_controller="";
                if ( (selected.length>0) && filters.search("all")!= -1){
                    all_controller="yes";
                }

            var selectedBubbles = $(`${svg_selector} #bubbleData circle`).not(function(index, element) {
                if( selected.includes(element.getAttribute("trait")) && (element.getAttribute("class").search(selected_.attr('value')) !== -1)){
                    return false;}
                else if( (all_controller=="yes") && (element.getAttribute("class").search("disabled") == -1) ){
                    return false;}
                else{ return true;}

            });


            for(i = 0; i < selectedBubbles.length; i++) {
                var classAttr = $(selectedBubbles[i]).attr('class');

                if (classAttr.search("opaque") == -1){
                    classAttr= classAttr +" opaque";

                    }
                classAttr = [...new Set(classAttr.split(" "))].join(" ");

                var cxAttr = $(selectedBubbles[i]).attr('cx');
                var pubmedidAttr = $(selectedBubbles[i]).attr('pubmedid');
                var authorAttr = $(selectedBubbles[i]).attr('author');
                var accessionAttr = $(selectedBubbles[i]).attr('accession');
                var nAttr = $(selectedBubbles[i]).attr('N');
                var diseaseOrTraitAttr = $(selectedBubbles[i]).attr('DiseaseOrTrait');
                var traitAttr = $(selectedBubbles[i]).attr('trait');
                var cyAttr = yScale(nAttr);
                var rAttr = sizeScale(nAttr);
                var ancestryAttr=$(selectedBubbles[i]).attr('ancestrySelection');

                bubbleDataGroup.append("circle")
                    .attr("class", classAttr)
                    .attr("onclick", "circleClick(evt);")
                    .attr("onmouseover", "circleMouseOver(evt);")
                    .attr("cx", cxAttr)
                    .attr("cy", cyAttr)
                    .attr("r", rAttr)
                    .attr("pubmedid", pubmedidAttr)
                    .attr("author", authorAttr)
                    .attr("accession", accessionAttr)
                    .attr("N", nAttr)
                    .attr("DiseaseOrTrait", diseaseOrTraitAttr)
                    .attr("trait", traitAttr)
                    .attr("PatternSelection","Pattern")
                    .attr("ancestrySelection",ancestryAttr);

            }

        }



    });


    $(selector).find(".filter select[name='trait']").change(function() {

        clearSelected();
        var selectedBefore = $(`${selector} #bubbleData circle`).not( function(index, element) {
                if( element.getAttribute("ancestrySelection").search("ANCESTRY") !== -1 ){ return false;}
                else{ return true;}
               });

        $(selectedBefore).remove();

        var selectedBefore_pattern = $(`${selector} #bubbleData circle`).not(function(index, element) {
                if( element.getAttribute("PatternSelection").search("NO") == -1 ){ return false;}
                else{ return true;}
               });

         $(selectedBefore_pattern).remove();

        var selected = $(this).val();

        if(selected.length == 0) {
            $(`${svg_selector} #bubbleData circle`).removeClass("disabled opaque");
        } else {
            $(`${svg_selector} #bubbleData circle`).not(function(index, element) {
                return selected.includes(element.getAttribute("trait"))
            }).addClass("disabled");

            var selectedBubbles = $(`${svg_selector} #bubbleData circle`).not(function(index, element) {
                return !selected.includes(element.getAttribute("trait"))
            });

            selectedBubbles.removeClass("disabled");


            for(i = 0; i < selectedBubbles.length; i++) {
                var classAttr = $(selectedBubbles[i]).attr('class');
                var cxAttr = $(selectedBubbles[i]).attr('cx');
                var cyAttr = $(selectedBubbles[i]).attr('cy');
                var rAttr = $(selectedBubbles[i]).attr('r');
                var pubmedidAttr = $(selectedBubbles[i]).attr('pubmedid');
                var authorAttr = $(selectedBubbles[i]).attr('author');
                var accessionAttr = $(selectedBubbles[i]).attr('accession');
                var nAttr = $(selectedBubbles[i]).attr('N');
                var diseaseOrTraitAttr = $(selectedBubbles[i]).attr('DiseaseOrTrait');
                var traitAttr = $(selectedBubbles[i]).attr('trait');
                var patternAttr=$(selectedBubbles[i]).attr("PatternSelection");
                var ancestryAttr=$(selectedBubbles[i]).attr("ancestrySelection");
                $(selectedBubbles[i]).remove();
                bubbleDataGroup.append("circle")
                    .attr("class", classAttr + " opaque")
                    .attr("onclick", "circleClick(evt);")
                    .attr("onmouseover", "circleMouseOver(evt);")
                    .attr("cx", cxAttr)
                    .attr("cy", cyAttr)
                    .attr("r", rAttr)
                    .attr("pubmedid", pubmedidAttr)
                    .attr("author", authorAttr)
                    .attr("accession", accessionAttr)
                    .attr("N", nAttr)
                    .attr("DiseaseOrTrait", diseaseOrTraitAttr)
                    .attr("trait", traitAttr)
                    .attr("PatternSelection",patternAttr)
                    .attr("ancestrySelection",ancestryAttr);

            }

        }

    });


    // When click on filter stage
    $('#cb2').change(function() {
        $('.ancestry-filter .btn').removeClass('active');
    })

    d3.select('#bubble-graph-controls').on('click', function () {imagePopup('popup-download-image', selector, svg_id)});

    var svgs = bubbleGraph.find('svg');
    if (svgs.length > 1) {
        bubbleGraph.find(".icon-zone .icon-download-image").unbind();
    }

    // Select
    $("select[name='trait']").select2({
        multiple: true,
        minimumInputLength: 3,
        placeholder: "Search for one or more traits",
        ajax: {
            url: '/api/traits',
            data: function (params) {
            var query = {
                search: params.term,
            }

            // Query parameters will be ?search=[term]
            return query;
            }
        }
    });
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

function sanitiseSVG(selector) {
    $(selector).find(".filter select[name='trait']").val(null).trigger('change');

    $(selector + " svg").remove();
    clearSelected();
    $(selector).find(".filter .option").unbind();
    $(selector).find(".filter .option").removeClass("active");
    $(selector).find(".filter select").unbind();
    $(selector).find(".filter select[name='parentTerms']").val("all");
}
