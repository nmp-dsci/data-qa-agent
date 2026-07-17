

const profileMetricsRef = {
    "_n_v":[
        {'name': 'Composition', 'ref': '_c_v' }
    ,   {'name': 'Value', 'ref': '_n_v' }
    ,   {'name': 'Difference', 'ref': '_n_d' }
    ,   {'name': 'Growth %', 'ref': '_n_g' }
    ,   {'name': 'Contribution', 'ref': '_c_g' }
    ],
    "_r_v":[
        {'name': 'Value' , 'ref':'_r_v'}
    ,   {'name': 'Difference' , 'ref':'_r_d'}
    ,   {'name': 'Growth' , 'ref':'_r_g'}
    ],
    "_a_v":[
        {'name': 'Value', 'ref':'_a_v'}
    ,   {'name': 'Difference', 'ref':'_a_d'}
    ,   {'name': 'Growth', 'ref':'_a_g'}
    ]
}

function removeAllChildNodes(parent) {
    if (parent ){
        while (parent.firstChild) {
            parent.removeChild(parent.firstChild);
        }    
    }
}

function profileMetrics(tagID='profile1') {
    yCol=varProfile(tagID); 

    tag = tagID.substr(0,tagID.length-1) ;
    metricObj = document.querySelector(`#${tag}_template #${tagID} #params #metric`);
    removeAllChildNodes(metricObj);

    metricFmt = yCol['n'].slice(-4) ;
    metricOptions = profileMetricsRef[metricFmt]; 

    metricOptions.map(metric => {
        metricOption = document.createElement("option");
        metricOption.text = metric.name;
        metricOption.value = metric.ref
        metricObj.appendChild(metricOption);
    }) // end 'metric'
} // end function 

