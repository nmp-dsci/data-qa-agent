

trend_plotType = [
    {name:'Line Chart',value:'line'},
    {name:'Heat Tiles',value:'heatmap'},
    {name:'Stacked Bar Chart',value:'stackedbar'},
]


function initTrendFilters(trendID='trend1'){

    // Setup plot type
    parentObj = document.querySelector(`#trend${trendID} #target #plottype`); 

    trend_plotType.map(function(plottype){
        myOption = document.createElement("option");
        myOption.text = plottype.name;
        myOption.value = plottype.value;
        parentObj.appendChild(myOption);
    })

    if (typeof appDefaults !== "undefined" && Object.keys(appDefaults ?? {}).includes(`trend${trendID}PlotType`) ){
        document.querySelector(`#trend${trendID} #target #plottype`).value = appDefaults[`trend${trendID}PlotType`];
    } 

    // --------------------------------------------------------
    // x- axis 
    let trendIDs = attrCols.filter(d=> ['dt','x','x2'].includes(d.class)  );
    trendIDs.map(function(d,i){
        parentObj = document.querySelector(`#trend${trendID} #target #trendid`); 
        myOption = document.createElement("option");
        myOption.text = d.name;
        myOption.value = d.column;
        parentObj.appendChild(myOption);
    });

    // DEFAULT SELECT, (1) try 'dt' default else (2) other default
    if (attrCols.map(r=>r.class).includes('dt')){
        defaultXaxis = attrCols.filter(d=> ['dt'].includes(d.class)  );
    } else {
        defaultXaxis = attrCols.filter(d=> ['x'].includes(d.class)  );
    }
    

    document.querySelector(`#trend${trendID} #target #trendid`).value = defaultXaxis[0].column;

    // --------------------------------------------------------
    // Response SET UP 
    parentObj = document.querySelector(`#trend${trendID} #target #profile`); 
    yValues = attrCols.filter(d=> d.class === "y" && d.trend_y)

    // ADD the 'valueLegend' entries to Profiling Tool Selector
    yValues.map(function(d,i){
        myOption = document.createElement("option");
        myOption.text = d.name;
        myOption.value = d.column;
        parentObj.appendChild(myOption);
    });

    if (typeof appDefaults !== "undefined" && Object.keys(appDefaults ?? {}).includes(`trend${trendID}Response`) ){
        document.querySelector(`#trend${trendID} #profile`).value = appDefaults[`trend${trendID}Response`];
    } else {
        document.querySelector(`#trend${trendID} #profile`).value = yValues[0].column;
    }

    

    // ########################################################
    // ATTRIBUTE FILTERS 
    // STEP 0: Draw "Attribute Filter Drop Down"

    const lvls = [{'c':'n','n':'target'},{'c':'d','n':'base'}]; 

    attrCols.map(function(d,i){
        if (['x2','x','g'].includes(d.class) ) {
            // build out drop down
            xValues = uniqueValues([d.column])[0].value
            // add drop down 
            xValues.map(function(v) {
                lvls.map(lvl => {
                    filterDropdown = document.querySelector(`#trend${trendID} #${lvl.n} #filter .items`); 
                    // if exists 
                    if (filterDropdown){
                        myLi = document.createElement("li");
                        myLi.textContent = d.name + " - " + v + '     ';
            
                        myInput = document.createElement("input");
                        myInput.type = 'checkbox';
                        myInput.className = 'trend_dropdown';
                        myInput.id = lvl.c + '__'+d.column+'__' + v; 
                        myInput.value = d.column+"__" + v ; 
                        myLi.appendChild(myInput);
                        //  
                        filterDropdown.appendChild(myLi);
                    }
                })// end lvl => {}
            });
        }; // end loop through filter for x 
    }); // end loop through column name

    document.querySelector(`#trend${trendID} #target #filter .anchor`).addEventListener("click", () => {
        // Target Controling
        console.log(`trendid:${trendID}`)
        const target_checkList = document.querySelector(`#trend${trendID} #target #filter`);
        if (target_checkList.classList.contains('visible')){
            target_checkList.classList.remove('visible');
        } else {
            target_checkList.classList.add('visible');
        }
    }); // end target on change

}


