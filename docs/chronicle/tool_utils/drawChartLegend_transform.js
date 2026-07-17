


function drawChartLegend_transform(chartContext,tagID,cCol,width){


    // 2. X treatment (move across)
    xCol2_cumsum = d3.cumsum(chartContext[`${tagID}_${cCol}`]['legendValues'].map(r=>r.value_len))
    box_cumsum = d3.cumsum(chartContext[`${tagID}_${cCol}`]['legendValues'].map(r=>1))
    chartContext[`${tagID}_${cCol}`]['legendValues'].map((r,i) => Object.assign(r,{'value_cumsum': i===0?0:xCol2_cumsum[i-1],'box_cumsum':i===0?0:box_cumsum[i-1]}))
    chartContext[`${tagID}_${cCol}`]['legendValues'].map((r,i) => Object.assign(r,{'legX_box':r.box_cumsum*20 + r.value_cumsum *16 }))
    chartContext[`${tagID}_${cCol}`]['legendValues'].map((r,i) => Object.assign(r,{'legX_text':r.legX_box + 20 }))
    // 3. Y treatment (move down) 
    chartContext[`${tagID}_${cCol}`]['legendValues'].map((r,i) => Object.assign(r,{'line':Math.floor(r.legX_box / width ) + 1 }))
    chartContext[`${tagID}_${cCol}`]['legendValues'].map((r,i) => Object.assign(r,{'legY_box':  20 + (r.line+0) * 20  }))
    chartContext[`${tagID}_${cCol}`]['legendValues'].map((r,i) => Object.assign(r,{'legY_text': 20 + (r.line+0) * 20 + 14  }))
    //  4. Legend Line re indexing for multi line
    lineUniq = [... new Set(chartContext[`${tagID}_${cCol}`]['legendValues'].map(r=>r.line))];
    lineDF = {};
    lineUniq.map(lineID => { 
        if (lineID !==  1) {
        LineEntry = chartContext[`${tagID}_${cCol}`]['legendValues'].filter(r=> r.line === (lineID));  // take first to what you want to pull back
        lineDF[lineID] = {'line':lineID,'legX_box':LineEntry[0].legX_box,'legX_text':LineEntry[0].legX_text }
        }
    });
    chartContext[`${tagID}_${cCol}`]['legendValues'].map(row => { 
        if (row.line !== 1 ){
        Object.assign(row, {'legX_box': row.legX_box - lineDF[row.line].legX_box, 'legX_text': row.legX_text - lineDF[row.line].legX_box })
        }
    })
}
