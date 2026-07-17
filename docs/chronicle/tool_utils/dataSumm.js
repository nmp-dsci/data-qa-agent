// What is this? 
// Return filters applied in Selection 
// function queryData(data_df,yCol={}, xCol=[''],calc_lvl='n',tagID = 'profile1'){
function dataSumm(query_df,chartContext,tagID, cCol){

  chartContext[`${tagID}_${cCol}`]['cutColMap'] = []; 
  aggCols.map(aggVar => {chartContext[`${tagID}_${cCol}`].cutColMap.push({'col':aggVar,'attr':query_df[0][aggVar]})});


  // %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%% 
  // Seondary Variable OVER RIDE
  if (x2_filter_n.length > 0 ){
    console.log("x2: active"); 
    x2_filter_n.map(xCol2 => { 
      console.log(`xCol2: ${xCol2}`)
      xCol1_2nd = secondary_lookup.filter(f=> f['xCol2'] === xCol2['xCol'])[0]['xCol'];
      //  get mapping required 
      xCol2_mapping = [];
      secondary_AttrCols[xCol1_2nd].map(m=> xCol2_mapping.push({'xCol1_v':String(m[xCol1_2nd]), 'xCol2_v':String(m[xCol2['xCol']])   }));
      // 
      // console.log('xCol2_mapping')
      // console.log(xCol2_mapping)
      // Logic to keep 'old' column or replace it: if 'xCol' then don't over write 
      xCol1_xCol = chartContext[`${tagID}_${cCol}`].filters_n.filter(f=> ['xCol'].includes(f.type) && f.xCol === xCol1_2nd); 
      if (xCol1_xCol.length > 0 ){
        console.log("Retain 'xCol1' and apply 'xCol2' with 'all'"); 
        primary_xCol_c = chartContext[`${tagID}_${cCol}`].cutColMap.filter(f=> f.attr === xCol1_2nd )[0]['col']; 
        replace_xCol_c = chartContext[`${tagID}_${cCol}`].cutColMap.filter(f=> f['attr'] === 'all' )[0]['col']

        query_df.map(r=> {
          replace_old = r[primary_xCol_c.replace('_c','_v')]
          replace_df = xCol2_mapping.filter(f=>f['xCol1_v'] === replace_old);
          replObj = {}
          if (replace_df.length === 1 ) {
            replObj[replace_xCol_c.replace('_c','_v')] = replace_df[0]['xCol2_v']
          } else {
            console.log(`MISSING: (a) replace_df, cant find ${replace_old} in "${replace_xCol_c}"`)
            // replObj[replace_xCol_c.replace('_c','_v')] = 'remove'
          }
          Object.assign(r, replObj)
        })

      } else {
        console.log("Replace 'xCol1' with 'xCol2'")
        replace_xCol_c = chartContext[`${tagID}_${cCol}`].cutColMap.filter(f=> f.attr === xCol1_2nd )[0]['col']; 

        query_df.map(r=> {
          replace_old = r[replace_xCol_c.replace('_c','_v')]
          replace_df = xCol2_mapping.filter(f=>f['xCol1_v'] === replace_old);
          replObj = {}
          if (replace_df.length === 1 ) {
            replObj[replace_xCol_c.replace('_c','_v')] = replace_df[0]['xCol2_v']
          } else {
            console.log(`MISSING: (b) replace_df, cant find ${replace_old} in "${replace_xCol_c}"`)
            // replObj[replace_xCol_c.replace('_c','_v')] = 'remove'
          }
          Object.assign(r, replObj)
        })
      }

      query_df.map(r=> r[replace_xCol_c] =  xCol2['xCol'] );
      // query_df = query_df.filter (f=> f[replace_xCol_c.replace('_c','_v')] !== 'remove' );

      // console.log('query_df:remove')
      // console.log(query_df)
    })

    // Redo cutColMap & agg  for secondary variable overrides
    chartContext[`${tagID}_${cCol}`].cutColMap = []; 
    aggCols.map(aggVar => {chartContext[`${tagID}_${cCol}`].cutColMap.push({'col':aggVar,'attr':query_df[0][aggVar]})});    

    query_df = summFunc(query_df, ['cutid','profile'].concat(aggCols.concat(aggCols.map(agg=>agg.replace('_c','_v')))))

  }

  // SETUP Mapping for downstream use: get mapping of all unique columns/values to pull through
  chartContext[`${tagID}_${cCol}`]['cutCols'] = chartContext[`${tagID}_${cCol}`].cutColMap.map(m=> m['attr']);
  chartContext[`${tagID}_${cCol}`]['cutColUniq'] = uniqueValues(chartContext[`${tagID}_${cCol}`].cutCols);
  chartContext[`${tagID}_${cCol}`]['cutColCombos'] = allCombos(chartContext[`${tagID}_${cCol}`].cutColUniq);

  // %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%% 
  // Apply filters to 'query_df' for 'dropdown' and 'yCol'

  chartContext[`${tagID}_${cCol}`].cutCols.map(function(fxCol) { 

    xColArray = chartContext[`${tagID}_${cCol}`].filters_n.filter(f=> f.xCol === fxCol && ['yCol','dropdown'].includes(f.type)).map(r=>r.xCol_v); 

    // Apply filter if not empty 
    if (xColArray.length > 0  ){

      if (typeof(xColArray[0]) === 'object'){
        xColArray = xColArray[0].map(r=> `${r}`)
      }

      // Identify the Column to do filtering on 
      agg_idx = chartContext[`${tagID}_${cCol}`].cutColMap.filter(c=>c.attr === fxCol)[0]['col'];
      console.log(`Apply filter for ${fxCol}: [${xColArray.join(',')}] ... agg_idx: ${agg_idx}`); 
      query_df = query_df.filter(f => xColArray.includes(f[agg_idx.replace('_c','_v')]));

      if (query_df.length === 0) console.log("ERROR: query_df.length === 0")

      // $$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$
      // default multi 'xCol_v' to first combo
      xColIgnore = chartContext[`${tagID}_${cCol}`].filters_n.filter(f=> f.xCol === fxCol && ['xCol'].includes(f.type));

      if (xColArray.length > 1 && xColIgnore.length === 0 ){
        // console.log('%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%')
        console.log("MULTI xCol_v TREATMENT ");-
        // console.log(xColArray)
        query_df.map(r => r[agg_idx.replace('_c','_v')] = xColArray[0])
        // TODO: Then aggregate over it to sum metrics
        // console.log("Query_df: Pre/ Post")
        // console.log(query_df);
        query_df = summFunc(query_df, ['cutid','profile'].concat(
          chartContext[tagID].aggCols.concat(chartContext[tagID].aggCols.map(agg=>agg.replace('_c','_v')))))
        // console.log('Post multi filter: query_df');
        // console.log(query_df);
      }
    }
  }); 
  // TODO : 'active_n' treatment
  // console.log(`query_df3 rows: ${query_df.length}`);
  // console.log(query_df);
  // // $$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$
  // // Apply Date time
  // console.log('DT mapping')
  if (activeMonths){
    query_df.map(r=>r['dt_dups'] = activeMonths.default)
    for (const [key,value] of Object.entries(activeMonths)){
      if (chartContext[`${tagID}_${cCol}`].cutCols.includes(key)){
        query_df.map(r=> r['dt_dups'] = value)
      }
    };
    query_df.map(row=> Object.assign(row,{'active_n_v': row.active_n_v / row.dt_dups}))
  }


  // // $$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$
  // Derived functions 
  if (derivedFunction) { 
      query_df.map(row=> { 
      derivedFunction.map(dCol => {
        row[dCol.c] = dCol.f(row)
      })
      return row
    })
  } 


  //  #############################################
  // Generate requiredCombos: map it a object xCol_c = xCol_v
  requiredCombos  = []; 
  chartContext[`${tagID}_${cCol}`].cutColCombos.map((combo) => {
      combo_row = {};
      allCount = 0;
      combo.map((cuts,idx) => {
        cutCol = chartContext[`${tagID}_${cCol}`].cutCols[idx];
        aggRef = chartContext[`${tagID}_${cCol}`].cutColMap.filter(f=>f.attr === cutCol)[0 + allCount]['col'];
        if (cutCol === "all" ) allCount = allCount + 1;
        if (cuts === '') cuts = '_missing_'
        // console.log(`aggRef: ${aggRef} cutCol: ${cutCol} cuts: ${cuts} idx: ${idx} `)
        // check if need to filter this down 
        xColFilter = chartContext[`${tagID}_${cCol}`].filters_n.filter(f=>f.xCol==cutCol);
        if (cutCol === 'all' || xColFilter.map(r=>r['type']).includes('xCol')){
          // console.log("requiredCombos: 'all' or 'xCol' & 'dropdown'")
          combo_row[aggRef] = cutCol
          combo_row[aggRef.replace('_c','_v')] = cuts
        } else if  (xColFilter.map(r=>r['type']).includes('yCol')){
          // console.log(`requiredCombos 'yCol': ${cutCol}`)
          attrFilter = xColFilter.filter(f=> f['type'].includes('yCol') );
          // console.log('attrFilter');
          // console.log(attrFilter);
          // flag whether you want to keep 'cuts' or not via inclusion list in filter_n.xColArray , ensure no duping with keepStatus
          keepStatus = false
          if (attrFilter.map(r=>r.xCol_v).includes(cuts) ) keepStatus = true;
          // console.log(`keepStatus: ${keepStatus}`)
          if (keepStatus) {
            combo_row[aggRef] = cutCol
            combo_row[aggRef.replace('_c','_v')] = cuts
          }
        } else if  (xColFilter.map(r=>r['type']).includes('dropdown')){
            // console.log(`requiredCombos 'dropdown': ${cutCol}`)
            attrFilter = xColFilter.filter(f=> f['type'].includes('dropdown') );
            // flag whether you want to keep 'cuts' or not via inclusion list in filter_n.xColArray , ensure no duping with keepStatus
            keepStatus = false;
            if (attrFilter.map(r=>r.xCol_v)[0].includes(cuts) ) keepStatus = true; // KEEP first cut only
            // console.log(`keepStatus: ${keepStatus}`)
            if (keepStatus) {
              combo_row[aggRef] = cutCol
              combo_row[aggRef.replace('_c','_v')] = cuts
            }
        }
      });
      if (Object.keys(combo_row).length === aggCols.length * 2 ) {
        requiredCombos.push(combo_row)
      } 
  });

  requiredCombos.map(r => r['profile'] = chartContext[tagID].level)

  // STEP 2: With Required combinations identied, default when they don't exist 
  
  // y Defaults
  responses = attrCols.filter( d=> ["y","ts"].includes(d.class)  ).map(d => d.column);
  yDefault = {'missing':1}
  responses.map(function(r){
    if ( ['value','uplift'].includes(r)){
      yDefault[r] = 0.00001
    } else {
      yDefault[r] = 0
    }
  })

  // Hash existing combinations: for (a) expended and (b) actuals 
  hashCols = aggCols.concat(aggCols.map(r=> r.replace('_c','_v')));

  requiredCombos.map(r => r['hash'] = hashCols.map(h => r[h]).join('___'));
  query_df.map(r => r['hash'] = hashCols.map(h => r[h]).join('___'));

  chartContext[`${tagID}_${cCol}`]['requiredCombos'] = requiredCombos


  expectedHash = requiredCombos.map(r=>r.hash);
  actualHash = query_df.map(r=>r.hash); 

  missingHash = [];
  expectedHash.map(r => {
      if (!actualHash.includes(r)) {
          // console.log(`queryData: appending Missing entries: "${r}"`)
          missingHash.push(r);
          //  append required
          missing_entry = requiredCombos.filter(c=> c['hash'] === r)[0];
          missing_entry = Object.assign(missing_entry, yDefault); 
          query_df.push(missing_entry);
      } 
  });

  

  return query_df
}