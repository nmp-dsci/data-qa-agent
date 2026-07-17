// #################################
//  TREND
ready(() => {

    let trendApps = 2 ; //document.querySelector('#trend_template').childElementCount; 
    trendCols = attrCols.filter(d => ['x','x2'].includes(d.class) & d.trend_y ).map(d => d.column);

    for (let trendID = 1; trendID < (trendApps+1); trendID++ ){ 
        (function(){
        console.log(`trendID init=${trendID ?? 99}`)

        initAppHTML(appName = 'trend', appID = trendID , templateHTML=templateHTMLTrend);
        initTrendFilters(trendID);

        for (cCol of trendCols){
            trendRun(trendID,cCol,  ()=>{},new_div=true);
        }

        document.querySelector(`#run_trend${trendID}`).addEventListener("click", e => {
            e.preventDefault();
            $(`#spinner_trend${trendID}`).fadeIn(() => {
                for (cCol of trendCols){
                    trendRun(trendID,()=>$(`#spinner_trend${trendID}`).fadeOut())
                }
            },new_div=false)
        });
        }());
    }// end TREND loop
})