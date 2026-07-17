
templateHTMLTrend = [
    {       element:'div'
        ,   parent:'#template_trend' 
        ,   setAttr:[
                {'tag':'id','value':'trend_template'},
                {'tag':'class','value':"row pl-3 pr-3 pt-3"}
        ]
    },
    {       element:'div'
        ,   parent:'#trend_template' 
        ,   setAttr:[
                {'tag':'id','value':'$trendID$'},
                {'tag':'class','value':"col-xl-6 pr-3"}
        ]
    },
    {       element:'h3'
        ,   parent:'#$trendID$' 
        ,   innerHTML: '$trendID$ App '
    },
    {       element:'img'
        ,   parent:'#$trendID$' 
        ,   setAttr:[
                {'tag':'id','value':'spinner_$trendID$'},
                {'tag':'src','value':"assets/ajax-loader.gif"},
                {'tag':'style','value':"display: none"},
        ]
    },
    {       element:'button'
        ,   parent:'#$trendID$' 
        ,   setAttr:[{'tag':'id','value':'run_$trendID$'},]
        ,   innerHTML:'Run $trendID$'
    },
    {       element:'div'
        ,   parent:'#$trendID$' 
        ,   setAttr:[{'tag':'id','value':'target'}]
    },
    {       element:'label'
        ,   parent:'#$trendID$ #target' 
        ,   innerHTML: '$trendID$  Selectiton '
    },
    // Application: drop downs
    {       element:'select'
        ,   parent:'#$trendID$ #target' 
        ,   setAttr:[{'tag':'id','value':'plottype'}]
    },
    {       element:'select'
        ,   parent:'#$trendID$ #target' 
        ,   setAttr:[{'tag':'id','value':'trendid'}]
    },
    {       element:'select'
        ,   parent:'#$trendID$ #target' 
        ,   setAttr:[{'tag':'id','value':'profile'}]
    },
    // Drop downn filter
    {       element:'div'
        ,   parent:'#$trendID$ #target'
        ,   setAttr:[
                {'tag':'id','value':'filter'},
                {'tag':'class','value':"dropdown-check-list" },
                {'tag':'tabindex','value':"100" },
        ]
    },
    {       element:'span'
        ,   parent:'#$trendID$ #target #filter'
        ,   setAttr:[{'tag':'class','value':'anchor'}]
        ,   innerHTML: '$trendID$ Filter '
    },
    {       element:'ul'
        ,   parent:'#$trendID$ #target #filter'
        ,   setAttr:[{'tag':'class','value':'items'}]
    },
    // Alert
    {       element:'div'
        ,   parent:'#$trendID$ #target #filter'
        ,   setAttr:[{'tag':'class','value':"alert$trendID$" }]
    },
    {       element:'span'
        ,   parent:'#$trendID$ #target #filter'
        ,   setAttr:[
                {'tag':'class','value':'alertclosebtn'},
            ,   {'tag':'style','value':'display:none'},
            ,   {'tag':'onclick','value':"this.style.display='none'"},
            ]
        ,   innerHTML: 'Warning! More than 3 cuts of the data filtered, first 2 kept'
    },
    // summary data
    {       element:'h4'
        ,   parent:'#$trendID$' 
        ,   innerHTML: 'Topline Summary'
    },
    {       element:'div'
        ,   parent:'#$trendID$' 
        ,   setAttr:[{'tag':'id','value':"table_topline" }]
    },
    {       element:'table'
        ,   parent:'#$trendID$ #table_topline' 
        // ,   setAttr:[{'tag':'width','value':"95%" }]
    },
    // Filters applied
    {       element:'h4'
        ,   parent:'#$trendID$' 
        ,   innerHTML: 'Filters Applied'
    },
    {       element:'div'
        ,   parent:'#$trendID$' 
        ,   setAttr:[{'tag':'id','value':"table_filter" }]
    },
    {       element:'table'
        ,   parent:'#$trendID$ #table_filter' 
        // ,   setAttr:[{'tag':'width','value':"95%" }]
    },

    // column 1
    {       element:'div'
        ,   parent:'#$trendID$' 
        ,   setAttr:[{'tag':'id','value':'column1'}]
    },
]












