


function drawChartLegend_load(chartContext,tagID,cCol){

    // ##########################################################
    //  draw legend

    legendG = d3.select(`.chart_${tagID}_${cCol}`)
            .select("g.legend");

        // UPDATE RECT
    var legend_rect = legendG
        .selectAll("rect")
        .data(chartContext[`${tagID}_${cCol}`]['legendValues'], function(d) {return d });

    legend_rect
        .attr("id","old");
    
    legend_rect.enter()
        .append("rect")
        .attr("class","legend_rect")
        .attr("id","new")
        .attr("x", d => d.legX_box )
        .attr("y", d => d.legY_box )
        .attr("width", 16)
        .attr("height", 16)
    .merge(legend_rect)
        .style("fill", function(d) { return d.c })
  
      legend_rect.exit().remove() ;
  
    // UPDATE TEXT
    var legend_txt = legendG
      .selectAll("text")
      .data(chartContext[`${tagID}_${cCol}`]['legendValues'], function(d) {  return d; });
  
    legend_txt
      .attr("id","old");
  
    legend_txt.enter()
        .append("text")
        .attr("class","legend_text")
        .attr("id","new")
        .attr("x", d => d.legX_text)
        .attr("y", d => d.legY_text)
      .merge(legend_txt)
        .text( d => d.text ? d.text : d.value)
          .style("font-weight", "bold")
        .style("font-size", "18px");
    
    
    legend_txt.exit().remove() ;

}
