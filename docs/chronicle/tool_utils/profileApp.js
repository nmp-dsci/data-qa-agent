ready(() => {

initAppHTML(appName = 'profile', appID = '1' , templateHTML=templateHTMLProfile);
initProfileFilters(tagID = 'profile1')
profileMetrics('profile1');

// --------------------------------------------------------------------------------
// map setup

if (geoCol in (primaryCols ?? []) ) {

    console.log(`Mapping Activated, Geofield: ${primaryCols[geoCol]}`)
    
    mapParent = document.querySelector(`#${'profile1'} #column1`); 
    mapObj = document.createElement('div');
    mapObj.class = "row"; 
    mapObj.id = "map"
    mapRow = mapParent.appendChild(mapObj);

    // mapRow = document.querySelector("#column1 #map"); 

    h3a = document.createElement("h3"); 
    h3b = document.createElement("h3"); 
    div1 = document.createElement("div");
    div2 = document.createElement("div");
    div3 = document.createElement("div");

    // append structure
    h3a.innerHTML = "Target SA4 Penetration";
    mapRow.appendChild(h3a);
    div1.id = "legend";
    mapRow.appendChild(div1);
    h3b.innerHTML = "Selection Map";
    mapRow.appendChild(h3b);
    div2.id = "map";
    mapRow.appendChild(div2);
    div3.id = "controls";
    mapRow.appendChild(div3);

    // ##########################################
    // Set structure

    var map_p = {width_b:900,height_b:600,legend_w:900,legend_h:30,legendbar_h:15};      

    d3.select("#legend")
        .append("svg")
        .attr("width", map_p.legend_w)
        .attr("height",map_p.legend_h)
        .attr("id","svg");

    // INITIALISE Dashboard
    d3.select("#map")
        .append("svg")
        .attr("width", map_p.width_b)
        .attr("height", map_p.height_b) 
        .attr("class","geom");

    // 
    

} // end additional treatment for map

// ##########################################
//  Run Listener: Run Profile
document.querySelector(`#run_${'profile1'}`).addEventListener("click", e => {
    // 
    e.preventDefault();
    $(`#spinner_${'profile1'}`).fadeIn(function() {
        chartContext = profileRun(chartContext,'profile1',() => $(`#spinner_${'profile1'}`).fadeOut(),new_div=false)
    });
}); // END '#run_profile'

// ##########################################
//  Run Listener: Profile Response 
metricTag = `#${'profile1'} #target #profile`;
document.querySelector(metricTag).addEventListener("change", e => {
    e.preventDefault();
    profileMetrics('profile1');
}); 

// Initialise visualisation
chartContext = profileRun(chartContext,'profile1',() => {}, new_div=true);

});