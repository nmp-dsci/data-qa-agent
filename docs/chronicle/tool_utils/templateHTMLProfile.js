
templateHTMLProfile = [
    {       element:'div'
        ,   parent:'#template_profile' 
        ,   setAttr:[
                {'tag':'id','value':'profile_template'},
                {'tag':'class','value':"row pl-3 pr-3 pt-3"}
        ]
    },
    {       element:'div'
        ,   parent:'#profile_template' 
        ,   setAttr:[
                {'tag':'id','value':'$profileID$'},
                {'tag':'class','value':"row"}
        ]
    },
    // column 1
    {       element:'div'
        ,   parent:'#$profileID$' 
        ,   setAttr:[
                {'tag':'id','value':'column1'},
                {'tag':'class','value':"col-xl-8 pr-3"}
        ]
    },
    // column 2
    {       element:'div'
        ,   parent:'#$profileID$' 
        ,   setAttr:[
                {'tag':'id','value':'column2'},
                {'tag':'class','value':"col-xl-4"}
        ]
    },
    {       element:'h4'
        ,   parent:'#$profileID$ #column2' 
        ,   innerHTML: '$profileID$ Selection '
    },
    {       element:'img'
        ,   parent:'#$profileID$ #column2'
        ,   setAttr:[
                {'tag':'id','value':'spinner_$profileID$'},
                {'tag':'src','value':"assets/ajax-loader.gif"},
                {'tag':'style','value':"display: none"},
        ]
    },
    {       element:'button'
        ,   parent:'#$profileID$ #column2'
        ,   setAttr:[{'tag':'id','value':'run_$profileID$'},]
        ,   innerHTML:'Run $profileID$'
    },
    // params
    {       element:'div'
        ,   parent:'#$profileID$ #column2'
        ,   setAttr:[{'tag':'id','value':'params'}]
    },
    {       element:'label'
        ,   parent:'#$profileID$ #column2 #params' 
        ,   innerHTML: '$profileID$ Metric '
    },
    {       element:'select'
        ,   parent:'#$profileID$ #column2 #params' 
        ,   setAttr:[{'tag':'id','value':'metric'}]
    },
    // Target fields
    {       element:'div'
        ,   parent:'#$profileID$ #column2'
        ,   setAttr:[{'tag':'id','value':'target'}]
    },
    {       element:'label'
        ,   parent:'#$profileID$ #column2 #target' 
        ,   innerHTML: 'Target '
    },
    {       element:'select'
        ,   parent:'#$profileID$ #column2 #target' 
        ,   setAttr:[{'tag':'id','value':'profile'}]
    },
    {       element:'div'
        ,   parent:'#$profileID$ #column2 #target'
        ,   setAttr:[{'tag':'id','value':'filter'}
            ,   {'tag':'class','value':'dropdown-check-list'}
            ,   {'tag':'tabindex','value':'100'}
        ]
    },
    {       element:'span'
        ,   parent:'#$profileID$ #column2 #target #filter'
        ,   setAttr:[{'tag':'class','value':'anchor'}]
        ,   innerHTML: '$profileID$ Filter '
    },
    {       element:'ul'
        ,   parent:'#$profileID$ #column2 #target #filter'
        ,   setAttr:[{'tag':'class','value':'items'}]
    },
    // Comparison fields
    {       element:'div'
        ,   parent:'#$profileID$ #column2'
        ,   setAttr:[{'tag':'id','value':'base'}]
    },
    {       element:'label'
        ,   parent:'#$profileID$ #column2 #base' 
        ,   innerHTML: 'Comparison '
    },
    {       element:'select'
        ,   parent:'#$profileID$ #column2 #base' 
        ,   setAttr:[{'tag':'id','value':'profile'}]
    },
    {       element:'div'
        ,   parent:'#$profileID$ #column2 #base'
        ,   setAttr:[{'tag':'id','value':'filter'}
            ,   {'tag':'class','value':'dropdown-check-list'}
            ,   {'tag':'tabindex','value':'100'}
        ]
    },
    {       element:'span'
        ,   parent:'#$profileID$ #column2 #base #filter'
        ,   setAttr:[{'tag':'class','value':'anchor'}]
        ,   innerHTML: '$profileID$ Filter '
    },
    {       element:'ul'
        ,   parent:'#$profileID$ #column2 #base #filter'
        ,   setAttr:[{'tag':'class','value':'items'}]
    },
    // Alert
    {       element:'div'
        ,   parent:'#$profileID$ #column2'
        ,   setAttr:[{'tag':'class','value':"alertprofile" }]
    },
    {       element:'span'
        ,   parent:'#$profileID$ #column2 .alertprofile'
        ,   setAttr:[
                {'tag':'class','value':'alertclosebtn'},
            ,   {'tag':'style','value':'display:none'},
            ,   {'tag':'onclick','value':"this.style.display='none'"},
            ]
        ,   innerHTML: 'Warning! More than 3 cuts of the data filtered, first 2 kept'
    },

    // Profile Summary Tables
    {       element:'hr'
        ,   parent:'#$profileID$ #column2'
        ,   setAttr:[{'tag':'class','value':'items'}]
    },
    // 1. Comparison of Responses
    {       element:'h4'
        ,   parent:'#$profileID$ #column2' 
        ,   innerHTML: 'Topline Summary'
    },
    {       element:'div'
        ,   parent:'#$profileID$ #column2'
        ,   setAttr:[{'tag':'id','value':"table_topline" }]
    },
    {       element:'table'
        ,   parent:'#$profileID$ #column2 #table_topline'
        // ,   setAttr:[{'tag':'width','value':"95%" }]
    },
    // 2. Filtered Data summary
    {       element:'h4'
        ,   parent:'#$profileID$ #column2' 
        ,   innerHTML: 'Data Group Comparison'
    },
    {       element:'div'
        ,   parent:'#$profileID$ #column2'
        ,   setAttr:[{'tag':'id','value':"table_totals" }]
    },
    {       element:'table'
        ,   parent:'#$profileID$ #column2 #table_totals'
        // ,   setAttr:[{'tag':'width','value':"95%" }]
    },
    // 3.  Data Filters
    {       element:'h4'
        ,   parent:'#$profileID$ #column2' 
        ,   innerHTML: 'Data Filters Applied'
    },
    {       element:'div'
        ,   parent:'#$profileID$ #column2'
        ,   setAttr:[{'tag':'id','value':"table_filter" }]
    },
    {       element:'table'
        ,   parent:'#$profileID$ #column2 #table_filter'
        // ,   setAttr:[{'tag':'width','value':"95%" }]
    },
    // Top Uplifts
    {       element:'h4'
        ,   parent:'#$profileID$ #column2' 
        ,   innerHTML: 'Positive Uplifts'
    },
    {       element:'div'
        ,   parent:'#$profileID$ #column2'
        ,   setAttr:[{'tag':'id','value':"table_top" }]
    },
    {       element:'table'
        ,   parent:'#$profileID$ #column2 #table_top'
        // ,   setAttr:[{'tag':'width','value':"95%" }]
    },
    // bottom Uplifts
    {       element:'h4'
        ,   parent:'#$profileID$ #column2' 
        ,   innerHTML: 'Negative Uplifts'
    },
    {       element:'div'
        ,   parent:'#$profileID$ #column2'
        ,   setAttr:[{'tag':'id','value':"table_bottom" }]
    },
    {       element:'table'
        ,   parent:'#$profileID$ #column2 #table_bottom'
        // ,   setAttr:[{'tag':'width','value':"95%" }]
    },
]
