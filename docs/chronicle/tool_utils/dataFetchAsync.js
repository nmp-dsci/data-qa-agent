// What is this? 
// Return filters applied in Selection 
// function queryData(data_df,yCol={}, xCol=[''],calc_lvl='n',tagID = 'profile1'){


function fetchCutID(chartContext) {
  return fetch(`http://127.0.0.1:3000/lookup/${chartContext.cutid.cutid}`);
}


async function dataFetchAsync(chartContext){

  try {
      query_df = await fetchCutID(chartContext);
      query_df = query_df.json()
      // query_df.map(r=> Object.assign(r, {'profile':chartContext.level}))
      return query_df;
  }
  catch (e) {
      console.log("Error found", e.message);
      throw e;
  }

}



