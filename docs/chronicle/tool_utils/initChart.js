function initChart(chartContext,tagID, cCol ,chartDim){


    d3.select(`#${chartContext[tagID].tagID}`)
      .select("#column1")
      .append('div')
      .attr("id", `${chartContext[tagID].tagID}_${chartContext[`${[tagID]}_${cCol}`].cCol}`)
      .attr("style","border:1px solid black")

    var svg = d3.selectAll(`#${chartContext[tagID].tagID}_${chartContext[`${[tagID]}_${cCol}`].cCol}`)
      .append("svg")
        .attr("width", chartDim.svgWidth)
        .attr("height", chartDim.svgHeight)
        .attr("id", `${chartContext[tagID].tagID}_${chartContext[`${[tagID]}_${cCol}`].cCol}_svg`)
      .append("g")
        .attr("transform", "translate("+chartDim.left+","+chartDim.top+")")
        .attr("class", `chart_${chartContext[tagID].tagID}_${chartContext[`${[tagID]}_${cCol}`].cCol}`);

    svg.append("text")
      // .attr("y", line1_p.top / 2)
      // .attr("x", (line1_p.width_b + line1_p.left)/2 )
      .attr("y",-1 * (chartDim.top/1.5 ))
      .attr("x",10)
      .style("font", "20px sans-serif")
      .attr("class","plot_title")

    // add X/Y axis
    svg.append("g")
      .attr("class", "x_axis" )
      .attr("transform", "translate(0, " + chartDim.height + ")" )

    svg.append("g")
      .attr("class", "y_axis")

    svg.append('text')
      .attr("class","yAxis_name")
      .attr("transform", "rotate(-90)")
      .attr("y",  -chartDim.left + 20)
      .attr("x",10 - (chartDim.height /2 ));
      // .style("text-anchor", "middle")

    // LEGEND
    svg.append("g")
      .attr("class","legend")
      .attr("height", 100)
      .attr("width", 300)
      .attr("transform",  `translate(${-20},${0 - chartDim.top})`);

}// initChart end