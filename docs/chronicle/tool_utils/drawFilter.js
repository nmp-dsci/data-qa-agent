function drawFilter(chartContext,tagID, cCol) {

    // dRAW Totals
    totalHeader = [
      {"n":"Group", "c":"filter_n","t":"c"},
      {"n":"Type", "c":"type","t":"c"},
      {"n":"Predictor", "c":"xCol_name","t":"c"},
      {"n":"Filter", "c":"xCol_v","t":"c"},
    ]

    totalSumm = [];
    chartContext[`${tagID}_${cCol}`].filters_n.map(filter_i =>{
      if (filter_i.xCol_v !== undefined & filter_i.xCol_v !== 'all'){
        addRow = JSON.parse(JSON.stringify(filter_i)); 
        addRow['filter_n'] = filter_i.filter_n === 'n' ? 'Target' : 'Comparison';
        addRow['type'] = filter_i['type']; 
        filterAttr = attrCols.filter(f=>f.column=== filter_i.xCol)[0];

        addRow['xCol_name'] = filterAttr['name'];
        if (Array.isArray(filter_i['xCol_v'])){
          addRow['xCol_v'] = filter_i.xCol_v.join(',')
        } else {
          addRow['xCol_v'] = filter_i.xCol_v
        }
        totalSumm.push(addRow);
      } 
    })

    drawTable(totalSumm, `#${chartContext[tagID].tagID} #table_filter`,totalHeader)

  } // end function 'drawTopline'