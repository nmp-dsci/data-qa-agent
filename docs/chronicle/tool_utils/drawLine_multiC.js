function drawLine_multiC(chartContext,tagID,cCol,chartDim) {


  chartContext[`${tagID}_${cCol}`]['chartData'] = chartContext[`${tagID}_${cCol}`]['chartData'].filter(f => f[chartContext[tagID].yCol['n']] !== 0 )

  // ####################################################################################
  // Sort by x1 And x2 
  if (chartContext[tagID].xCol1DF.order){
    chartContext[`${tagID}_${cCol}`]['chartData'].sort((a , b) => d3.ascending(chartContext[tagID].xCol1DF['order'].indexOf(a[chartContext[`${tagID}_${cCol}`].xCol1Ref.replace("_c","_v")]),
    chartContext[tagID].xCol1DF['order'].indexOf(b[chartContext[`${tagID}_${cCol}`].xCol1Ref.replace("_c","_v")])))
  } else {
    chartContext[`${tagID}_${cCol}`]['chartData'].sort((a , b) => d3.ascending(a[chartContext[`${tagID}_${cCol}`].xCol1Ref.replace("_c","_v")], b[chartContext[`${tagID}_${cCol}`].xCol1Ref.replace("_c","_v")]))
  }
  

  // ###############################

  var xScale = d3.scaleBand()
    .rangeRound([0,chartDim['width']])
    .padding(0.1)
    .domain(chartContext[tagID].xCol1DF['order']);

  var yScale = d3.scaleLinear()
    .domain(d3.extent(chartContext[`${tagID}_${cCol}`]['chartData'].map(d => d[chartContext[tagID].yCol['n']])))
    .range([chartDim['height'],0]);

  // ###########################################
  // CHART processing
  svg_chart = d3.select(`#${chartContext[tagID].tagID}_${chartContext[`${tagID}_${cCol}`].cCol}`)
      .select(`.chart_${chartContext[tagID].tagID}_${chartContext[`${tagID}_${cCol}`].cCol}`)

  svg_chart.select(".yAxis_name")
    .text(chartContext[tagID].yColDF[chartContext[tagID].level].name)
    .style("font-weight", "bold")
    .style("font-size", "20px");

  svg_chart.select(".plot_title")
    .text(`'${chartContext[`${tagID}`].yColDF['n'].name}' by '${chartContext[`${tagID}_${cCol}`].cColDF.name}' & '${chartContext[tagID].xCol1DF.name}'` )
    .style("font-size", "26px")

  // X-axis
  svg_chart
    .selectAll(".x_axis")
    .transition()
    .duration(300)
    .call(d3.axisBottom(xScale).tickFormat((interval,i) => {
        return i % xAxisLabelText_gap !== 0 ? " ": interval;
      })) 
    .selectAll("text")
    // .style("font-weight", "bold")
    .style("font-size", "15px")
    .attr("transform", (d,i) =>  `translate(-10,${i%2 == 0 ? 15:0 }) rotate(${0 })`)

  // yaxis
  svg_chart
      .selectAll(".y_axis")
      .transition()
      .duration(300)
      .call(d3.axisLeft(yScale).tickFormat(d3.format(chartContext[tagID].yColDF[chartContext[tagID].level].format)))
      .selectAll("text")
      .style("font-size", "17px");

  // ###################################################
  //  DRAW LINE

  svg_chart.selectAll("path.segment").remove()
  svg_chart.selectAll("path.barCol").remove()
  svg_chart.selectAll("rect.tile").remove()
  svg_chart.selectAll("text.tile").remove()
  svg_chart.selectAll("rect.stackedBar").remove()


  if (chartContext[tagID].xCol1 !== chartContext[`${tagID}_${cCol}`].cCol){
    var cCol_paths = d3.group(chartContext[`${tagID}_${cCol}`]['chartData'], d => d[chartContext[`${tagID}_${cCol}`].cColRef_v]);
  } else {
    var cCol_paths = d3.group(chartContext[`${tagID}_${cCol}`]['chartData'], d => d['cutid']);
  }

  var path = d3.line()
    .x(function(d) { return xScale(d[chartContext[`${tagID}_${cCol}`].xCol1Ref_v]) + 20 ; })
    .y(function(d) { return yScale(d[chartContext[tagID].yCol['n']]); });

  svg_chart
    .selectAll("path.segment")
    .data(cCol_paths)
    .join('path')
      .attr("class","segment")
      .attr("stroke", "black")
      .attr("fill", "none")
      .attr("d",function(d) { 
        return path(d[1]); 
      })
  //   .attr("class", d => colDF.filter(r=>r.column===d.key)[0].stroke)
    .attr("stroke-width", d => {
      path_df = chartContext[`${tagID}_${cCol}`].legendValues.filter(r=> r.value === d[0] )
      return  path_df.length > 0 ? path_df[0].w : 6
    })
    .attr("stroke-opacity", d => {
      path_df = chartContext[`${tagID}_${cCol}`].legendValues.filter(r=> r.value === d[0] )
      return  path_df.length > 0 ? path_df[0].o : 0.7
    })
    .attr("stroke", d => {
      path_df = chartContext[`${tagID}_${cCol}`].legendValues.filter(r=> r.value === d[0] )
      return  path_df.length > 0 ? path_df[0].c : d3.schemeCategory10[0]; 
    });

} // draw Line