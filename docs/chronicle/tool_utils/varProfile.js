// -------------------------------------------------------
// function 'varProfile': pull profile Cuts 
function varProfile(tagID=''){
    let tag = tagID.substr(0,tagID.length-1) ;
    let num = document.querySelector(`#${tag}_template #${tagID} #target #profile`);
    let den = document.querySelector(`#${tag}_template #${tagID} #base #profile`);
    return {'n':num ? num.value : undefined,'d':den ? den.value : undefined};
}
