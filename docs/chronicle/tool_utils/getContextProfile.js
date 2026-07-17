


function getContextProfile(chartContext,tagID, cCol){
      
    // ##########################
    // Set Context Dictionary 
    chartContext[`${tagID}_${cCol}`] = {'cCol':cCol,'aggC':[cCol]}
    chartContext[`${tagID}_${cCol}`]['cColDF'] = attrCols.filter(d=> d.column===cCol)[0] ;
    chartContext[`${tagID}_${cCol}`]['cColDF'] = dataColOrder(cCol, chartContext[`${[tagID]}_${cCol}`]['cColDF']) ;
    chartContext[`${tagID}_${cCol}`]['cColUniq'] = uniqueValues([cCol]); 
    chartContext[`${tagID}_${cCol}`]['cColUniqList'] = allCombos(chartContext[`${[tagID]}_${cCol}`]['cColUniq']); 

    //#######################################################
    // Generate data
    profile_df = [];
    filter_df = [];
    chartContext[tagID]['profileValues'].map( function (calc_lvl){

    chartContext[tagID]['level'] = calc_lvl;

    dataCutID(chartContext,tagID, cCol)
    query_df = dataFetch(data_df,chartContext,tagID, cCol)

    input_df = [];
    input_df = dataSumm(query_df,chartContext,tagID, cCol)

    profile_df = profile_df.concat(input_df)
    filter_df = filter_df.concat(chartContext[`${[tagID]}_${cCol}`].filters_n)
    }); // end generate data

    chartContext[`${tagID}_${cCol}`]['chartData'] = profile_df
    chartContext[`${tagID}_${cCol}`].filters_n = filter_df;

    // references for 'input_df'
    cColRef = '';
    aggCols.map(aggCol => {if (chartContext[`${tagID}_${cCol}`]['chartData'][0][aggCol] === chartContext[`${[tagID]}_${cCol}`].cCol) cColRef = aggCol})
    chartContext[`${tagID}_${cCol}`]['cColRef'] = cColRef
    chartContext[`${tagID}_${cCol}`]['cColRef_v'] = chartContext[`${[tagID]}_${cCol}`]['cColRef'].replace('_c','_v')

    // Profile Calculation
    profileCalc(chartContext,tagID, cCol)

}













