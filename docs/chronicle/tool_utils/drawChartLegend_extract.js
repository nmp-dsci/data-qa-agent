
function drawChartLegend_extract(chartContext,tagID, cCol){

    // 3a. always sort it 
    chartContext[`${tagID}_${cCol}`]['uniqcColsAdj'] = chartContext[`${tagID}_${cCol}`]['uniqcColsAdj'].sort((a , b) => d3.ascending(a, b)) 

    // 3. generate value mapping
    cColValues = [];
    cMap = [...d3.schemeCategory10 , ...d3.schemeTableau10,...d3.schemeCategory10 , ...d3.schemeTableau10 ] ;
    chartContext[`${tagID}_${cCol}`]['uniqcColsAdj'].map(function(key,idx){
        cColValues.push({
        column:chartContext[`${tagID}_${cCol}`].cCol
        ,value:key
        , value_len:String(key).length
        ,c:cMap[idx]
        ,"o":0.7,"w":6
        })
    });

    chartContext[`${tagID}_${cCol}`]['legendValues'] = cColValues;

    // return cColValues
}
