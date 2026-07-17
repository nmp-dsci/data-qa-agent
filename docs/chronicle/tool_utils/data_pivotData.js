// row_cols = ['pool_n','renew_n','renew_r','pot_r'];
// pivot_attr = [{'col':'year_fy','value':'2020'},{'col':'year_fy','value':'2021'}];


function pivotData(data_df=[],row_cols=[],pivot_attr=[],derived_growth=false,drived_diff=false){
    // STEP 1: PIVOT DAta
    output_df = [];
    for (row of row_cols){
        console.log('row')
        console.log(row)
        rowFmt = attrCols.filter(r => r.column === row)[0];
        console.log('rowFmt')
        console.log(rowFmt)
        output_row = {'metric':rowFmt['name']}
        for (attr of pivot_attr){
            filter_df = data_df.filter(d => d[attr.col] === attr.value);
            if (filter_df.length > 0){
                // output_row[attr.value] = rowFmt.format(filter_df[0][row]);
                output_row[attr.value] = filter_df[0][row];
            } else {
                output_row[attr.value] = 0;
            }
        }
        // Step 2a: derived calculations
        if (drived_diff){
            output_row['diff'] = rowFmt.format(output_row[pivot_attr[1].value] - output_row[pivot_attr[0].value]);
        }
        // Step 2b: derived calculations
        if (derived_growth){
            output_row['growth'] = d3.format('+0.2%')(output_row[pivot_attr[1].value]/output_row[pivot_attr[0].value]-1);
        }
        // Step 3: final format
        for (attr of pivot_attr){
            output_row[attr.value] = rowFmt.format(output_row[attr.value]);
        }
        // append output
        output_df = output_df.concat(output_row)
    }
    return output_df
}