function drawDoughnutGraph(selector, data, withMetric, withStage) {
    window.withMetric = withMetric;
    window.withStage = withStage;

    let margin = 0,
        width = 270 - margin,
        height = 324 - margin;

	let svg_id = 'doughnutSVG'
    let svg_selector = `#${svg_id}`
    d3.select('#doughnutGraph svg').remove();

    let mainSvg = d3.select("#doughnutGraph")
        .append("svg")
        .attr("id", svg_id)
        .attr("class", "term-all")
        .attr("width", width)
        .attr("height", height);

    mainSvg.append('rect').attr('class', 'white-rect').attr('fill', '#ffff').attr('style', 'fill: white;').attr('height', '550').attr('width', '700');

    let svg = mainSvg.append("g")
        .attr('class', 'svg-container')
        .attr("transform", "translate(" + width / 2  + "," + height / 3 + ")");

    let specificData;
    let dataKeys;
    let currentYear;
    let selected;
    let legend;
    window.associationSwitch = document.getElementById('cbAssociation');
    let tspanAssociation;
    let rectAssociation;
    let textAssociation;
    let noDataSpan;

    let associationDoughnut = d3.select("#doughnutGraph svg")
        .append('g')
        .attr('class', 'svg-container-2')
        .attr("transform", "translate(" + width*2  + "," + height / 3 + ")");

    let doughnutSVG = document.querySelector('#doughnutSVG');

    if (window.innerWidth > 600 && window.innerWidth <= 1440) {
        associationDoughnut.attr("transform", "translate(" + width*1.6  + "," + height / 3 + ")");
    }

    // Define the div for the tooltip
    d3.select('.d3-tooltip.doughnut').remove();
    let d3Tooltip = d3.select("body").append("div")
        .attr("class", "d3-tooltip doughnut")
        .style("opacity", 0);

    let nextButtons = document.querySelectorAll('.doughnut-graph-change-year button.next');
    let previousButtons = document.querySelectorAll('.doughnut-graph-change-year button.previous');

    let dateSpan = document.querySelector('.doughnut-graph-change-year span');
    currentYear ? dateSpan.innerHTML = currentYear : null;

    if (!window.withMetric && window.withStage) {
        specificDataGraph('doughnut_replication_participants');
        if (associationSwitch.checked && currentYear) {
            if (selected && selected[0].label) {
                drawDoughnutAssociation(data['doughnut_associations'], currentYear, selected[0].label);
            } else {
                drawDoughnutAssociation(data['doughnut_associations'], currentYear, 'All');
            }
        }
    } else if (!window.withMetric && !window.withStage) {
        specificDataGraph('doughnut_discovery_participants');
        if (associationSwitch.checked && currentYear) {
            if (selected && selected[0].label) {
                drawDoughnutAssociation(data['doughnut_associations'], currentYear, selected[0].label);
            } else {
                drawDoughnutAssociation(data['doughnut_associations'], currentYear, 'All');
            }
        }
    } else if (window.withMetric && window.withStage) {
        specificDataGraph('doughnut_replication_studies');
        if (associationSwitch.checked && currentYear) {
            if (selected && selected[0].label) {
                drawDoughnutAssociation(data['doughnut_associations'], currentYear, selected[0].label);
            } else {
                drawDoughnutAssociation(data['doughnut_associations'], currentYear, 'All');
            }
        }
    } else if (window.withMetric && !window.withStage) {
        specificDataGraph('doughnut_discovery_studies');
        if (associationSwitch.checked && currentYear) {
            if (selected && selected[0].label) {
                drawDoughnutAssociation(data['doughnut_associations'], currentYear, selected[0].label);
            } else {
                drawDoughnutAssociation(data['doughnut_associations'], currentYear, 'All');
            }
        }
    }

    // Functions changing dates
    window.dgpreviousYear = function() {
        currentYear--;
        drawDgOnYearChange();
    };

    window.dgnextYear = function() {
        currentYear++;
        drawDgOnYearChange();
    };

    window.dgfirstYear = function() {
        currentYear = dataKeys[0];
        drawDgOnYearChange();
    };

    window.dglastestYear = function() {
        currentYear = dataKeys[dataKeys.length-1];
        drawDgOnYearChange();
    };

    $('#doughnutGraph').find(".filter select[name='parentTerms']").change(function(e) {
        selected = $(this).find('option:selected');
        var parentSVG = $("#doughnutSVG");

        $.each(parentSVG.attr("class").split(" "), function(index, value) {
            if(value.indexOf("term-") !== -1) {
                parentSVG.removeClass(value);
            }
        });

        parentSVG.addClass("term-" + selected.attr('value'));

        let parentTerm = selected[0].label;

        if (parentTerm === 'All parent terms') {
            parentTerm = 'All';
        }

        drawDoughnutPerYearPerAncestry(specificData, currentYear, parentTerm);

        if (associationSwitch.checked) {
            drawDoughnutAssociation(data['doughnut_associations'], currentYear, parentTerm);
        }
    });

	d3.select('#doughnut-graph-controls').on('click', function () {imagePopup('popup-download-image', selector, svg_id)});

    associationSwitch.addEventListener('change', function() {
        let associationLabel = document.getElementById('associationLabel');
        let arrowAsso = document.getElementById('associationArrow');
        let associationTitle = document.querySelector('.doughnut-graph-filter-association-title');
        let showHideLabel = document.querySelector('.doughnut-graph-filter-detail-associations span');
        let worldMap = document.getElementById('worldMap');
        let doughnutGraph = document.getElementById('doughnutGraph');

        associationSwitch.checked ? noDataSpan.classList.add('associations') : noDataSpan.classList.remove('associations');

        if(this.checked) {
            // Checkbox is checked.. ON
            associationTitle.classList.add('active');
            associationLabel.innerText = 'Hide';
            showHideLabel.innerText = 'hide';
            if (window.innerWidth <= 600) {
                arrowAsso.style.transform = 'translate(-50%, -50%) rotate(-90deg) scale(-1)';
            } else if(window.innerWidth > 600 && window.innerWidth < 1200) {
                arrowAsso.style.transform = 'translate(-50%, -50%) rotate(0deg) scale(1)';
            } else {
                arrowAsso.style.transform = 'translate(-50%, -50%) rotate(0deg) scale(-1)';
            }

            if (worldMap.parentElement.classList.contains('col-lg-6')) {
                worldMap.parentElement.classList.remove('col-lg-6');
                worldMap.parentElement.classList.add('col-lg-3');
            }

            if (doughnutGraph.parentElement.classList.contains('col-lg-3')) {
                doughnutGraph.parentElement.classList.remove('col-lg-3');
                doughnutGraph.parentElement.classList.add('col-lg-6');
            }

            if (doughnutGraph.parentElement.classList.contains('col-sm-6')) {
                doughnutGraph.parentElement.classList.remove('col-sm-6');
                doughnutGraph.parentElement.classList.add('col-sm-12');
            }

            if (window.innerWidth > 600 && window.innerWidth <= 1440) {
                legend.transition(200).attr('transform', 'translate('+(width-100)+', 0)');
            } else if (window.innerWidth <= 600 && window.innerWidth >= 375) {
                doughnutSVG.setAttribute('height', '620');
                doughnutSVG.style.maxHeight = '620px';
                legend.transition(200).attr('transform', 'translate(44, 20)');
                associationDoughnut.transition(200).style('transform', 'translate(135px, 495px)');
            } else if(window.innerWidth < 375) {
                doughnutSVG.setAttribute('height', '640');
                doughnutSVG.style.maxHeight = '640px';
                legend.transition(200).attr('transform', 'translate(44, 20)');
                associationDoughnut.transition(200).style('transform', 'translate(135px, 515px)');
            } else {
                if (!isIE()) {
                    legend.transition(200).attr('transform', 'translate('+(width-40)+', 0)');
                } else {
                    legend.attr('transform', 'translate(230, 0)');
                }
            }

            if (selected && selected[0].label) {
                if (selected[0].label === 'All parent terms') {
                    drawDoughnutAssociation(data['doughnut_associations'], currentYear, 'All');
                } else {
                    drawDoughnutAssociation(data['doughnut_associations'], currentYear, selected[0].label);
                }
            } else {
                drawDoughnutAssociation(data['doughnut_associations'], currentYear, 'All');
            }
        } else {
            // Checkbox is not checked.. OFF
            associationTitle.classList.remove('active');
            associationLabel.innerText = 'Show';
            showHideLabel.innerText = 'show';
            if (window.innerWidth <= 600) {
                arrowAsso.style.transform = 'translate(-50%, -50%) rotate(-90deg) scale(1)';
            } else if(window.innerWidth > 600 && window.innerWidth < 1200) {
                arrowAsso.style.transform = 'translate(-50%, -50%) rotate(0deg) scale(-1)';
            } else {
                arrowAsso.style.transform = 'translate(-50%, -50%) rotate(0deg) scale(1)';
            }

            if (worldMap.parentElement.classList.contains('col-lg-3')) {
                worldMap.parentElement.classList.remove('col-lg-3');
                worldMap.parentElement.classList.add('col-lg-6');
            }

            if (doughnutGraph.parentElement.classList.contains('col-lg-6')) {
                doughnutGraph.parentElement.classList.remove('col-lg-6');
                doughnutGraph.parentElement.classList.add('col-lg-3');
            }

            if (doughnutGraph.parentElement.classList.contains('col-sm-12')) {
                doughnutGraph.parentElement.classList.remove('col-sm-12');
                doughnutGraph.parentElement.classList.add('col-sm-6');
            }

            if (window.innerWidth > 600 && window.innerWidth <= 1440) {
                legend.transition(200).attr('transform', 'translate(0, 0)');
            } else if (window.innerWidth <= 600) {
                doughnutSVG.setAttribute('height', '324');
                doughnutSVG.style.maxHeight = '324px';
                legend.transition(200).attr('transform', 'translate(0, 0)');
                associationDoughnut.transition(200).style('transform', 'translate(540px, 108px)')
            } else {
                if (!isIE()) {
                    legend.transition(200).attr('transform', 'translate(0, 0)');
                } else {
                    legend.attr('transform', 'translate(0, 0)');
                }
            }

            tspanAssociation = document.querySelectorAll('.tspan-association');
            if (tspanAssociation && tspanAssociation.length > 0) {
                Array.prototype.forEach.call(tspanAssociation, function(el, i) {
                    if (i < 3) {
                        el.parentNode.setAttribute('x', '44');
                    } else if (i === 3) {
                        el.parentNode.setAttribute('x', '220');
                    } else if (i === 4) {
                        el.parentNode.setAttribute('x', '195');
                    } else {
                        el.parentNode.setAttribute('x', '151');
                    }
                });
            }

            d3.selectAll('.tspan-association').remove();
            d3.select('.doughnut-association-title').remove();

            if (rectAssociation && rectAssociation.length > 0) {
                Array.prototype.forEach.call(rectAssociation, function(el, i) {
                    if (i <= 2) {
                        el.setAttribute('x', '10');
                        el.setAttribute('y', (height/2+50)+(i*24 + i*12));
                    } else if(i > 2) {
                        el.setAttribute('x', 117);
                        el.setAttribute('y', (height/2-58)+(i*24 + i*12));
                    }
                });
            }

            if (textAssociation && textAssociation.length > 0) {
                Array.prototype.forEach.call(textAssociation, function(el, i) {
                    if (i <= 2) {
                        el.setAttribute('x', '44');
                        el.querySelector('tspan').setAttribute('x', '44');
                        el.setAttribute('y', (height/2+42)+(i*24 + i*12 + 18));
                    } else if(i > 2) {
                        el.setAttribute('x', 151);
                        el.querySelector('tspan').setAttribute('x', '151');
                        el.setAttribute('y', (height/2-58)+(i*24 + i*12 + 10));
                    }
                });
            }

            let doughnutPartsAssociations = document.querySelectorAll('.doughnutPartAssociation');
            if (doughnutPartsAssociations && doughnutPartsAssociations.length > 0) {
                Array.prototype.forEach.call(doughnutPartsAssociations, function(el) {
                    el.parentNode.removeChild(el);
                });
            }
        }
    });

    function drawDgOnYearChange() {
        if (selected && selected[0].label) {
            if (selected[0].label === 'All parent terms') {
                drawDoughnutPerYearPerAncestry(specificData, currentYear, 'All');
                if (associationSwitch.checked) {
                    drawDoughnutAssociation(data['doughnut_associations'], currentYear, 'All');
                }
            } else {
                drawDoughnutPerYearPerAncestry(specificData, currentYear, selected[0].label);
                if (associationSwitch.checked) {
                    drawDoughnutAssociation(data['doughnut_associations'], currentYear, selected[0].label);
                }
            }
        } else {
            drawDoughnutPerYearPerAncestry(specificData, currentYear, 'All');
            if (associationSwitch.checked) {
                drawDoughnutAssociation(data['doughnut_associations'], currentYear, 'All');
            }
        }
    }

    function drawDoughnutAssociation(dataByType, currentYear, parentTerm) {
        let val = dataByType[currentYear][parentTerm];
        // The radius of the pieplot is half the width or half the height (smallest one). I subtract a bit of margin.
        let radius = Math.min(width, height) / 3 - margin;
        // set the color scale
        let color = d3.scaleOrdinal()
            .domain(val)
            .range(["#ed7892", "#5fabdd", "#f59c44", "#fece59", "#c190c1", "#54bdbe"]);

// Compute the position of each group on the pie:
        let pie = d3.pie()
            .value(function(d) { return d.value.value; });
        let data_ready = pie(d3.entries(val));

        let doughnutPartsAssociations = document.querySelectorAll('.doughnutPartAssociation');
        if (doughnutPartsAssociations && doughnutPartsAssociations.length > 0) {
            for (let i=0; i < doughnutPartsAssociations.length; i++) {
                doughnutPartsAssociations[i].parentNode.removeChild(doughnutPartsAssociations[i]);
            }
        }

        // Build the pie chart: Basically, each part of the pie is a path that we build using the arc function.
        associationDoughnut.selectAll('.doughnutPartAssociation')
            .data(data_ready)
            .enter()
            .append('path')
            .attr('class', 'doughnutPartAssociation')
            .attr('d', d3.arc()
                .innerRadius(40)
                .outerRadius(radius)
            )
            .attr('fill', function(d) {
                return(color(d.data.key))
            })
            .on('mouseover', function (d) {
                d3Tooltip.transition()
                    .duration(200)
                    .style("opacity", 1);
                d3Tooltip.html("<div><p>"+d.data.value.ancestry+"</p><span>"+parseFloat(parseFloat(d.data.value.value).toFixed(2))+"%</span></div>")
                    .style("left", (d3.event.pageX) + "px")
                    .style("top", (d3.event.pageY - 58) + "px");
            })
            .on('mouseout', function () {
                d3Tooltip.transition()
                    .duration(500)
                    .style("opacity", 0);
            });

        if (window.innerWidth <= 600) {
            associationDoughnut.style('transform', 'translate(135px, 495px)')
        }

        d3.selectAll('.tspan-association').remove();

        d3.selectAll('.tspan-percentage')
            .append('tspan')
            .attr('class', 'tspan-association');

        tspanAssociation = document.querySelectorAll('.tspan-association');
        if (tspanAssociation && tspanAssociation.length > 0) {
            Array.prototype.forEach.call(tspanAssociation, function(el, i) {
                if (i < 3) {
                    el.parentNode.setAttribute('x', '-6');
                    if (window.innerWidth < 375 && associationSwitch.checked) {
                        el.setAttribute('x', '-9');
                        el.setAttribute('dy', 12);
                    }
                } else if (i === 3) {
                    el.parentNode.setAttribute('x', '220');
                    if (window.innerWidth < 375 && associationSwitch.checked) {
                        el.parentNode.setAttribute('x', '168');
                        el.setAttribute('x', '98');
                        el.setAttribute('dy', 12);
                    } else if (window.innerWidth < 400 && associationSwitch.checked) {
                        el.setAttribute('x', '149');
                        el.setAttribute('dy', 12);
                    }
                } else if (i === 4) {
                    el.parentNode.setAttribute('x', '195');
                    if (window.innerWidth < 375 && associationSwitch.checked) {
                        el.parentNode.setAttribute('x', '144');
                        el.setAttribute('x', '98');
                        el.setAttribute('dy', 12);
                    } else if (window.innerWidth < 400 && associationSwitch.checked) {
                        el.setAttribute('x', '149');
                        el.setAttribute('dy', 12);
                    }
                } else {
                     if (associationSwitch.checked && window.innerWidth < 375) {
                         el.parentNode.setAttribute('x', '101');
                     } else {
                         el.parentNode.setAttribute('x', '151');
                     }
                }
                if (data_ready && data_ready[i]) {
                    el.textContent = ' (Asso. '+parseFloat(parseFloat(data_ready[i].data.value.value).toFixed(2))+'%)';
                }
            });
        }

        rectAssociation = document.querySelectorAll('.doughnut-legend-rect');
        if (rectAssociation && rectAssociation.length > 0) {
            Array.prototype.forEach.call(rectAssociation, function(el, i) {
                if (i <= 2) {
                    el.setAttribute('x', '-40');
                    if (associationSwitch.checked && window.innerWidth < 375) {
                        el.setAttribute('y', (height/2+42)+(i*32 + i*12 + 10));
                    }
                } else if(i > 2) {
                    if (associationSwitch.checked && window.innerWidth < 375) {
                        el.setAttribute('x', 67);
                        el.setAttribute('y', (height/2-82)+(i*32 + i*12));
                    } else if(associationSwitch.checked && window.innerWidth < 400) {
                        el.setAttribute('x', 117);
                        el.setAttribute('y', (height/2-82)+(i*32 + i*12));
                    } else {
                        el.setAttribute('x', 117);
                        el.setAttribute('y', (height/2-58)+(i*24 + i*12));
                    }
                }
            });
        }

        textAssociation = document.querySelectorAll('.doughnut-legend-text');
        if (textAssociation && textAssociation.length > 0) {
            Array.prototype.forEach.call(textAssociation, function(el, i) {
                if (i <= 2) {
                    el.setAttribute('x', '-6');
                    el.querySelector('tspan').setAttribute('x', '-6');
                    if (associationSwitch.checked && window.innerWidth < 375) {
                        el.setAttribute('y', (height/2+42)+(i*32 + i*12 + 18));
                    } else {
                        el.setAttribute('y', (height/2+42)+(i*24 + i*12 + 18));
                    }
                } else if(i > 2) {
                    if (associationSwitch.checked && window.innerWidth < 375) {
                        el.setAttribute('x', 101);
                        el.querySelector('tspan').setAttribute('x', '101');
                        el.setAttribute('y', (height/2-55)+(i*32 + i*12 - 18));
                    } else if(associationSwitch.checked && window.innerWidth < 400) {
                        el.setAttribute('x', 151);
                        el.setAttribute('y', (height/2-55)+(i*32 + i*12 - 18));
                    } else {
                        el.setAttribute('x', 151);
                        el.setAttribute('y', (height/2-58)+(i*24 + i*12 + 10));
                    }
                }
            });
        }
    }


    function specificDataGraph(type) {
        specificData = data[type];
        dataKeys = Object.keys(specificData);
        currentYear = dataKeys[dataKeys.length-1];

        if (!selected) {
            let options = document.querySelectorAll(".filter select[name='parentTerms'] option");
            if (options && options.length > 0) {
                for (let i = 0, l = options.length; i < l; i++) {
                    options[i].selected = options[i].defaultSelected;
                }
            }
        }

        drawDoughnutPerYearPerAncestry(specificData, currentYear, 'All');
    }

    /**
     * @return {boolean}
     */
    function IsAllPropertiesNull(obj) {
        let values = Object.keys(obj).map(function(e) {
            return obj[e]
        });
        return values.every(function(v) { return v.value === ""; });
    }

    function drawDoughnutPerYearPerAncestry(dataByType, currentYear, parentTerm) {
        let val = dataByType[currentYear][parentTerm];

        noDataSpan = document.querySelector('.doughnut-graph-no-data');
        noDataSpan.innerText = '';

        associationSwitch.checked ? noDataSpan.classList.add('associations') : noDataSpan.classList.remove('associations');

        let isAllNull = IsAllPropertiesNull(val);
        if (isAllNull) {
            noDataSpan.innerText = 'No data found for '+parentTerm+' in '+currentYear;
        }

        // The radius of the pieplot is half the width or half the height (smallest one). I subtract a bit of margin.
        let radius = Math.min(width, height) / 3 - margin;

        // set the color scale
        let color = d3.scaleOrdinal()
            .domain(val)
            .range(["#ed7892", "#5fabdd", "#f59c44", "#fece59", "#c190c1", "#54bdbe"]);

        if (currentYear >= dataKeys[dataKeys.length-1]) {
            for (let i = 0; i < nextButtons.length; i++) {
                nextButtons[i].disabled = true;
            }
            for (let i = 0; i < previousButtons.length; i++) {
                previousButtons[i].disabled = false;
            }
        } else if (currentYear <= dataKeys[0]) {
            for (let i = 0; i < previousButtons.length; i++) {
                previousButtons[i].disabled = true;
            }
            for (let i = 0; i < nextButtons.length; i++) {
                nextButtons[i].disabled = false;
            }
        } else {
            for (let i = 0; i < previousButtons.length; i++) {
                previousButtons[i].disabled = false;
            }
            for (let i = 0; i < nextButtons.length; i++) {
                nextButtons[i].disabled = false;
            }
        }

        dateSpan.innerHTML = currentYear;

        // Compute the position of each group on the pie:
        let pie = d3.pie()
            .value(function(d) { return d.value.value; });
        let data_ready = pie(d3.entries(val));

        let doughnutParts = document.querySelectorAll('.doughnutPart');
        if (doughnutParts && doughnutParts.length > 0) {
            for (let i=0; i < doughnutParts.length; i++) {
                doughnutParts[i].parentNode.removeChild(doughnutParts[i]);
            }
        }

        // Build the pie chart: Basically, each part of the pie is a path that we build using the arc function.
        svg.selectAll('.doughnutPart')
            .data(data_ready)
            .enter()
            .append('path')
            .attr('class', 'doughnutPart')
            .attr('d', d3.arc()
                .innerRadius(40)
                .outerRadius(radius)
            )
            .attr('fill', function(d){
                return(color(d.data.key))
            })
            .on('mouseover', function (d) {
                d3Tooltip.transition()
                    .duration(200)
                    .style("opacity", 1);
                d3Tooltip.html("<div><p>"+d.data.value.ancestry+"</p><span>"+parseFloat(parseFloat(d.data.value.value).toFixed(2))+"%</span></div>")
                    .style("left", (d3.event.pageX) + "px")
                    .style("top", (d3.event.pageY - 58) + "px");
            })
            .on('mouseout', function () {
                d3Tooltip.transition()
                    .duration(500)
                    .style("opacity", 0);
            });

        d3.select('#doughnutSVG .doughnut-legend').remove();
        legend = d3.select('#doughnutSVG').append('g')
            .attr('class', 'doughnut-legend');

        legend.selectAll('.doughnut-legend-rect')
            .data(d3.entries(val))
            .enter()
            .append('rect')
            .attr('class', 'doughnut-legend-rect')
            .attr('width', 24)
            .attr('height', 24)
            .attr('x', function(d, i) {
                if (i < 3) {
                    return 10;
                } else {
                    return 117;
                }
            })
            .attr('y', function(d, i) {
                if (i < 3) {
                    return (height/2+50)+(i*24 + i*12);
                } else {
                    return (height/2+50)+((i-3)*24 + (i-3)*12);
                }
            })
            .attr('fill', function(d) {
                return(color(d.key))
            });

        legend.selectAll('.doughnut-legend-text')
            .data(d3.entries(val))
            .enter()
            .append('text')
            .attr('class', 'doughnut-legend-text')
            .attr('data-title', function(d) { return d.value.ancestry.split(' ').join('-').replace('/', '-').toLowerCase() })
            .attr('x', function(d, i) {
                if (i < 3) {
                    return 44;
                } else {
                    return 151;
                }
            })
            .attr('y', function(d, i) {
                if (i < 3) {
                    return (height/2+42)+(i*24 + i*12 + 18);
                } else {
                    return (height/2+42)+((i-3)*24 + (i-3)*12 + 18);
                }
            })
            .text(function(d) { return d.value.ancestry.split(' ').slice(0,3).join(' ') })
            .append('tspan').attr('x', function(d, i) {
                if (i < 3) {
                    return 44;
                } else {
                    return 151;
                }
            })
            .attr('dy', 12)
            .text(function(d) {
                if (d.value.ancestry === 'Hispanic or Latin American' || d.value.ancestry === 'African American or Afro-Caribbean') {
                    return d.value.ancestry.split(' ').slice(3,4).join(' ');
                }
            })
            .append('tspan').attr('class', 'tspan-percentage').attr('x', function(d, i) {
                if (i < 3) {
                    return 44;
                } else {
                    if(d.value.ancestry === 'Hispanic or Latin American') {
                        return 194;
                    } else if(d.value.ancestry === 'African American or Afro-Caribbean') {
                        return 219;
                    } else {
                        return 151;
                    }
                }
            })
            .attr('dy', function(d) {
                if(d.value.ancestry === 'Hispanic or Latin American' || d.value.ancestry === 'African American or Afro-Caribbean') {
                    return 0;
                } else {
                    return 14;
                }
            })
            .attr('font-weight', 'normal')
            .text(function(d) {
                if (d && d.value && d.value.value) {
                    return parseFloat(parseFloat(d.value.value).toFixed(2))+'%';
                } else {
                    return '';
                }
            });

        if (associationSwitch.checked) {
            if (window.innerWidth > 600 && window.innerWidth <= 1440) {
                legend.attr('transform', 'translate(' + (width - 100) + ', 0)');
            } else if (window.innerWidth <= 600){
                doughnutSVG.setAttribute('height', '600');
                legend.attr('transform', 'translate(44, 20)');
            } else {
                if (!isIE()) {
                    legend.attr('transform', 'translate('+(width-40)+', 0)');
                } else {
                    legend.attr('transform', 'translate(230, 0)');
                }
            }
        }
    }
}