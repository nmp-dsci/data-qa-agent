function initProfileFilters(profileID = 'profile1'){


    // --------------------------------------------------------
    // PROFILNG Selection: Response SET UP 
    yValues = attrCols.filter(d=> d.class === "y" && d.profile_y);

    ['target','base'].map((lvl,idx)=>{
        yValues.map(function(d,i){
            responseTag = `#${profileID} #${lvl} #profile`;
            parentObj = document.querySelector(responseTag);
            if (parentObj ){ 
                myOption = document.createElement("option");
                myOption.text = d.name;
                myOption.value = d.column;
                parentObj.appendChild(myOption);
            };
        });

        // Default values 
        if (typeof appDefaults !== "undefined" && Object.keys(appDefaults ?? {}).includes(`${profileID}Response`) ){
            document.querySelector(responseTag).value = appDefaults[`${profileID}Response`];
        } else {
            document.querySelector(responseTag).value = yValues[0].column;
        }
    })
    
    // ########################################################
    // ATTRIBUTE FILTERS 
    // STEP 0: Draw "Attribute Filter Drop Down"
    attrCols.map(function(d,i){
        if (['x','g','x2'].includes(d.class) ) {
            // build out drop down
            xValues = uniqueValues([d.column])[0].value
            // add drop down 
            xValues.map(function(v) {

                const lvls = [{'c':'n','n':'target'},{'c':'d','n':'base'}]; 
                lvls.map(lvl => {
                    filterDropdown = document.querySelector(`#${profileID} #${lvl.n} .items`); 
                    // if exists 
                    if (filterDropdown){
                        myLi = document.createElement("li");
                        myLi.textContent = d.name + " - " + v + '     ';
            
                        myInput = document.createElement("input");
                        myInput.type = 'checkbox';
                        myInput.className = `${profileID}_dropdown`;
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


    // #######################################################

    // Default filters 

    if (typeof appDefaults !== "undefined" && Object.keys(appDefaults ?? {}).includes(`${profileID}FilterCol`) ){

        colValues = uniqueValues([appDefaults[`${profileID}FilterCol`]])[0].value;
        console.log('colValues')
        console.log(colValues)
        if (colValues.length > 0){
            colValues = colValues.sort((a,b) => b - a );
            default_val = {'n': colValues[0], 'd': colValues[1]};
            document.querySelector(`#${profileID} #n__${appDefaults[`${profileID}FilterCol`]}__${default_val['n']}`).checked = true; 
            document.querySelector(`#${profileID} #d__${appDefaults[`${profileID}FilterCol`]}__${default_val['d']}`).checked = true;     
        }
    }


    // #######################################################
    //  Check list listener functions

    var base_checkList = document.querySelector(`#${profileID} #base #filter`);

    if (base_checkList){
        document.querySelector(`#${profileID} #base .anchor`).addEventListener("click", () => {
        // Target Controling
        if (base_checkList.classList.contains('visible')){
            base_checkList.classList.remove('visible');
        } else {
            base_checkList.classList.add('visible');
            }
        });
    }

    var target_checkList = document.querySelector(`#${profileID} #target #filter`);

    document.querySelector(`#${profileID} #target .anchor`).addEventListener("click", () => {
        // Target Controling
        if (target_checkList.classList.contains('visible')){
            target_checkList.classList.remove('visible');
        } else {
            target_checkList.classList.add('visible');
        }
    }); // end target on change

}