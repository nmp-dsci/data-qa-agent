

function initAppHTML(appName = 'trend', appID = 1, templateHTML= []){

    replaceFrom = `$${appName}ID$`;
    replaceTo = `${appName}${appID}`;

    templateHTML.map(element=> {

        parentObj = document.querySelector(element['parent'].replace(replaceFrom,replaceTo)); 
        newObj = document.createElement(element['element'].replace(replaceFrom,replaceTo));

        if (element['setAttr']){ element['setAttr'].map(attr => newObj.setAttribute(attr.tag,attr.value.replace(replaceFrom,replaceTo)))}

        if (element['innerHTML']){ newObj.innerHTML = element['innerHTML'].replace(replaceFrom,replaceTo)  }

        parentObj.appendChild(newObj);
    })

}
