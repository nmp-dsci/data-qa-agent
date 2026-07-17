function draw_stackedbar(chartContext,tagID,cCol ,chartDim) {

    // ####################################################################################
    // Sort by x1 And x2 

    if (chartContext[`${tagID}_${cCol}`].cColDF.order){
      chartContext[`${tagID}_${cCol}`]['chartData'].sort((a , b) => d3.ascending(chartContext[`${tagID}_${cCol}`].cColDF['order'].indexOf(
        a[chartContext[`${tagID}_${cCol}`].cColRef_v])
        , chartContext[`${tagID}_${cCol}`].cColDF['order'].indexOf(b[chartContext[`${tagID}_${cCol}`].cColRef_v])))
    } else {
      chartContext[`${tagID}_${cCol}`]['chartData'].sort((a , b) => d3.ascending(a[chartContext[`${tagID}_${cCol}`].cColRef_v], b[chartContext[`${tagID}_${cCol}`].cColRef_v]))
    }

    if (chartContext[tagID].xCol1DF.order){
      chartContext[`${tagID}_${cCol}`]['chartData'].sort((a , b) => d3.ascending(chartContext[tagID].xCol1DF['order'].indexOf(
        a[chartContext[`${tagID}_${cCol}`].xCol1Ref_v])
        , chartContext[tagID].xCol1DF['order'].indexOf(b[chartContext[`${tagID}_${cCol}`].xCol1Ref_v])))
    } else {
      chartContext[`${tagID}_${cCol}`]['chartData'].sort((a , b) => d3.ascending(a[chartContext[`${tagID}_${cCol}`].xCol1Ref_v], b[chartContext[`${tagID}_${cCol}`].xCol1Ref_v]))
    }

    // ####################################################################################
    // Get unique variable, apply filter if exists
    if (chartContext[`${tagID}_${cCol}`]['uniqcColsAdj'].length !== chartContext[`${tagID}_${cCol}`].cColDF['order'].length & chartContext[`${tagID}_${cCol}`].cColDF['order'].length > 0){
      chartContext[`${tagID}_${cCol}`]['chartData'] = chartContext[`${tagID}_${cCol}`]['chartData'].filter(f=>  chartContext[`${tagID}_${cCol}`]['uniqcColsAdj'].includes(f[chartContext[`${tagID}_${cCol}`].cColRef_v]))
    }

    var xScale = d3.scaleBand()
      .rangeRound([0,chartDim['width']])
      .padding(0.1)
      .domain(chartContext[tagID].xCol1DF['order']);

    total_x1  = summFunc(chartContext[`${tagID}_${cCol}`]['chartData'],[chartContext[`${tagID}_${cCol}`].xCol1Ref_v]);

    var yScaleBar = d3.scaleLinear()
      .range([chartDim['height'],0])
      .domain([0,d3.max(total_x1.map(d => d[chartContext[tagID].yCol['n']]))]);
        
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
      .style("font-size", "26px");

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
        .call(d3.axisLeft(yScaleBar).tickFormat(d3.format(chartContext[tagID].yColDF[chartContext[tagID].level].format)))
        .selectAll("text")
        .style("font-size", "17px");

    // ###################################################
    //  Draw Heat map
    svg_chart.selectAll("path.segment").remove()
    svg_chart.selectAll("path.barCol").remove()
    svg_chart.selectAll("rect.tile").remove()
    svg_chart.selectAll("text.tile").remove()      

    // Stacked bar chart data preparation 
    // step 1 : overall cumsum 
    cumsumRaw = d3.cumsum(chartContext[`${tagID}_${cCol}`]['chartData'].map(f=> f[chartContext[tagID].yCol['n']]));
    chartContext[`${tagID}_${cCol}`]['chartData'].map((m,i) => Object.assign(m,{'cumsum_raw':cumsumRaw[i]}));
    cumsum_idx = chartContext[tagID].xCol1DF['order'].map(r=> chartContext[`${tagID}_${cCol}`]['chartData'].filter(f=> f[chartContext[`${tagID}_${cCol}`].xCol1Ref_v] === r )[0]  )
    cumsum_idx.map((m,i) => Object.assign(m,{'cumsum_idx':m['cumsum_raw'] - m[chartContext[tagID].yCol['n']]}));
    chartContext[`${tagID}_${cCol}`]['chartData'].map((m,i) => Object.assign(m,{'cumsum_x1': m['cumsum_raw'] - cumsum_idx.filter(f=>f[chartContext[`${tagID}_${cCol}`].xCol1Ref_v]===m[chartContext[`${tagID}_${cCol}`].xCol1Ref_v])[0]['cumsum_idx']  }));

    // Visulatse 
    svg_chart
      .selectAll("rect.stackedBar")
      .data(chartContext[`${tagID}_${cCol}`]['chartData'])
      .join("rect")
        .attr("class", "stackedBar")
        .attr("width", xScale.bandwidth() )
        .attr("x", d => xScale(d[chartContext[`${tagID}_${cCol}`].xCol1Ref_v]) )
        .attr("y", d => yScaleBar(d['cumsum_x1'])  )// summ of: lag_cumsum + incremental
        .attr("height", d => yScaleBar(d['cumsum_x1'] - d[chartContext[tagID].yCol['n']]) -  yScaleBar( d['cumsum_x1']) ) // lag_cumsum - (lag_cumsum + incremental)
        .style("fill", d=> chartContext[`${tagID}_${cCol}`].legendValues.filter( f=>  f.value === d[chartContext[`${tagID}_${cCol}`].cColRef_v])[0]['c']  )
        .attr("fill-opacity",d=> 1);

} // draw Line