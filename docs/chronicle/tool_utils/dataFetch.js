// What is this? 
// Return filters applied in Selection 
// function queryData(data_df,yCol={}, xCol=[''],calc_lvl='n',tagID = 'profile1'){

function dataFetch(data_df,chartContext,tagID, cCol){

      // %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%% 
      // Pull out the raw query based on Column names 
      query_df = data_df.filter(r=> r.cutid === chartContext[`${[tagID]}_${cCol}`].cutid.cutid); 
      query_df = JSON.parse(JSON.stringify(query_df));
      query_df.map(r=> Object.assign(r, {'profile':chartContext[tagID].level}))


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

  return query_df
}