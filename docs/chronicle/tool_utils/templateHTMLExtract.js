
templateHTMLExtract = [
    {       element:'div'
        ,   parent:'#template_extract' 
        ,   setAttr:[
                {'tag':'id','value':'extract_template'},
                {'tag':'class','value':"row pl-3 pr-3 pt-3"}
        ]
    },
    // column 1: data dictionary
    {       element:'div'
        ,   parent:'#extract_template' 
        ,   setAttr:[
                {'tag':'id','value':'column1'},
                {'tag':'class','value':"col-xl-8 pr-3"}
        ]
    },
    {       element:'h4'
        ,   parent:'#extract_template #column1' 
        ,   innerHTML: 'Data Dictionary'
        ,   setAttr:[{'tag':'class','value':'d-flex justify-content-between align-items-center mb-3'},]
    },
    {       element:'div'
        ,   parent:'#extract_template #column1' 
        ,   setAttr:[
            {'tag':'id','value':'table_dictionary'},
        ]
    },
    {       element:'table'
        ,   parent:'#extract_template #column1 #table_dictionary'
        // ,   setAttr:[{'tag':'width','value':"95%" }]
    },
    // column 2: Download data
    {       element:'div'
        ,   parent:'#extract_template' 
        ,   setAttr:[
                {'tag':'id','value':'column2'},
                {'tag':'class','value':"col-xl-4 pr-3"}
        ]
    },
    {       element:'h4'
        ,   parent:'#extract_template #column2' 
        ,   innerHTML: 'Extract Data'
        ,   setAttr:[{'tag':'class','value':'d-flex justify-content-between align-items-center mb-3'},]
    },
    {       element:'ul'
        ,   parent:'#extract_template #column2 '
        ,   setAttr:[{'tag':'id','value':'extract_download'},{'tag':'class','value':'list-group mb-3'}]
    },
    {       element:'button'
        ,   parent:'#extract_template #column2 '
        ,   setAttr:[{'tag':'id','value':'extract_downloadSummary'},
            {'tag':'class','value':'btn btn-primary btn-lg btn-block'}]
        ,   innerHTML:'Extract Data'
    },
    // Alert
    {       element:'div'
        ,   parent:'#extract_template #column2'
        ,   setAttr:[{'tag':'class','value':"alertextract" }]
    },
    {       element:'span'
        ,   parent:'#extract_template #column2 .alertextract'
        ,   setAttr:[
                {'tag':'class','value':'alertclosebtn'},
            ,   {'tag':'style','value':'display:none'},
            ,   {'tag':'onclick','value':"this.style.display='none'"},
            ]
        ,   innerHTML: 'Warning! More than 3 cuts of the data filtered, first 2 kept'
    },
]
