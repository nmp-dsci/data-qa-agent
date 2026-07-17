function drawTopline(profile_all,tagID='') {

  console.log('profile_all')
  console.log(profile_all)
    //------------------------------------------------- 
    // Step 1: Generate DAta 

    total_df  = summFunc(profile_all.filter(r=> r.cutid === d3.min(profile_all.map(v=>v.cutid))),["profile"]);


    // Derived functions 
    if (derivedFunction) { 
      total_df.map(row=> { 
        derivedFunction.map(dCol => {
          row[dCol.c] = dCol.f(row)
        })
        return row
      })
    } ; 

    console.log('total_df')
    console.log(total_df)

    //------------------------------------------------- 
    //  Table 1: "Selection Summary " 

    // dRAW Totals
    totalHeader = [
      {"n":"Group", "c":"Group","t":"c"},
      {"n":"Metric", "c":"Metric","t":"c"},
      {"n":"Total", "c":"value","t":"v"},
      // {"n":"comparison", "c":"comparison","t":"v"},
    ]

    // Set the y response
    yColDropdown =varProfile(tagID); 

    profile_lvls = [... new Set(profile_all.map(r=> r.profile))];

    totalRow = [];
    profile_lvls.map(lvl => {
      yColInfo = attrCols.filter(r=>r.column === yColDropdown[lvl])[0];
      yColInfo = JSON.parse(JSON.stringify(yColInfo));// Stop Javascript over writing itself
      yColInfo['profile'] = lvl
      totalRow.push(yColInfo)
    })

    // ------------------------------------------------------
    //  Selection Totals 
    totalSumm = [];
    totalRow.map(function(metric){
      if (metric.c !== "dummy"){
        addRow = {"Group": metric.profile === 'n'? 'Target' : 'Comparison','Metric':metric.name};
        yCol_c = Object.keys(metric).includes('raw_metric') ? metric.raw_metric : metric.column; 
        // Requies 'total_df' aggregation to sum over the difference metrics too 
        // if (["_a","_r",'_p',"_d"].includes(yColDropdown['n'].slice(-2))) {
        //   yCol_c = yCol_c.replace('_n','_d')
        //   console.log(`OVER RIDE METRIC: ${yCol_c}`)
        // }
        addRow['value'] = d3.format(metric['format'])(total_df.filter(f => f.profile === metric.profile)[0][yCol_c]);

        // addRow['comparison'] = d3.format("0.2%")(targetDF.filter(d => d['dummy'] === "all")[0][metric.column]/baseDF.filter(d => d['dummy'] === "all")[0][metric.column]);
      } else {
        addRow = {'Group':'', 'Metric':metric.name,'value':""};
      }
      totalSumm.push(addRow);
    })

    drawTable(totalSumm, `#${tagID} #table_topline`,totalHeader)

    
    //------------------------------------------------- 
    //  Table 2: "Filter Totals " 

    metricCols = Object.keys(total_df[0]).map(r=> !['profile'].includes(r) ? r : undefined).filter(r=>r)
    console.log(`metricCols: ${metricCols.join('--')}`)

    // dRAW Totals
    totalHeader = [
      {"n":"Metric", "c":"metric","t":"c"},
      {"n":"Target", "c":"target","t":"v"},
    ];

    if (profile_lvls.includes('d')){
      totalHeader = totalHeader.concat([
        {"n":"Comparison", "c":"comparison","t":"v"},
        {"n":"Diff +-", "c":"diff_n","t":"v"},
        {"n":"Diff %", "c":"diff_g","t":"v"},
      ])
    };

    totalRow = [];
    metricCols.map(metric => {
      metricInfo = attrCols.filter(r=>r.column === metric)[0];
      newRow = {'metric':metricInfo.name }
      profile_lvls.map(lvl => {
        lvlName = lvl==='n' ? 'target' : 'comparison';
        newRow[lvlName] = total_df.filter(r=> r['profile']===lvl)[0][metric]; 
        // diff metrics 
        if (lvl === 'd'){
          newRow['diff_n'] = newRow['target'] - newRow['comparison'];
          newRow['diff_g'] = d3.format("+0.2%")(newRow['target'] / newRow['comparison'] - 1);
        };
      });
      // format
      totalHeader.filter(r=> r.t === 'v').map(valueCol => {
          if (valueCol.c !== "diff_g") newRow[valueCol.c] =  d3.format(metricInfo['format'])(newRow[valueCol.c]);
      });
      // dump
      totalRow.push(newRow)
    }); 

    console.log("Total Summary");
    console.log(totalRow)

    drawTable(totalRow,`#${tagID} #table_totals` ,totalHeader)

  } // end function 'drawTopline'