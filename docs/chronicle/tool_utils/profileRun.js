function profileRun(chartContext,tagID='profile1',callback=()=>{},new_div=false){

    document.querySelector(`#${tagID} .alertprofile`).style.display = 'none';

    // ############################################
    // from profile chart
    // metric
    metricObj = document.querySelector(`#profile_template #params #metric`).value; 

    yTypes ={
      // Value
      '_c_v':{'lvl':['n','d'],'desc':'composition metric' ,'upliftFmt':d3.format("+0.1%"),'format':d3.format("0.0%") ,'yTitle':"Composition %"},
      '_c_g':{'lvl':['n','d'],'desc':'Contribution to growth' ,'upliftFmt':d3.format("+0.1%"),'format':d3.format("0.0%") ,'yTitle':"Growth Contribution"},
      '_n_v':{'lvl':['n','d'],'desc':'volume metric' ,'yTitle':"Volume #"},
      '_n_d':{'lvl':['n','d'],'desc':'volume dif metric','upliftFmt':d3.format("+.2s"),'format':d3.format("+.2s") ,'yTitle':"Volume Diff"},
      '_n_g':{'lvl':['n','d'],'desc':'volume growth metric','upliftFmt':d3.format("+0.1%"),'format':d3.format("0.0%") ,'yTitle':"Volume Growth"},

      '_r_v':{'lvl':['n','d'],'desc':'rate metric' ,'yTitle':"Rate #"},
      '_r_d':{'lvl':['n','d'],'desc':'rate dif metric','upliftFmt':d3.format("+.2s"),'format':d3.format("+.2s") ,'yTitle':"Rate Diff"},
      '_r_g':{'lvl':['n','d'],'desc':'rate growth metric','upliftFmt':d3.format("+0.1%"),'format':d3.format("0.0%") ,'yTitle':"Rate Growth"},

      '_a_v':{'lvl':['n','d'],'desc':'average metric' ,'yTitle':"Volume #"},
      '_a_d':{'lvl':['n','d'],'desc':'average dif metric','upliftFmt':d3.format("+.2s"),'format':d3.format("+.2s") ,'yTitle':"Value Diff"},
      '_a_g':{'lvl':['n','d'],'desc':'average growth metric','upliftFmt':d3.format("+0.1%"),'format':d3.format("0.0%") ,'yTitle':"Value Growth"},
    }

    // general chart margins
    var chartDim = {svgHeight:450,svgWidth:profileWidth, top: 100, right: 20, bottom: 40, left: 90};
    chartDim['width'] = chartDim.svgWidth - chartDim.left - chartDim.right,
    chartDim['height'] = chartDim.svgHeight - chartDim.top - chartDim.bottom;

    //  What columns available for profiling
    profileAll = []
    profileCols = attrCols.filter(d => ['x','x2'].includes(d.class)  && d.profile_y).map(d => d.column)

    // initalise chart context 
    chartContext[tagID] = {
      'tagID':tagID,
      'aggCols': aggCols,
      'yCol':varProfile(tagID),
      'yColDropdown':varProfile(tagID=tagID),
      'yType': yTypes[metricObj],
      'profileCol' : 'profile',
      'profileValues' : ['n','d'],
    }

    chartContext[tagID]['yColDF'] = {
      'n':attrCols.filter(d => d.column === chartContext[tagID]['yCol']['n'] )[0] ,
      'd':attrCols.filter(d => d.column === chartContext[tagID]['yCol']['d'] )[0] 
    }      

    chartContext[tagID]['yCol_lookup'] = {
      'n':attrCols.filter(d => d.column === chartContext[tagID]['yColDropdown']['n'] )[0] ,
      'd':attrCols.filter(d => d.column === chartContext[tagID]['yColDropdown']['d'] )[0] 
    }      

    if ( ["_n_v","_r_v","_a_v","_n_d","_r_d","_a_d"].includes(metricObj) ){
      chartContext[tagID]['yType']['upliftFmt'] = d3.format("+"+chartContext[tagID]['yCol_lookup']['n']['format']);
      chartContext[tagID]['yType']['format'] = d3.format(chartContext[tagID]['yCol_lookup']['n']['format']);
    }  

    for (cCol of profileCols){

      getContextProfile(chartContext,tagID, cCol);

      profileAll = profileAll.concat(chartContext[`${tagID}_${cCol}`]['chartData']);

      if (new_div) { 
        initChart(chartContext,tagID, cCol ,chartDim);
      }

      // ############################################
      // LEGEND GENERATION 
      
      legendColValues = [];
      cMap = d3.schemeCategory10;
      chartContext[tagID]['profileValues'].map(function(key,idx){
        legendColValues.push({
          column:cCol
          ,value:key
          ,text:chartContext[tagID]['yCol_lookup'][key].name.trim()
          , value_len:String(chartContext[tagID]['yCol_lookup'][key].name.trim()).length
          ,c: key === 'n'? "rgba(13, 47, 244)": "rgba(167, 167, 167)"
          ,"o":0.7,"w":6
        })
      });

      chartContext[`${tagID}_${cCol}`]['legendValues'] = legendColValues;

      drawChartLegend_transform(chartContext,tagID,cCol,profileWidth)
      drawChartLegend_load(chartContext,tagID,cCol)

      // user interfrace features
      drawFilter(chartContext,tagID, cCol)
      // downloadData(chartContext[`${tagID}_${cCol}`]['chartData'], chartContext);
      // downloadSVG( chartContext);

      // Visualise data
      profileChart(chartContext,tagID,cCol,widthRaw=profileWidth);

    };

    chartContext[tagID]['profileAll'] = profileAll

    profileSummary(chartContext,tagID);

    // Initialise features
    if (geoCol){
      getContextProfile(chartContext,tagID, geoCol);
      drawMaps(chartContext,tagID, geoCol, map_json);
    }

    drawTopline(profileAll,tagID);

    callback.call(this);

    return chartContext
}

