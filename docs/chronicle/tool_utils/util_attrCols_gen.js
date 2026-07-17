
// ######################################################
// GENERATE 'attrCols'

metrics = dataCols.filter(f=> aggCols.includes(f) === false)
metrics = metrics.filter(f=> ['cutid'].includes(f) === false)
metrics = metrics.filter(f=> aggCols.map(r=> r.replace('_c','_v')).includes(f) === false)

var attrCols = [
    // Profiling / latent  column
    {class:"p", name:"Profile",column:"profile",order:['n','d']},
    {class:"p", name:"dummy",column:"dummy"},
    {class:"p", name:"xCol",column:"xCol"},
    {class:"p", name:"xCol_v",column:"xCol_v"},
    {class:"p", name:"type_var",column:"type_var"},
];

//  append all 'x' columns
uniqXcols = [new Set(data_df.map(m=>m[aggCols[0]]))][0]

uniqXcols.forEach(xCol => {
    attrCols.push({class:"x",profile_col:"column2",trend_col:"column1",column:xCol,profile_y:true,trend_y:true},)
})

metrics.map(yCol => {
    attrCols.push({class:"y",column:yCol,profile_y:true,trend_y:true  })
})

// Time stamp column
secondary_AttrCols = {};
secondary_lookup = [];

if ('dt_yyyymm' in (primaryCols ?? [])) {
    
    attrCols.map(r=> { if (r['column'] === primaryCols['dt_yyyymm']) Object.assign(r, {'class':'dt'})})

    // $$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$
    // Secondary predictors

    window_keys = Object.keys(window); 
    re =  /(util_).*/g;
    secondary_xCols = window_keys.filter( f=> f.search(re) >= 0 )
    secondary_xCols.map( xCol =>  { 
        // load json then (1) adjust white label name and (2) reduce to whats in the data 
        console.log(`sendoary_xCols == '${xCol}'`)
        xCol = xCol.replace('util_','');
        utilJSON = JSON.parse(window[`util_${xCol}`]); 
        // catch error 
        if (primaryCols[xCol] === undefined){
            console.log(`Cant find "${xCol}" in primaryCols`)
        }
        // relabelling for (1) white label name
        if (!Object.keys(utilJSON[0]).includes(primaryCols[xCol] )  ){
            utilJSON.map(o=> delete Object.assign(o, {[primaryCols[xCol]]: o[xCol] })[xCol]);
        }
        xColAlias= primaryCols[xCol] ;
        //  (2) reduce to whats in the data 
        uniqXcols = [new Set(data_df.filter(f=> f['agg0_c'] === xColAlias ).map(m=> m['agg0_v']))][0]
        utilJSON = utilJSON.filter(f=> Array.from(uniqXcols).includes(String(f[xColAlias])))
        if (utilJSON.length  === 0) {
            console.log("ERROR: utlJSON length 0: values aren't exact same format")
            console.log(uniqXcols)
        }
        // Append
        secondary_AttrCols[xColAlias] = utilJSON; 
        // Append to attrCols 
        console.log(`secondary_AttrCols[xCol] == '${xColAlias}'`)
        rowZero = secondary_AttrCols[xColAlias][0]
        Object.keys(rowZero).map(xCol2 => {
        if (xColAlias !== xCol2){
            entryAttr = {parent:xColAlias,class:"x2",profile_col:"column1",trend_col:"column1",column:xCol2,profile_y:true,trend_y:true}
            attrCols.push(entryAttr)
            secondary_lookup.push({'xCol':xColAlias,'xCol2':xCol2})
        }
        })
    })
}