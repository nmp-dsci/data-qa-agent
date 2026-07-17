// function profileCalc(table_df = [],yCol={'n':"","d":""},xCol='',tagID='profile1'){
function profileCalc(chartContext,tagID, cCol){

    // --------------------------------------------------
    // Total 
    total_df = summFunc(chartContext[`${tagID}_${cCol}`]['chartData'], ['profile'] )

    // --------------------------------------------------- 
    // Add composition metrics for '_n_v'
    metrics = attrCols.filter( d=> ["y"].includes(d.class) & d.trend_y ).map(d => d.column);

    metrics_n = metrics.filter(r=> r.slice(-4) === '_n_v');
    chartContext[`${tagID}_${cCol}`]['chartData'].map(row=> { 
      metrics_n.map(metric_n => {
        row[metric_n.slice(0,-4) + '_c_v'] = row[metric_n] / total_df.filter(f=>f.profile===row.profile)[0][metric_n]
      })  
    })

    // Comparison metrics: Difference / Growth 
    xColAggID_n = aggCols.map(aggCol => chartContext[`${tagID}_${cCol}`]['chartData'].filter(r=>r['profile'] === 'n')[0][aggCol] === chartContext[`${tagID}_${cCol}`]['cCol'] ? aggCol : undefined).filter(Boolean)[0]; 
    xColAggID_d = aggCols.map(aggCol => chartContext[`${tagID}_${cCol}`]['chartData'].filter(r=>r['profile'] === 'd')[0][aggCol] === chartContext[`${tagID}_${cCol}`]['cCol'] ? aggCol : undefined).filter(Boolean)[0]; 

    chartContext[`${[tagID]}_${cCol}`]['cColUniq'][0].value.map(xCol_v => {
      profile_n = chartContext[`${tagID}_${cCol}`]['chartData'].filter(r=> r[xColAggID_n.replace('_c','_v')] === xCol_v &&  r['profile'] === 'n')[0];
      profile_d = chartContext[`${tagID}_${cCol}`]['chartData'].filter(r=> r[xColAggID_d.replace('_c','_v')] === xCol_v &&  r['profile'] === 'd')[0];

      // Treating missing values
      if (profile_n=== undefined) {
        profile_n = {'profile': 'n', 'missing':true};
        profile_n[xColAggID_n] = chartContext[`${[tagID]}_${cCol}`]['cCol'];
        profile_n[xColAggID_n.replace('_c','_v')] = xCol_v;
        metrics.map(metric =>profile_n[metric] = 0.0000001 );
      }
      if (profile_d=== undefined) {
        profile_d = {'profile': 'n', 'missing':true};
        profile_d[xColAggID_d] = chartContext[`${[tagID]}_${cCol}`]['cCol'];
        profile_d[xColAggID_d.replace('_c','_v')] = xCol_v;
        metrics.map(metric =>profile_d[metric] = 0.0000001 );
      }

      //  Metric enrichments
      // profile_diff = JSON.parse(JSON.stringify(profile_n));// Stop Javascript over writing itself
      metrics.map(metric =>profile_n[metric.slice(0,-2)+'_d'] = profile_n[metric] - profile_d[metric] );
      metrics.map(metric=> profile_n[metric.slice(0,-2)+'_g'] = (profile_n[metric] - profile_d[metric]) / profile_d[metric]);
      metrics_n.map(metric_n => {
        metric_n_c = metric_n.slice(0,-4) + '_c_v';
        profile_n[metric_n.slice(0,-4)+"_c_d"] = profile_n[metric_n_c] - profile_d[metric_n_c];
        profile_n[metric_n.slice(0,-4)+"_c_l"] = profile_d[metric_n_c];
        profile_n[metric_n.slice(0,-4)+"_c_g"] = profile_n[metric_n.slice(0,-2)+'_g'] * profile_d[metric_n_c];
      }); 
    }) // end loop through unqiue values


    // ########################################################
    // add Profile Chart defaults for visualisation

    chartContext[`${tagID}_${cCol}`]['chartData'].map(row=> {
      // set cCol values
      xColAggID = aggCols.map(aggCol => chartContext[`${tagID}_${cCol}`]['chartData'].filter(r=>r['profile'] === row['profile'])[0][aggCol] === chartContext[`${tagID}_${cCol}`]['cCol'] ? aggCol : undefined).filter(Boolean)[0]; 
      xColAggID_n = aggCols.map(aggCol => chartContext[`${tagID}_${cCol}`]['chartData'].filter(r=>r['profile'] === 'n')[0][aggCol] === chartContext[`${tagID}_${cCol}`]['cCol'] ? aggCol : undefined).filter(Boolean)[0]; 
      xColAggID_d = aggCols.map(aggCol => chartContext[`${tagID}_${cCol}`]['chartData'].filter(r=>r['profile'] === 'd')[0][aggCol] === chartContext[`${tagID}_${cCol}`]['cCol'] ? aggCol : undefined).filter(Boolean)[0]; 

      
      let xCol_df = {'cCol':row[xColAggID],'xCol_v':row[xColAggID.replace('_c','_v')]};

      Object.assign(row, xCol_df);
      Object.assign(row, {'type_var':chartContext[tagID]['yCol'][row['profile']]
        , 'response': row[chartContext[tagID]['yCol'][row['profile']]] 
        , 'barCol':row[chartContext[tagID]['yCol'][row['profile']].slice(0,-4)+metricObj]
      });

    });

    // ################################
    //  allow multi response differencing
    chartContext[`${tagID}_${cCol}`]['cColUniq'][0].value.map(xCol_v => {
  
      profile_n = chartContext[`${tagID}_${cCol}`]['chartData'].filter(r=> r[xColAggID_n.replace('_c','_v')] === xCol_v &&  r['profile'] === 'n')[0];
      profile_d = chartContext[`${tagID}_${cCol}`]['chartData'].filter(r=> r[xColAggID_d.replace('_c','_v')] === xCol_v &&  r['profile'] === 'd')[0];

      if (["_d","_g"].includes(metricObj.slice(-2))) {
        upliftCol_v = profile_n['barCol']
      } else if (profile_n && profile_d ){ 
        upliftCol_v = profile_n['barCol'] - profile_d['barCol']
      } else if (profile_n && !profile_d){
        upliftCol_v = profile_n['barCol']
      } else {
        upliftCol_v = 0
      }

      if (profile_n) {
        Object.assign(profile_n, {'upliftCol': upliftCol_v })
      }
      if (profile_d) {
        Object.assign(profile_d, {'upliftCol':0 })
      }

    })

    // if nvalues > 10 
    if (chartContext[`${tagID}_${cCol}`]['cColUniqList'].length > 20  ) {
      profile_t10 = chartContext[`${tagID}_${cCol}`]['chartData'].filter(r => r.profile === "n");
      profile_t10 = profile_t10.slice().sort((a, b) => d3.descending(a['response'], b['response'])).slice(0,10);
      keep_xCol_v = profile_t10.map(d => d.xCol_v)
      chartContext[`${tagID}_${cCol}`]['chartData'] = chartContext[`${tagID}_${cCol}`]['chartData'].filter(d => keep_xCol_v.includes(d.xCol_v))
    }
} //profileCalc
