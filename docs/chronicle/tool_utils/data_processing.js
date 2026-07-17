// Flatten Utilities
const uniqueValues = function(columns=[]){
  var valueDict = [];
  columns.map(function(col){
      // console.log(`uniqueValues:col = ${col}`)
      colSpecs = attrCols.filter(d=> d.column === col)[0];
      if (colSpecs === undefined) colSpecs = {'class':'all'}
      if (['dt','x','g','all'].includes(colSpecs['class'])){
        // console.log("uniqueValues:['x','g','all']")
        if (colSpecs && 'order' in colSpecs && colSpecs.order.length > 0){
          values = colSpecs['order'];
        } else if (col == 'all') {
          values = ['all']
        } else {
          uniq_df = data_df.filter(r=> r.agg0_c === col ).map(r=> r.agg0_v); 
          if (uniq_df.length === 0 ) uniq_df = data_df.filter(r=> r.agg1_c === col ).map(r=> r.agg1_v); 
          values = uniq_df.filter((x,i)=>uniq_df.indexOf(x) === i );
        }
        valueDict.push({
          key:col
          , value:values
        });
      } else if (['x2'].includes(colSpecs['class'])) {
        // console.log("uniqueValues:['x2']")
        primaryCol = secondary_lookup.filter(f=>f['xCol2'] === col )[0]['xCol']
        uniq_df = secondary_AttrCols[primaryCol].map(r=> r[col])
        values = uniq_df.filter((x,i)=>uniq_df.indexOf(x) === i );
        valueDict.push({
          key:col
          , value:values
        });

      } else {
        valueDict.push({
            key:col
          , value:['all']
        });
      }
  });
  return valueDict
}

// CARTESIAN PRODUCTS
function allCombos(valueDict=[]){

  const f = (a, b) => [].concat(...a.map(d => b.map(e => [].concat(d, e))));
  const cartesian = (a, b, ...c) => (b ? cartesian(f(a, b), ...c) : a);

  if (valueDict.length === 1){
      flatArr = valueDict[0].value.map(d => [d])
  } else if (valueDict.length === 2){
      flatArr = cartesian(valueDict[0].value,valueDict[1].value)
  } else if (valueDict.length === 3){
      flatArr = cartesian(valueDict[0].value,valueDict[1].value,valueDict[2].value)
  } else if (valueDict.length === 4){
      flatArr = cartesian(valueDict[0].value,valueDict[1].value,valueDict[2].value,valueDict[3].value)
  }

  return flatArr
} // end 'allCombos'


// NEST
function AggSumm( data, keys, rollupFunc) {

  // Generate Agg column 
  data.map(r=>r['aggCol'] = keys.map(k=> r[k]).join('__'))

  // Compute Aggreagion
  summData = d3.rollups(data, rollupFunc , d => d.aggCol);

  // Un-nest 
  flatDF = []
  summData.map((row)=> {
    rowEntry = row[1]
    row[0].split('__').map((key,idx) =>{
      rowEntry[keys[idx]] = key
    })
    flatDF.push(rowEntry);
  })

  return flatDF;
} // end 'AggSumm' function  

// MASTER summFunc
function summFunc(data, keys){
  // dependancies
  // 'rollupFunction' is the aggregation calculation
  // 'derivedFunction': are derived calucations onto of output of 'rollupFunction'

    // Generate Agg column 
    data.map(r=>r['aggCol'] = keys.map(k=> r[k]).join('__'))

    // Compute Aggreagion
    summData = d3.rollups(data, rollupFunction , d => d.aggCol);
  
    // Un-nest 
    flatDF = []
    summData.map((row)=> {
      rowEntry = row[1]
      row[0].split('__').map((key,idx) =>{
        rowEntry[keys[idx]] = key
      })
      flatDF.push(rowEntry);
    });

        // // $$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$
    // Derived functions 
    if (derivedFunction) { 
      flatDF.map(row=> { 
      derivedFunction.map(dCol => {
        row[dCol.c] = dCol.f(row)
      })
      return row
    })
  } 

  return flatDF
};
