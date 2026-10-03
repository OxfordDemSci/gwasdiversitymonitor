/* One keyboard stop per SVG chart; full records remain available in View data. */
(function(root, factory) {
    'use strict';
    if (typeof module === 'object' && module.exports) module.exports = factory;
    else root.gwasChartAccessibility = factory(root);
})(typeof window !== 'undefined' ? window : this, function(browser) {
    'use strict';
    const document = browser.document;
    let tooltip = null;
    let inspectedNode = null;

    function hideTooltip() {
        if (tooltip) tooltip.hidden = true;
        inspectedNode = null;
    }

    function showTooltip(node, label) {
        if (!tooltip) {
            tooltip = document.createElement('div');
            tooltip.id = 'gwas-chart-tooltip';
            tooltip.className = 'gwas-chart-tooltip';
            tooltip.setAttribute('role', 'tooltip');
            document.body.appendChild(tooltip);
        }
        document.querySelectorAll('.d3-tooltip, .d3-tip').forEach(element => {
            element.style.opacity = '0';
        });
        tooltip.textContent = label;
        tooltip.hidden = false;
        inspectedNode = node;
        const bounds = node.getBoundingClientRect();
        const tooltipBounds = tooltip.getBoundingClientRect();
        const left = Math.max(8, Math.min(browser.innerWidth - tooltipBounds.width - 8,
            bounds.left + bounds.width / 2 - tooltipBounds.width / 2));
        const preferredTop = bounds.top > tooltipBounds.height + 12
            ? bounds.top - tooltipBounds.height - 8 : bounds.bottom + 8;
        const top = Math.max(8, Math.min(browser.innerHeight - tooltipBounds.height - 8, preferredTop));
        tooltip.style.left = left + 'px';
        tooltip.style.top = top + 'px';
    }

    function enhanceMarks(chartId, selection, labelFor) {
        const nodes = selection.nodes();
        const chart = document.getElementById(chartId);
        if (!chart || !nodes.length) return;
        let help = document.getElementById(chartId + '-keyboard-help');
        if (!help) {
            help = document.createElement('p');
            help.id = chartId + '-keyboard-help';
            help.className = 'gwas-sr-only';
            help.textContent = 'Use arrow keys to move between chart marks, Home or End for the first or last, '
                + 'and Enter or Space to inspect. Tap a mark to inspect it. Escape dismisses its tooltip. '
                + 'View data opens a table of every chart value.';
            chart.appendChild(help);
        }
        const previousIndex = nodes.indexOf(document.activeElement);
        const initialIndex = previousIndex < 0 ? 0 : previousIndex;
        nodes.forEach((node, index) => {
            node.setAttribute('tabindex', index === initialIndex ? '0' : '-1');
            node.setAttribute('role', 'button');
            node.setAttribute('aria-label', labelFor(node.__data__, node));
            node.setAttribute('aria-describedby', help.id);
            node.classList.add('gwas-interactive-mark');
        });
        selection.on('focus.chartAccessibility', function(datum) {
            nodes.forEach(node => node.setAttribute('tabindex', node === this ? '0' : '-1'));
            showTooltip(this, labelFor(datum, this));
        }).on('blur.chartAccessibility', hideTooltip)
            .on('click.chartAccessibility', function(datum) {
                showTooltip(this, labelFor(datum, this));
            }).on('keydown.chartAccessibility', function(datum) {
                const event = browser.d3.event;
                if (event.altKey || event.ctrlKey || event.metaKey) return;
                const currentIndex = nodes.indexOf(this);
                let nextIndex;
                if (event.key === 'ArrowRight' || event.key === 'ArrowDown') nextIndex = (currentIndex + 1) % nodes.length;
                else if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') nextIndex = (currentIndex + nodes.length - 1) % nodes.length;
                else if (event.key === 'Home') nextIndex = 0;
                else if (event.key === 'End') nextIndex = nodes.length - 1;
                else if (event.key === 'Enter' || event.key === ' ') {
                    event.preventDefault();
                    showTooltip(this, labelFor(datum, this));
                    return;
                } else if (event.key === 'Escape') {
                    event.preventDefault();
                    hideTooltip();
                    return;
                } else return;
                event.preventDefault();
                nodes.forEach((node, index) => node.setAttribute('tabindex', index === nextIndex ? '0' : '-1'));
                nodes[nextIndex].focus();
            });
    }

    document.addEventListener('pointerdown', event => {
        if (event.target !== inspectedNode && event.target !== tooltip) hideTooltip();
    });
    document.addEventListener('keydown', event => { if (event.key === 'Escape') hideTooltip(); });
    browser.addEventListener('gwas:chartdatachanged', hideTooltip);
    return {enhanceMarks: enhanceMarks, hideTooltip: hideTooltip};
});
