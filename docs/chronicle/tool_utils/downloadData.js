function downloadData(data=[{}], chartContext){

  dataExtract = JSON.parse(JSON.stringify(data));

  // Rename cuts 
  aggCols.map(aggCol => {
    if (dataExtract[0][aggCol] !== 'all' ) {
      colName = dataExtract[0][aggCol];
      dataExtract.map(r=> r[colName] = r[aggCol.replace('_c','_v')] )
    }
  })
  // kill off backend columns
  dataExtract.map(row=>{
    [...aggCols, ...['cutid','hash','profile'] , ...aggCols.map(r=>r.replace('_c','_v'))].map(killCol=>{
      delete row[killCol]
    })
  })
  
    const replacer = (key, value) => value === null ? '' : value // specify how you want to handle null values here
    var header = Object.keys(dataExtract[0])
    var outputCSV = [
      header.join(','), // header row first
      ...dataExtract.map(row => header.map(fieldName => JSON.stringify(row[fieldName], replacer)).join(','))
    ].join('\r\n')

    // Button Preparation
    var colDiv = document.querySelector("#"+chartContext.tagID+'_'+chartContext.cCol);

    var downloadLink = document.querySelector("#"+chartContext.tagID+'_'+chartContext.cCol+"_csv");
    if (downloadLink) {
      console.log("Remove old download data")
      colDiv.removeChild(downloadLink);
    }

    // Create Button
    downloadButton=document.createElement('a');
    downloadButton.className = "d-none d-sm-inline-block btn btn-sm btn-primary shadow-sm";
    downloadButton.id = chartContext.tagID+'_'+chartContext.cCol+"_csv";
    downloadButton.innerHTML='<a class="d-none d-sm-inline-block btn btn-sm btn-primary shadow-sm"><i class="fas fa-download fa-sm text-white-50"></i> Download Data</a>'

    downloadButton.download=chartContext.tagID+'_'+chartContext.cCol+".csv";
    downloadButton.href='data:text/csv;charset=utf-8,'+escape(outputCSV);
    colDiv.appendChild(downloadButton);
  }