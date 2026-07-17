// What is this? 
// Return filters applied in Selection 
function dropdownFilter(trendContext,tagID){

    // when either target/base filters applied 
    filter_lvl = trendContext[tagID].level=== 'n' ? 'target' : 'base';
    dropdown = document.querySelectorAll (`#${trendContext[tagID].tagID} #${filter_lvl} input:checked`);

    filter_obj = []
    dropdown.forEach(function(e){
        console.log('checked')
        filter_str = e.id.split('__')
        if (filter_str[0]  === trendContext[tagID].level ){
            filter_obj.push({'filter_n':filter_str[0],'xCol':filter_str[1],'xCol_v':filter_str[2],'type':'dropdown'})
        }
    });
    
    return filter_obj
}