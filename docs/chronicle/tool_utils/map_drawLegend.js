// -------------------------------------------------------
function drawLegend(buckets,response=$("#target #profile").val()){

    // pull response info
    responseDF = attrCols.filter(r=> r.column === response)[0]

    var map_p = {width_b:900,height_b:600,legend_w:400,legend_h:30,legendbar_h:15};      
    var legendX = d3.scaleLinear().range([0, map_p.legend_w]);
    var t = d3.transition().duration(750);

    console.log('buckets')
    console.log(buckets)

    // update legendX domain
    legendX.domain([buckets[0].x, buckets[buckets.length - 1].x + buckets[buckets.length - 1].width]);

    svg_legend = d3.select('#legend #svg')

    // JOIN
    var legendRect = svg_legend.selectAll(".legend-rect")
        .data(buckets, function(d){ return d.bucket; });

    var legendNumber = svg_legend.selectAll(".legend-number")
        .data(buckets, function(d){ return d.bucket; });

    var legendMax = svg_legend.selectAll(".legend-max")
    .data([buckets[buckets.length - 1].x + buckets[buckets.length - 1].width]);

    // EXIT
    legendRect.exit()
    .transition(t)
        .attr("opacity", 1e-6)
        .remove();

    legendNumber.exit()
    .transition(t)
        .attr("opacity", 1e-6)
        .remove();

    // UPDATE
    legendRect
    .transition(t)
        .attr("width", function(d){ return legendX(d.x + d.width); })
        .attr("x", function(d){ return legendX(d.x); })
        .attr("fill", function(d){ return d.color });

    legendNumber
    .transition(t)
        .attr("x", function(d){ return legendX(d.x); })
        .text(function(d){ return d3.format(responseDF['format'])(d.x); });

    legendMax
        .attr("x", function(d){  return legendX(d); })
        .text(function(d){ return d3.format(responseDF['format'])(d); })

    // ENTER
    legendRect.enter().append("rect")
        .attr("class", "legend-rect")
        .attr("y", 0)
        .attr("height", map_p.legendbar_h)
        .attr("x", function(d){ return legendX(d.x); })
        .attr("fill", function(d){ return d.color })
        .attr("width", function(d){ return legendX(d.x + d.width); });

    legendNumber.enter().append("text")
        .attr("class", "legend-number")
        .attr("y", map_p.legend_h)
        .attr("x", function(d){ return legendX(d.x); })
        .text(function(d){ return d3.format(responseDF['format'])(d.x); });
        
    legendMax.enter().append("text")
        .attr("class", "legend-max")
        .attr("y", map_p.legend_h)
        .attr("x", function(d){ return legendX(d); })
        .style("text-anchor", "end")
        .text(function(d){return d3.format(responseDF['format'])(d); });

} // drawLegend
  