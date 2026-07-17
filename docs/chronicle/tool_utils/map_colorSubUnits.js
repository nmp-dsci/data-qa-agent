// -------------------------------------------------------
function colorSubUnits(data, breakType='q', breakCount='5', colorScheme='RdGn', value=$("#target #profile").val()){

    // Check if underlying y response 
    valueAttr = attrCols.filter(r=> r.column === value)[0]
    if (Object.keys(valueAttr).includes('raw_metric')) value = valueAttr.raw_metric

    var nums = data.filter(d => isFinite(+d[value])).map(d => +d[value] );
    var buckets = chroma.limits(nums, breakType, breakCount);
    var t = d3.transition().duration(750);

    d3.selectAll(".path_geo")
        .transition(t)
        .style("fill", function(d){
        e = data.filter(f => +f[primaryCols[geoCol]] === +d.properties[absCode16]); 
        if ( e.length === 1 && !isNaN(e[0][value])  ){
            var bucketNumber = d3.min(buckets.map(function(bucket, i){
            if (e[0][value] <= bucket){
                return i;
            }
            })); // end map
            return colors[colorScheme][breakCount][bucketNumber - 1];
        } else {
            return "#DCDCDC"
        };
        });

    // an array to return for drawing the legend
    var arr = [];
    buckets.map(function(d, i){
        if (i != 0){
        var obj = {};
        obj.bucket = i;
        obj.x = buckets[i - 1];
        obj.width = d - obj.x;
        obj.color = colors[colorScheme][breakCount][i - 1];
        arr.push(obj);
        }
    });

    
    return arr;
} // end ColorSubUnits
