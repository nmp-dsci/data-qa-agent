
//$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$
// Apply mapping of name/ format stored for column name
attrColNames.map(colInfo => {
  if (attrCols.map(r=>r['column']).includes(colInfo['column'])){
    attrCols.map(r=> {
      if (r['column'] === colInfo['column']  && r['class'][0] === colInfo['class'][0]){
        addAttrs = Object.keys(colInfo).filter(f => ['class','column'].includes(f)==false)
        assignAttrs = {}
        addAttrs.map(a=> assignAttrs[a] = colInfo[a])
        Object.assign(r, assignAttrs)
      }
    })
  }
});

// if name doesn't exist default it to column name
attrCols.map(r=> Object.assign(r , {'name': (r['name'] ?? r['column'])}))
attrCols.map(r=> Object.assign(r , {'format': (r['format'] ?? "0.3s")}))

