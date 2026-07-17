// What is this? 
// Return filters applied in Selection 
// function queryData(data_df,yCol={}, xCol=[''],calc_lvl='n',tagID = 'profile1'){
function dataCutID(chartContext,tagID, cCol){

    // ####################################
    // STEP 1: Pull the filters applied to the dataset
    filters_n = dropdownFilter(chartContext,tagID) // dropdown filter

    // xCol  requirement
    chartContext[`${[tagID]}_${cCol}`]['aggC'].map(xVar => {
      filters_n.push({'filter_n':chartContext[tagID].level,'xCol':xVar,'xCol_v':undefined,'type':'xCol'})
    })

    // yCol filter
    if (Object.keys(chartContext[tagID].yCol).length > 0   ){
      if (chartContext[tagID].yColDF[chartContext[tagID].level].filter_obj){
        filter_y = chartContext[tagID].yColDF[chartContext[tagID].level].filter_obj.map(r=> Object.assign(r,{'filter_n':chartContext.level,'type':'yCol'}))
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
        filter_n_xcol1 = {'filter_n':chartContext[tagID].level, 'xCol': xCol1_2nd, 'type':'x1', 'class':'x' ,'id': xCol2['id'] +1000  }
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
            filters_n.push({'filter_n':chartContext[tagID].level,'xCol':`all${pad_i}`,'xCol_v':'all','type':'padding'})
        }
    }

    chartContext[`${[tagID]}_${cCol}`]['filters_n'] = filters_n;

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

    chartContext[`${[tagID]}_${cCol}`]['filter_obj_summ'] = filter_obj_summ
    chartContext[`${[tagID]}_${cCol}`]['lookup_field'] = lookup_field

    // console.log("ERROR: related to secondary factors not having 2 ways")
    console.log(` lookup fields ${lookup_field}`)
    cutid = lookup_df.filter(r=> r.value === lookup_field)[0]; 

    if (cutid === undefined){
      console.log("NO DATA FOR 'lookup field' above")
      document.querySelector(".alert"+tag).style.display = 'block';
    } else {
      console.log(`Query Cutid: ${cutid.cutid} from lookup fields ${lookup_field}`)
      chartContext[`${[tagID]}_${cCol}`]['cutid'] = cutid
    }
    // return chartContext

}