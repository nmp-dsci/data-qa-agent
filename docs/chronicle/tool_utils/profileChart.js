// -------------------------------------------------------
//  function 'profileChart'
function profileChart(chartContext,tagID,cCol,widthRaw=500) {
  
  // Change map dimensions is large amount of xCols
  sumStrLen = d3.sum(chartContext[`${tagID}_${cCol}`]['cColUniqList'].map(r=>String(r[0]).length),d=>d)
  maxStrLen = d3.max(chartContext[`${tagID}_${cCol}`]['cColUniqList'].map(r=>String(r[0]).length),d=>d)
  xColBuffer = chartContext[`${tagID}_${cCol}`]['cColUniqList'].length > 7 || maxStrLen > 13 || sumStrLen > 30; 
  xCol_buffer = xColBuffer  === true ? maxStrLen * 4 : 20

  //##########################################
  // Chart preparation
  var margin = {top: 100, right: 20, bottom: 20 + xCol_buffer, left: 90};
  var width = widthRaw - margin.left - margin.right,
    height = 450 - margin.top - margin.bottom;

  var xScale = d3.scaleBand().padding([.1]).rangeRound([0,width]);


  // Load axis's
  // this for top states 
  if (chartContext[`${tagID}_${cCol}`]['cColUniq'][0].value.length >= 10   ) { 
    xValueDF =  chartContext[`${tagID}_${cCol}`]['chartData'].filter(d=> d[chartContext[tagID]['profileCol']] ==='n')
    xValueDF = xValueDF.sort((a,b)=> b.upliftCol - a.upliftCol )
    xScale.domain( xValueDF.map(d=> d['xCol_v']) );
  } else {
    xScale.domain( chartContext[`${tagID}_${cCol}`]['cColUniq'][0].value );
  }
  
  yMin = d3.min(chartContext[`${tagID}_${cCol}`]['chartData'].map(d => d.barCol));
  yMax = d3.max(chartContext[`${tagID}_${cCol}`]['chartData'].map(d => d.barCol));
  yMinDefault = yMin > 0 ? 0: yMin;
  yMaxDefault = yMax < 0 ? 0: yMax;  

  var yScale = d3.scaleLinear()
    .range([height, 0  ])
    .nice();

  var colorBand = d3.scaleBand()
    .domain(chartContext[tagID]['profileValues'] )
    .rangeRound([0, xScale.bandwidth() ]);


  // CHART processing
  svg_chart = d3.selectAll(`#profile1_${chartContext[`${tagID}_${cCol}`]['cCol']}`)
    .select(`.chart_profile1_${chartContext[`${tagID}_${cCol}`]['cCol']}`)
    .attr("height", height );

  svg_chart.select(".yAxis_name")
    .text(chartContext[tagID].yType.yTitle)
    .style("font-weight", "bold")
    .style("font-size", "20px");

  svg_chart.select(".plot_title")
    .text(chartContext[`${tagID}_${cCol}`]['cColDF'].name)
    .style("font-size", "26px");

  // X-axis
  svg_chart
    .selectAll(".x_axis")
    .transition()
    .duration(300)
    .call(d3.axisBottom(xScale)) 
    .attr("transform", `translate(0,${height }) rotate(${0 })`)
    .selectAll("text")
    .style("font-weight", "bold")
    .style("font-size", "16px")
    .style("font-color", "rgba(13, 47, 244)")
    // .attr("text-anchor", "start")
    .attr("transform", `translate(-10,${xColBuffer===true ? (xCol_buffer/2)  :0 }) rotate(${xColBuffer ===true ? -45 :0 })`)


  // yaxis - min/ max

  if (['_r_v','_a_v'].includes(chartContext[tagID].yCol['n'].slice(-4))){
    yScale.domain([ yMin ,yMax]);
  } else {
    yScale.domain([ yMinDefault ,yMaxDefault]);
  }

  svg_chart
    .selectAll(".y_axis")
    .transition()
    .duration(300)
    .call(d3.axisLeft(yScale).tickFormat( chartContext[tagID].yType.format))
    .style("font-weight", "bold")
    .style("font-size", "16px")
    .style("font-color", "rgba(13, 47, 244)")

  if (['_r_v','_a_v'].includes(chartContext[tagID].yCol['n'].slice(-4))){

    svg_chart.selectAll("rect.barCol").remove()

    // ###################################################
    //  DRAW LINE
    chartContext[`${tagID}_${cCol}`]['chartData'].map(r=>r['xAxisOrder'] = xScale(r[chartContext[`${tagID}_${cCol}`]['cColRef_v']])); 
    chartContext[`${tagID}_${cCol}`]['chartData'].sort((a,b)=> b.xAxisOrder - a.xAxisOrder )

    var cCol_paths = d3.group(chartContext[`${tagID}_${cCol}`]['chartData'], d => d['profile']);

    // Order 'cCol_paths' contents so it aligns
    var path = d3.line()
      .x(function(d) { return xScale(d[chartContext[`${tagID}_${cCol}`]['cColRef_v']]) + 20 ; })
      .y(function(d) { return yScale(d.barCol); });

    //  Update , Exist , Enter
    svg_chart
      .selectAll("path.barCol")
      .data(cCol_paths)
      .join('path')
        .attr("class","barCol")
        .attr("stroke", "black")
        .attr("fill", "none")
        .attr("d",function(d) { 
          return path(d[1]); 
        })
    //   .attr("class", d => colDF.filter(r=>r.column===d.key)[0].stroke)
      .attr("stroke-width", d => {
        path_df = chartContext[`${tagID}_${cCol}`]['legendValues'].filter(r=> r.value === d[0] )
        return  path_df.length > 0 ? path_df[0].w : 6
      })
      .attr("stroke-opacity", d => {
        path_df = chartContext[`${tagID}_${cCol}`]['legendValues'].filter(r=> r.value === d[0] )
        return  path_df.length > 0 ? path_df[0].o : 0.7
      })
      .attr("stroke", d => {
        path_df = chartContext[`${tagID}_${cCol}`]['legendValues'].filter(r=> r.value === d[0] )
        return  path_df.length > 0 ? path_df[0].c : d3.schemeCategory10[0]; 
      });
  } else {   
    //$##############################
    // 1.DRAW uplifts on BARS 
    svg_chart
      .selectAll("path.barCol")
      .remove()

    svg_chart
      .selectAll("rect.barCol")
      .data(chartContext[`${tagID}_${cCol}`]['chartData'])
      .join("rect")
        .attr("class", "barCol")
        .attr("width", colorBand.bandwidth() )
        .attr("x", d => xScale(d['xCol_v']) + colorBand(d[chartContext[tagID]['profileCol']]) )
        .attr("y", d => d.barCol > 0 ? yScale(d.barCol) :yScale(0) )
        .attr("height", d => yScale(0 ) - yScale( d.barCol * (d.barCol > 0 ? 1 : -1 ) ))
        .style("fill", d=> d[chartContext[tagID]['profileCol']] === 'n'? (d.barCol >= 0 ? "rgba(13, 47, 244)" :"rgba(255,0,0)" ): "rgba(167, 167, 167)")
        .attr("fill-opacity",d=> d[chartContext[tagID]['profileCol']] === 'n'?1: 1)
  }

    
  //$##############################
  // DRAW 0 Line
  svg_chart
    .select(`#profile_${chartContext[`${tagID}_${cCol}`]['cCol']}_line0`)
    .remove().exit();
  
  svg_chart
    .append("svg:line")
    .attr("id",`profile_${chartContext[`${tagID}_${cCol}`]['cCol']}_line0`)
    .attr("x1", 0)
    .attr("x2", width)
    .attr("y1", yScale(0))
    .attr("y2", yScale(0))
    .style("stroke", "rgb(189, 189, 189)");


  //$##############################
  // 2. Draw LIFTS 
  var lifts = svg_chart
    .selectAll(".lifts")
    .data(chartContext[`${tagID}_${cCol}`]['chartData']);

  lifts.exit().remove();

  var lift_entry = lifts.enter()
    .append("text")
    .attr("class","lifts")

  lifts = lift_entry.merge(lifts) 
    .attr("x", d => xScale(d['xCol_v']) + colorBand(d[chartContext[tagID]['profileCol']])   )
    .attr("y", d => d.barCol > 0 ? yScale(d.barCol ) -15 : yScale(0) -15 )
    .attr("dy",12)
    .attr("font-weight",xScale.domain().length > 7 ? 100 :500)
    .text( d => {
      if (d.profile === 'n'){
        return chartContext[tagID].yType.upliftFmt(d.upliftCol);
      } else {
        return "";
      }
    })
    .style("font-weight", "bold")
    .style("font-size",xScale.domain().length > 7 ? "16px" :"20px" )
    .style("fill", d=>  d.upliftCol < 0 ? 'red' : 'green');

} 