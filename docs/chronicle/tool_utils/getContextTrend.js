


function getContextTrend(chartContext,tagID, cCol){
      
    // ##########################
    // Set Context Dictionary 
    chartContext[`${tagID}_${cCol}`] = {'cCol':cCol,'aggC':[xCol1,cCol]};
    chartContext[`${tagID}_${cCol}`]['cColDF'] = attrCols.filter(d=> d.column===chartContext[`${tagID}_${cCol}`]['cCol'])[0] ;
    chartContext[`${tagID}_${cCol}`]['cColDF'] = dataColOrder(cCol, chartContext[`${tagID}_${cCol}`]['cColDF']) ;
    // chartContext[`${tagID}_${cCol}`]['cColUniq'] = uniqueValues([cCol]); 

    //#######################################################
    // Generate data
    dataCutID(chartContext,tagID, cCol)
    query_df = dataFetch(data_df,chartContext,tagID, cCol)

    input_df = [];
    input_df = dataSumm(query_df,chartContext,tagID, cCol) 
    chartContext[`${tagID}_${cCol}`]['chartData'] = input_df

    // references for 'chartData'
    cColRef = '';
    aggCols.map(aggCol => {if (chartContext[`${tagID}_${cCol}`]['chartData'][0][aggCol] === cCol) cColRef = aggCol})
    chartContext[`${tagID}_${cCol}`]['cColRef'] = cColRef
    chartContext[`${tagID}_${cCol}`]['cColRef_v'] = chartContext[`${tagID}_${cCol}`]['cColRef'].replace('_c','_v')

    xCol1Ref = '';
    aggCols.map(aggCol => {if (chartContext[`${tagID}_${cCol}`]['chartData'][0][aggCol] === xCol1) xCol1Ref = aggCol})
    chartContext[`${tagID}_${cCol}`]['xCol1Ref'] = xCol1Ref
    chartContext[`${tagID}_${cCol}`]['xCol1Ref_v'] = chartContext[`${tagID}_${cCol}`]['xCol1Ref'].replace('_c','_v')

    // Derived 
    chartContext[`${tagID}_${cCol}`]['uniqcColsRaw'] = Array.from([new Set(chartContext[`${tagID}_${cCol}`]['chartData'].map(r=> r[chartContext[`${tagID}_${cCol}`].cColRef.replace("_c","_v")]))][0])
    
    if (chartContext[`${tagID}_${cCol}`]['uniqcColsRaw'].length > 15){
      chartContext[`${tagID}_${cCol}`]['uniqcColsAdj'] = chartContext[`${tagID}_${cCol}`]['uniqcColsRaw'].slice(0,15)
      chartContext[`${tagID}_${cCol}`]['chartDataRaw'] = chartContext[`${tagID}_${cCol}`]['chartData']
      chartContext[`${tagID}_${cCol}`]['chartData'] = chartContext[`${tagID}_${cCol}`]['chartData'].filter(f=>  chartContext[`${tagID}_${cCol}`]['uniqcColsAdj'].includes(f[chartContext[`${tagID}_${cCol}`].cColRef.replace("_c","_v")]))
    } else {
      chartContext[`${tagID}_${cCol}`]['uniqcColsAdj'] = chartContext[`${tagID}_${cCol}`]['uniqcColsRaw']
    }


}













