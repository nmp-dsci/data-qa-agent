// --------------------------------------------------
// helper function
function drawTable(data, divID,tableHead){
    // prep data
    data = data.map(d => {
        entry = [];
        // Aggregated fields
        tableHead.map(function(agg){
        if (d.cut === "Lift" & agg.n !== 'cut'){
            entry.push(d3.format("0.2f")(d[agg.c]));
        } else {
            if (agg.f ){
            entry.push(agg.f(d[agg.c]));
            } else { 
            entry.push(d[agg.c]);
            }
        }
        })
        // Value Columns [constant]
        // entry.push((d.change));
        return entry;
    });

    // Select HTML DOM object
    table_t10 = d3.select(divID);

    // //  Set height
    table_t10
        .style('height',(50 + data.length * 30 ) + 'px')

    // Column names: kill existing and draw new
    table_t10
        .selectAll(".col_name")
        .remove();
    
    table_t10
        .select("table")
        .append("tr")
        .attr("class","col_name")
        .selectAll("th")
        .data(tableHead.map(d => d.n))
        .enter()
        .append("th")
        .text( d => d)
        .style("font-weight", "bold")
        .style("font-size", "16px")
        .style("color", function(d){
        col_df = tableHead.filter( r => r.n === d )[0]
        return col_df['colour']
        });

    // Rows: kill existing and draw new
    table_t10
        .selectAll(".row_data")
        .remove();

    metricID = tableHead.map(d => d.t).indexOf('v');

    data.map(function(r){
        table_t10.select("table")
        .append("tr")
        .attr("class","row_data")
        .selectAll("td")
        .data(r)
        .enter()
        .append("td")
        .attr("alight", (d,i) => i == 0 ? "left" : "right")
        .text(d => d)
        .style("font-weight", "bold")
        .style("font-size", "16px")
        .style("color", (d,i) => {
            // if (i >= metricID & d !== '' ){
            //   if (parseFloat(d) < 0){
            //     return "red";
            //   } else { 
            //     return "green";
            //   }
            // } 
            return "black";
        })
    });
    
} // end Draw tAble function
