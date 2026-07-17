// What is this? 
// Return filters applied in Selection 
// function queryData(data_df,yCol={}, xCol=[''],calc_lvl='n',tagID = 'profile1'){
function queryData(data_df,chartContext){

    // ####################################
    // STEP 1: Pull the filters applied to the dataset
    filters_n = dropdownFilter(chartContext) // dropdown filter

    // xCol  requirement
    chartContext['aggC'].map(xVar => {
      filters_n.push({'filter_n':chartContext.level,'xCol':xVar,'xCol_v':undefined,'type':'xCol'})
    })

    // yCol filter
    if (Object.keys(chartContext.yCol).length > 0   ){
      if (chartContext.yColDF[chartContext.level].filter_obj){
        filter_y = chartContext.yColDF[chartContext.level].filter_obj.map(r=> Object.assign(r,{'filter_n':chartContext.level,'type':'yCol'}))
        filters_n = filters_n.concat(filter_y)
      }
    }

    // ####################################
    // 20210824: enhancement for secondary factors
    // Secondary Variables, need to turn back to 'underlying' variable filters

    filters_n.map( (r,idx)=> { 
      r['id'] = idx
      xColAttr = attrCols.filter(f=> r.xCol == f.column )[0];
      if (xColAttr === undefined){
        r['class'] = 'all'
      } else {
        r['class'] = xColAttr['class']
      }
    }); 

    x2_filter_n = filters_n.filter(f=> ['x2'].includes(f['class']) ); 

    if (x2_filter_n.length > 0 ){
      console.log("x2: active"); 
      x2_filter_n.map(xCol2 => { 
        console.log(`xCol2: ${xCol2}`)
        xCol1_2nd = secondary_lookup.filter(f=> f['xCol2'] === xCol2['xCol'])[0]['xCol'];
        console.log(`xCol1_2nd: ${xCol1_2nd}`)
        filter_n_xcol1 = {'filter_n':chartContext.level, 'xCol': xCol1_2nd, 'type':'x1', 'class':'x' ,'id': xCol2['id'] +1000  }
        // recreate filter if they exist
        if (xCol2['xCol_v'] === undefined ){
          filter_n_xcol1['xCol_v'] = undefined 
        } else {
          xCol1_filter_v = secondary_AttrCols[xCol1_2nd].filter(f=>  xCol2['xCol_v'].includes(f[xCol2['xCol']])).map(m=>m[xCol1_2nd]); 
          filter_n_xcol1['xCol_v'] = xCol1_filter_v
        }
        //  
        filters_n.push(filter_n_xcol1)
      })
    }
        
    // ####################################
    // STEP 2 Unqiue xCols for filtering  (with padding for < lvls)
    lvls = aggCols.length
    uniqXcols = [new Set(filters_n.filter(f=> f['class'] !== 'x2').map(r=> r.xCol))]

    if ( uniqXcols[0].size < lvls ){
        // console.log(`LESS than 3 filters, padding with ALL , size: ${uniqXcols[0].size}`)
        pad_n = lvls - uniqXcols[0].size; 
        for (pad_i in [... Array(pad_n).keys()]){
            // console.log(`pad_i == ${pad_i}`)
            filters_n.push({'filter_n':chartContext.level,'xCol':`all${pad_i}`,'xCol_v':'all','type':'padding'})
        }
    }


    // ####################################
    // STEP 3: Get final filters to look up 
    // Problem: groups is disolving the 'all' padding e.g. 2x All 1xAge = 1x All 1xAge
    var filter_obj_summ = d3.groups(filters_n.filter(f=> f['class'] !== 'x2'),d => d['xCol']);
    
    // Pull the Raw Query 
    allDefaults = aggCols.map((x,i)=> `all${i}`)
    filter_obj_summ.map(r => r[0] = allDefaults.includes(r[0])  ? 'all':r[0])
    lookup_field = filter_obj_summ.map(r=> r[0]).sort().join('___');

    console.log('filter_obj_summ');
    console.log(filter_obj_summ);

    if (filter_obj_summ.filter(r=> r[0] !== 'all').length > aggCols.length ) {
      // DESC: when xCol filters applied over threshold (aggCols), then fix it removing xCol Dropdown and reruning 
      console.log('xCol filters applied over threshold (aggCols): fix applied ')
      ancilXcol = filters_n.filter(r=> r['type'] !== 'dropdown').map(r=>r['xCol'])
      rmShortlist = filters_n.filter(r=> r['type'] === 'dropdown' && !ancilXcol.includes(r['xCol'])).map(r=> r.xCol); 
      rmShortxCols = rmShortlist.filter((x,i)=>rmShortlist.indexOf(x) === i );
      
      over_aggCols = filter_obj_summ.filter(r=> r[0] !== 'all').length;
      rmDropdownXcol = over_aggCols -  aggCols.length  ; 
      rmXcols = rmShortxCols.slice(0,rmDropdownXcol);
      console.log(`Too many 'dropdown' filters removing: ${rmXcols.join('--')} `);

      // rerun filter summary
      filters_n = filters_n.filter(r=> !rmXcols.includes(r.xCol) ); 
      filter_obj_summ = d3.groups(filters_n,d => d['xCol']);
      lookup_field = filter_obj_summ.map(r=> r[0]).sort().join('___');

      // Kick of warning
      document.querySelector(".alert"+tag).style.display = 'block';

    }

    // console.log("ERROR: related to secondary factors not having 2 ways")
    console.log(` lookup fields ${lookup_field}`)
    cutid = lookup_df.filter(r=> r.value === lookup_field)[0]; 

    if (cutid === undefined){
      console.log("NO DATA FOR 'lookup field' above")
      document.querySelector(".alert"+tag).style.display = 'block';
      return [];
    } else {

      console.log(`Query Cutid: ${cutid.cutid} from lookup fields ${lookup_field}`)

      // %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%% 
      // Pull out the raw query based on Column names 
      query_df = data_df.filter(r=> r.cutid === cutid.cutid); 
      query_df = JSON.parse(JSON.stringify(query_df));
      query_df.map(r=> Object.assign(r, {'profile':chartContext.level}))


       // API call
      // async function getData(cutid) {
      //   const response = await fetch(`http://127.0.0.1:3000/lookup/${cutid}`);
      //   const output = await response.json() ; 
      //   // const output2 = await output.then(r=> {r});
      //   return output
      // }

      // const getData = async (cutid) =>  {
      //   const response = await fetch(`http://127.0.0.1:3000/lookup/${cutid}`);
      //   const query_df = await response.json() ; 
      //   return query_df
      // }

      // const useData = async (cutid,chartContext.level) => {
      //   const query_df = await getData(cutid);
      //   query_df.map(r=> Object.assign(r, {'profile':chartContext.level}));
      //   console.log('check nathan')
      //   console.log(query_df)
      //   return query_df;
      // }

      // query_df = useData(cutid.cutid,chartContext.level)

      cutColMap = []; 
      aggCols.map(aggVar => {cutColMap.push({'col':aggVar,'attr':query_df[0][aggVar]})});


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
        xCol1_xCol = filters_n.filter(f=> ['xCol'].includes(f.type) && f.xCol === xCol1_2nd); 
        if (xCol1_xCol.length > 0 ){
          console.log("Retain 'xCol1' and apply 'xCol2' with 'all'"); 
          primary_xCol_c = cutColMap.filter(f=> f.attr === xCol1_2nd )[0]['col']; 
          replace_xCol_c = cutColMap.filter(f=> f['attr'] === 'all' )[0]['col']

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
          replace_xCol_c = cutColMap.filter(f=> f.attr === xCol1_2nd )[0]['col']; 

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
      cutColMap = []; 
      aggCols.map(aggVar => {cutColMap.push({'col':aggVar,'attr':query_df[0][aggVar]})});    

      query_df = summFunc(query_df, ['cutid','profile'].concat(aggCols.concat(aggCols.map(agg=>agg.replace('_c','_v')))))

    }

    // SETUP Mapping for downstream use: get mapping of all unique columns/values to pull through
    cutCols = cutColMap.map(m=> m['attr']);
    cutColUniq = uniqueValues(cutCols);
    cutColCombos = allCombos(cutColUniq);


    // console.log('query_df2');
    // console.log(query_df);


      // %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%% 
      // Apply filters to 'query_df' for 'dropdown' and 'yCol'

      cutCols.map(function(fxCol) { 

        xColArray = filters_n.filter(f=> f.xCol === fxCol && ['yCol','dropdown'].includes(f.type)).map(r=>r.xCol_v); 

        // console.log(`xColArray for: ${fxCol}`)
        // console.log(xColArray)
        // console.log(xColArray.length)

        // Apply filter if not empty 
        if (xColArray.length > 0  ){

          if (typeof(xColArray[0]) === 'object'){
            xColArray = xColArray[0].map(r=> `${r}`)
          }

          // Identify the Column to do filtering on 
          agg_idx = cutColMap.filter(c=>c.attr === fxCol)[0]['col'];
          console.log(`Apply filter for ${fxCol}: [${xColArray.join(',')}] ... agg_idx: ${agg_idx}`); 
          query_df = query_df.filter(f => xColArray.includes(f[agg_idx.replace('_c','_v')]));

          if (query_df.length === 0) console.log("ERROR: query_df.length === 0")

          // $$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$
          // default multi 'xCol_v' to first combo
          xColIgnore = filters_n.filter(f=> f.xCol === fxCol && ['xCol'].includes(f.type));

          if (xColArray.length > 1 && xColIgnore.length === 0 ){
            // console.log('%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%')
            console.log("MULTI xCol_v TREATMENT ");-
            // console.log(xColArray)
            query_df.map(r => r[agg_idx.replace('_c','_v')] = xColArray[0])
            // TODO: Then aggregate over it to sum metrics
            // console.log("Query_df: Pre/ Post")
            // console.log(query_df);
            query_df = summFunc(query_df, ['cutid','profile'].concat(aggCols.concat(aggCols.map(agg=>agg.replace('_c','_v')))))
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
          if (cutCols.includes(key)){
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
      cutColCombos.map((combo) => {
          combo_row = {};
          allCount = 0;
          combo.map((cuts,idx) => {
            cutCol = cutCols[idx];
            aggRef = cutColMap.filter(f=>f.attr === cutCol)[0 + allCount]['col'];
            if (cutCol === "all" ) allCount = allCount + 1;
            if (cuts === '') cuts = '_missing_'
            // console.log(`aggRef: ${aggRef} cutCol: ${cutCol} cuts: ${cuts} idx: ${idx} `)
            // check if need to filter this down 
            xColFilter = filters_n.filter(f=>f.xCol==cutCol);
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

      requiredCombos.map(r => r['profile'] = chartContext.level)

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


      return [query_df,filters_n]
    }
}