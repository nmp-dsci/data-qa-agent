// ------------------------------------------------
// Objective; draw line chart (create/ replce)
// parameters
// target_df: raw data you want to aggregate
// xCol: x column in 'target_df'
// sectionAttr: each of Y axis to plot for xColl Aggregation

// Example sction id 
// sectionAttr = {
//   "s1a":{yFmt:"0.0%"  ,title:"% of Active Member",yAxis:"% of Active NRMA member"},
//   "s1b":{yFmt:"0.0%"  ,title:"Redemption Rate by Cohort",yAxis:"% of Active NRMA member"},
//   "s1c":{yFmt:"$0.1f" ,title:"Redemption $$ per Member",yAxis:"Average Redempption Spend per member"},
//   "s2a":{yFmt:"0.1f"  ,title:"Average Sessions by Cohort",yAxis:"Sessions / Active Members"},
//   "s2b":{yFmt:"0.0%"  ,title:"Penetration into Redemptions",yAxis:"% of Redemption with Engagement"},
//   "s2c":{yFmt:"$0.1f" ,title:"Redemption $$ per Session",yAxis:"Average Redemption Spend / Channel Visits"},
// }

// function 'drawLine'
function drawLine_multiY(target_df,xCol = '',sectionAttr = {}) { 

    // general chart margins
    var margin = {top: 100, right: 20, bottom: 100, left: 90};
    var width = 500 - margin.left - margin.right,
      height = 450 - margin.top - margin.bottom;

    let summ_df = summFunc(target_df , [xCol]);
    summ_df = summ_df.sort(function(x, y){
      return d3.ascending(x[xCol], y[xCol]);
    })

    // loop through 'sections'
    sectionIDs = [... new Set(attrCols.map(d=> d.section))].filter(x=>x !== "undefined"); 

    sectionIDs.map(function(sectionID){

      // step 1: load in section attirbutes
      colDF = attrCols.filter(r => r.section  === sectionID );
      yCols = colDF.map(r=> r.column);

      // melt df : so each row is unique at YAxis/ Xaxis
      melt_df = melt_yCols(summ_df , yCols,xCol);
      
      var yScale = d3.scaleLinear().range([height, 0 ])
        .domain(d3.extent(melt_df.map(d=>d.value))).nice();

      // Load axis's
      var xScale = d3.scaleBand().padding([.1]).rangeRound([0,width]);
      valueDict = uniqueValues([xCol]);
      xScale.domain( valueDict[0].value.sort(d3.ascending) );


      // ###########################################
      // CHART processing
      svg_chart = d3.select(`#${sectionID}`)
        .select(`.chart_${sectionID}`)

      svg_chart.select(".yAxis_name")
        .text(sectionAttr[sectionID].yAxis);

      svg_chart.select(".plot_title")
        .text(sectionAttr[sectionID].title)
        .style("font-size", "26px")

      // X-axis
      svg_chart
        .selectAll(".x_axis")
        .transition()
        .duration(300)
        .call(d3.axisBottom(xScale)) 
        .selectAll("text")
        // .style("font-weight", "bold")
        .style("font-size", "17px")
        .attr("transform", (d,i) =>  `translate(-10,${i%2 == 0 ? 15:0 }) rotate(${0 })`)

      // yaxis
      svg_chart
        .selectAll(".y_axis")
        .transition()
        .duration(300)
        .call(d3.axisLeft(yScale).tickFormat(d3.format(sectionAttr[sectionID].yFmt)))
        .selectAll("text")
        // .style("font-weight", "bold")
        .style("font-size", "17px");

      // ##################################
      // paths data
      path_df = d3.nest()
        .key(d => d.yCol )
        .entries(melt_df);

      var path = d3.line()
        .x(function(d) { return xScale(d['xCol']) + 20; })
        .y(function(d) { return yScale(d['value']); });

      //  Update , Exist , Enter
      var responsePaths = svg_chart
        .selectAll("path#response")
        .data(path_df);

      responsePaths.exit().remove() ;

      responsePaths.enter()
        .append('path')
        .attr("id","response")
        .attr("stroke", "black")
        .attr("fill", "none")
        .merge(responsePaths)
        .attr("d",function(d) { 
          return path(d.values); 
        })
        .attr("class", d => colDF.filter(r=>r.column===d.key)[0].stroke)
        .attr("stroke-width", d => colDF.filter(r=>r.column===d.key)[0].w)
        .attr("stroke-opacity", d => colDF.filter(r=>r.column===d.key)[0].o)
        .attr("stroke", d => colDF.filter(r=>r.column===d.key)[0].c);


    }) // END loop through 'sectionID'


  } // drawLine