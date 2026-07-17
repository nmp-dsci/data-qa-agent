ready(() => {

  // Create DAta dictionary 
  uniqXcols = [];
  aggCols.map((aggCol) => {
    aggColUniq = [... new Set(data_df.map(r=>r[aggCol]))]
    uniqXcols = uniqXcols.concat(aggColUniq);
  })

  xCols = [... new Set(uniqXcols)];

  if (secondary_lookup ) console.log('error' ) //{xCols = xCols.concat(secondary_lookup.map(m=>m['xCol2']))}


  // 
  yCols = attrCols.filter(f=>f.class==="y" && f.trend_y).map(r=>r.column);
  Cols = xCols.concat(yCols);
  Cols = Cols.map(r=> ['all'].includes(r) ? undefined : r ).filter(r=>r);


  attrColFields = ['column','name','class','parent','description'];
  dictionary = [];

  Cols.map(xCol => { 
    xColRow = {};
    xCollAttr = attrCols.filter(f=>f['column'] === xCol);
    attrColFields.map(field=>{
      if (xCollAttr[0]){
        xColRow[field] = xCollAttr[0][field] || "" ;
      } else {
        xColRow[field] = xCol;
      }
      xColRow['unique'] = uniqueValues([xCol])[0]['value'].join(' - '); 

    })
    dictionary.push(xColRow);
  })

  attrCols.filter(f=> ['x2','x','y'].includes(f.class)).map(col => { 
    if (dictionary.map(r=>r.column).includes(col.column) === false){
      xColRow = {};
      xCollAttr = attrCols.filter(f=>f['column'] === col.column);
      attrColFields.map(field=>{
        if (xCollAttr[0]){
          xColRow[field] = xCollAttr[0][field] || "" ;
        } else {
          xColRow[field] = xCol;
        }
        xColRow['unique'] = "NOT IN DATASET" 
  
      })
      dictionary.push(xColRow);
    }

  })

  // dRAW Totals
  totalHeader = [
    {"n":"Name", "c":"name","t":"c"},
    {"n":"Raw Name", "c":"column","t":"c"},
    {"n":"Class", "c":"class","t":"c"},
    {"n":"Parent", "c":"parent","t":"c"},
    // {"n":"Description", "c":"description","t":"c"},
    {"n":"Unique Values", "c":"unique","t":"c"},
  ]

  drawTable(dictionary, "#table_dictionary",totalHeader)


  // Create Extract function
  extractRun(function() {}, new_div=true)

  document.querySelector("#extract_downloadSummary").addEventListener("click", e => productExtract());

});


// #####################################
// initialse "Extract Summary"
function extractRun(callback,new_div=false){

  dtColAttrs = attrCols.filter(f=> ["dt"].includes(f.class) );

  xColsAttr = attrCols.filter(f=> ["x2","x","dt"].includes(f.class) );
  download_ul = document.querySelector("#extract_download");

  aggCols.map( (aggCol,idx) => {
    download_li = document.createElement("li");
    download_li.className = "list-group-item d-flex justify-content-between";
    
    download_div = document.createElement("div");

    download_span = document.createElement("h6");
    download_span.className = "my-0 pb-3";
    download_span.innerHTML = `Option ${idx + 1}`;
    download_div.appendChild(download_span)

    // Build Select drop down
    download_select = document.createElement("select");
    download_select.className = "custom-select d-block w-100"; 
    download_select.id = `download_op${idx}`;

    download_option = document.createElement("option");
    download_option.value = "";
    download_option.innerHTML = "Choose ...";
    download_select.appendChild(download_option)

    xColsAttr.map((xColAttr) => { 
      download_option = document.createElement("option");
      download_option.value = xColAttr.column;
      download_option.innerHTML = xColAttr.name;
      download_select.appendChild(download_option) 
    }) // forEach 'xColsAttr'

    if (idx=== 0 & dtColAttrs.length > 0 ) download_select.value = dtColAttrs[0].column

    download_div.appendChild(download_select)

    download_li.appendChild(download_div)

    download_ul.appendChild(download_li)


  }) // forEach 'aggCols'

  callback.call(this);
}

// #####################################
// produce summary 


function productExtract(){

  // Pull whats in drop downs
  extractOptions = aggCols.map((aggCol,idx)=>{
    select_v = document.querySelector(`#download_op${idx}`); 
    return select_v.value || 'all';
  })
  extractOptions = extractOptions.filter(f=>f!=="all")

  query_df= queryData(data_df, yCol,extractOptions,'n','extract')[0];

  // Rename cuts 
  aggCols.map(aggCol => {
    if (query_df[0][aggCol] !== 'all' ) {
      colName = query_df[0][aggCol];
      query_df.map(r=> r[colName] = r[aggCol.replace('_c','_v')] )
    }
  })
  // kill off backend columns
  query_df.map(row=>{
    [...aggCols, ...['cutid','hash','profile'] , ...aggCols.map(r=>r.replace('_c','_v'))].map(killCol=>{
      delete row[killCol]
    })
  })
  

  // // $$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$
  // Generate CSV

  const replacer = (key, value) => value === null ? '' : value // specify how you want to handle null values here
  var header = Object.keys(query_df[0])
  var outputCSV = [
    header.join(','), // header row first
    ...query_df.map(row => header.map(fieldName => JSON.stringify(row[fieldName], replacer)).join(','))
  ].join('\r\n')


  downloadSummary=document.createElement('a');
  downloadSummary.download='extract_'+lookup_field+".csv";
  downloadSummary.href='data:text/csv;charset=utf-8,'+escape(outputCSV);

  document.body.appendChild(downloadSummary); 
  downloadSummary.click(); 
  document.body.removeChild(downloadSummary); 

}



