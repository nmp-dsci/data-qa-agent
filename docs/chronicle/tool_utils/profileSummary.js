// ##################################################
// function 'profileSummary'
function profileSummary(chartContext,tagID){

    profile_all = chartContext[tagID].profileAll.filter(d => d.profile === "n")

    // ##########################################
    // Overall Predictor leveral
    // 3. bring back the summary. 
    profileExtent = d3.rollups(profile_all, function(v)   { 
        return {
            'max':d3.max(v,r =>(isFinite(r.upliftCol)  ? r.upliftCol : -100 ))
        ,   'min':d3.min(v,r =>(isFinite(r.upliftCol)  ? r.upliftCol : 100 ))
        }
    },d=>d['cCol'] );

    profileExtent = profileExtent.filter(r=> r[0])

    let pProfile1 = [];
    profileExtent.map(function(skew){
        ['min','max'].map(calc => {
            skewRow =  profile_all.filter(d => d['cCol'] === skew[0]  && d.upliftCol === skew[1][calc] )[0];
            if (skewRow){
                colDesc = attrCols.filter(d=> d.column === skewRow.cCol)[0];
                Object.assign(skewRow, {'xCol_name': colDesc.name,'summCalc':calc});
                pProfile1 = pProfile1.concat(skewRow);
            }
        })
    });

    profile1Max = pProfile1.filter(row=> row.summCalc === "max")
    profile1Max = profile1Max.slice().sort((a, b) => d3.descending(a.upliftCol, b.upliftCol));

    profile1Min = pProfile1.filter(row=> row.summCalc === "min")
    profile1Min = profile1Min.slice().sort((a, b) => d3.ascending(a.upliftCol, b.upliftCol));

    yCol = varProfile('profile1');

    // 4. Plot summary 
    let profile1Header = [{"n":"Predictor" , "c":"xCol_name"   ,"t":"c"}];
    profile1Header.push({"n":"Segment"     , "c":"xCol_v"      ,"t":"c"});

    // Number vs Metric 
    profile1Header.push({"n":"Uplift", "c":"upliftCol","t":"n", "f":chartContext[tagID].yType['upliftFmt']});
    // profile1Header.push({"n":"Composition" , "c":"composition" ,"t":"n", "f":d3.format(".2%")      });

    
    drawTable(profile1Max, `#${chartContext[tagID].tagID} #table_top`,profile1Header)
    drawTable(profile1Min, `#${chartContext[tagID].tagID} #table_bottom`,profile1Header)

} // END: profileSummary
