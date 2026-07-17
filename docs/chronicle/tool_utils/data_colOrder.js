// Confirm order: 
function dataColOrder(xCol, xColDF){
    xOrder = Object.keys(xColDF).includes('order')  ?  xColDF['order'] : 'missing' ;
    if (xOrder === "missing"){
        // console.log('XCOF MISSING')
        xOrder = uniqueValues([xCol]);
        xOrder = xOrder[0].value.slice().sort((a, b) => d3.ascending(a, b));
        // console.log(xOrder)
        xColDF['order'] = xOrder
    } 
    return xColDF
}