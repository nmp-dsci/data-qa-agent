
function trendRun(trendID ,cCol,callback=()=>{}, new_div=false){

  tagID = `trend${trendID}`;

  // document.querySelector(`.alerttrend${trendID}`).style.display = 'none';

  // Get variable
  xCol1 = document.querySelector(`#${tagID} #target #trendid`).value;

  // general chart margins
  var chartDim = {svgHeight:450,svgWidth:trendWidth, top: 100, right: 20, bottom: 40, left: 90};
  chartDim['width'] = chartDim.svgWidth - chartDim.left - chartDim.right,
  chartDim['height'] = chartDim.svgHeight - chartDim.top - chartDim.bottom;

  // trendAll = []

  // Set Context Dictionary 
  yCol=varProfile(tagID);
  chartContext[tagID] = {
    'tagID':tagID,
    'level':'n',
    'aggCols': aggCols,
    'xCol1':xCol1 , 
    'yCol':varProfile(tagID),
  }

  chartContext[tagID]['xCol1DF'] = attrCols.filter(d=> d.column===chartContext[tagID]['xCol1'])[0] ;
  chartContext[tagID]['xCol1DF'] = dataColOrder(xCol1, chartContext[tagID]['xCol1DF']) ;
  chartContext[tagID]['yColDF'] = {'n':attrCols.filter(d => d.column === chartContext[tagID]['yCol']['n'] )[0] };

  getContextTrend(chartContext,tagID, cCol)

  if (new_div) { 
    initChart(chartContext,tagID, cCol ,chartDim);
  }
  
  // ############################################
  // LEGEND GENERATION 
  drawChartLegend_extract(chartContext,tagID, cCol)
  drawChartLegend_transform(chartContext,tagID,cCol,chartDim['width'])
  drawChartLegend_load(chartContext,tagID,cCol)

  // other params
  // drawFilter(chartContext);
  // downloadData(input_df, chartContext);
  // downloadSVG( chartContext);

  plotType = document.querySelector(`#${tagID} #target #plottype`).value;

  if (plotType === 'line') { 
    trendDF = drawLine_multiC(chartContext,tagID,cCol,chartDim);
  } else if (plotType === 'heatmap'){
    trendDF = draw_heatmap(chartContext,tagID,cCol,chartDim);
  } else if (plotType === 'stackedbar'){
    trendDF = draw_stackedbar(chartContext,tagID,cCol,chartDim);
  }

  // chartContext[`${tagID}_${cCol}`]['chartData'].map(r=> r['trendID'] = trendID)
  // trendAll = trendAll.concat(chartContext[`${tagID}_${cCol}`]['chartData']);

  // trendSummary(trendAll); // doesn't exist yet
  // drawTopline(trendAll,tagID);

  callback.call(this);

}



