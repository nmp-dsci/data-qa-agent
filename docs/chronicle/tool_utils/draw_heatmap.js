function draw_heatmap(chartContext,tagID,cCol,chartDim) {

  // Sort so xCol1 is good
  if (chartContext[tagID].xCol1DF.order){
    chartContext[`${tagID}_${cCol}`]['chartData'].sort((a , b) => d3.ascending(chartContext[tagID].xCol1DF['order'].indexOf(a[chartContext[`${tagID}_${cCol}`].xCol1Ref_v]), chartContext[tagID].xCol1DF['order'].indexOf(b[chartContext[`${tagID}_${cCol}`].xCol1Ref_v])))
  } else {
    chartContext[`${tagID}_${cCol}`]['chartData'].sort((a , b) => d3.ascending(a[chartContext[`${tagID}_${cCol}`].xCol1Ref_v], b[chartContext[`${tagID}_${cCol}`].xCol1Ref_v]))
  }

  // ####################################################################################
  // Get unique variable, apply filter if exists
  if (chartContext[`${tagID}_${cCol}`]['uniqcColsAdj'].length !== chartContext[`${tagID}_${cCol}`].cColDF['order'].length ){
    chartContext[`${tagID}_${cCol}`]['chartData'] = chartContext[`${tagID}_${cCol}`]['chartData'].filter(f=>  chartContext[`${tagID}_${cCol}`]['uniqcColsAdj'].includes(f[chartContext[`${tagID}_${cCol}`].cColRef_v]))
  }

  // ###############################
  var xScale = d3.scaleBand()
    .rangeRound([0,chartDim['width']])
    .padding(0.1)
    .domain(chartContext[tagID].xCol1DF['order']);

  var yScale = d3.scaleBand()
    .rangeRound([chartDim['height'],0])
    .padding(0.1)
    .domain(chartContext[`${tagID}_${cCol}`].cColDF['order']);

  var cScale = d3.scaleLinear()
    .domain(d3.extent(chartContext[`${tagID}_${cCol}`]['chartData'].map(r=>r[chartContext[tagID].yCol['n']])))
    .range(["red", "yellow", "green"]);

  // ###########################################
  // CHART processing
  svg_chart = d3.select(`#${tagID}_${cCol}`)
      .select(`.chart_${tagID}_${cCol}`)

  svg_chart.select(".plot_title")
    .text(`'${chartContext[`${tagID}`].yColDF['n'].name}' by '${chartContext[`${tagID}_${cCol}`].cColDF.name}' & '${chartContext[tagID].xCol1DF.name}'` )
    .style("font-size", "26px");

  // X-axis
  svg_chart
    .selectAll(".x_axis")
    .transition()
    .duration(300)
    .call(d3.axisBottom(xScale)) 
    .selectAll("text")
    // .style("font-weight", "bold")
    .style("font-size", "17px")
    .attr("transform", `translate(-10,${0}) rotate(${0})`)
    // .attr("transform", `translate(-10,${xColBuffer===true ? (xCol_buffer/2)  :0 }) rotate(${xColBuffer ===true ? -45 :0 })`)

  // yaxis
  svg_chart
      .selectAll(".y_axis")
      .transition()
      .duration(300)
      .call(d3.axisLeft(yScale)) 
      .selectAll("text")
      .style("font-size", "17px");

  // ###################################################
  //  Draw Heat map

  svg_chart.selectAll("path.segment").remove();
  svg_chart.selectAll("rect.stackedBar").remove();

  svg_chart
    .selectAll("rect.tile")
    .data(chartContext[`${tagID}_${cCol}`]['chartData'])
    .join("rect")
      .attr("class", "tile")
      .attr("x", d => xScale(d[chartContext[`${tagID}_${cCol}`].xCol1Ref_v])  )
      .attr("y", d => yScale(d[chartContext[`${tagID}_${cCol}`].cColRef_v]) )
      .attr("rx",4)
      .attr("ry",4)
      .attr("width", xScale.bandwidth() )
      .attr("height", yScale.bandwidth() )
      .style("fill", d => cScale(d[chartContext[tagID].yColDF[chartContext[tagID].level]['column']]))

  svg_chart
    .selectAll("text.tile")
    .data(chartContext[`${tagID}_${cCol}`]['chartData'])
    .join("text")
      .attr("class", "tile")
      .attr("x", d => xScale(d[chartContext[`${tagID}_${cCol}`].xCol1Ref_v]) + xScale.bandwidth()/4 )
      .attr("y", d => yScale(d[chartContext[`${tagID}_${cCol}`].cColRef_v]) + yScale.bandwidth()/2 )
      .attr("dy", ".35em")
      .text(d=> d3.format(chartContext[tagID].yColDF[chartContext[tagID].level]['format'])(d[chartContext[tagID].yColDF[chartContext[tagID].level]['column']]))
        .style("font-weight", "bold")

} // draw Line