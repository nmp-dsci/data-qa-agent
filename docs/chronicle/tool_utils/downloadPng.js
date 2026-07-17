// Generate PNG from SVG 
// ref: https://observablehq.com/@mbostock/saving-svg

// function serialize(svg){
//     const xmlns = "http://www.w3.org/2000/xmlns/";
//     const xlinkns = "http://www.w3.org/1999/xlink";
//     const svgns = "http://www.w3.org/2000/svg";

//     return function serialize(svg) {
//         svg = svg.cloneNode(true);
//         const fragment = window.location.href + "#";
//         const walker = document.createTreeWalker(svg,NodeFilter.SHOW_ELEMENT);
//         while (walker.nextNode()){
//             for (const attr of walker.currentNode.attributes){
//                 if (attr.value.includes(fragment)){
//                     attr.value = attr.value.replace(fragment, "#");
//                 }
//             }
//         }
//         svg.setAttrubtesNS(xmlns,"xmlns", svgns);
//         svg.setAttrubtesNS(xmlns,"xmlns:xlink", xlinkns);
//         const serializer = new window.XMLSerializer; 
//         const string = serializer.serializeToString(svg);
//         return new Blob([string], {type:"image/svg+xml"});
//     };
// }

// function svgDownloadPng(svg) { 
//     let resolve, reject; 
//     const promise =  new Promise((y,n) => (resolve = y, reject = n ));
//     const image = new Image; 
//     image.onerror = reject; 
//     image.onload = () => {
//         const rect = svg.getBoundingClientRect();
//         const context = DOM.context2d(rect.width, rect.height); 
//         context.drawImage(image, 0, 0, rect.width , rect.height) ; 
//         context.canvas.toBlob(resolve); 
//     }; 
//     image.src =  URL.createObjectURL(serialize(svg));
//     return promise;
// }




function svgToPng(svgtag) {
    saveSvgAsPng(d3.select(svgtag).node(), 'chart.png');
}; 

function svgToSvg(svgtag) {
    svgAsDataUri(d3.select(svgtag).node(), {}, function(uri) {
        console.log('uri', uri);
    });
}


function downloadSVG( chartContext){

    var width = 500;
      height = 450 ;

    var colDiv = document.querySelector("#"+chartContext.tagID+'_'+chartContext.cCol);

    var downloadLink = document.querySelector("#"+chartContext.tagID+'_'+chartContext.cCol+"_downloadSVG");
    if (downloadLink) {
      console.log("Remove old download data")
      colDiv.removeChild(downloadLink);
    }

	// ######################################
    // Create Button
    buttonSVG=document.createElement('a');
    buttonSVG.className = "d-none d-sm-inline-block btn btn-sm btn-primary shadow-sm";
    buttonSVG.id = chartContext.tagID+'_'+chartContext.cCol+"_downloadSVG";
    buttonSVG.innerHTML='<a class="d-none d-sm-inline-block btn btn-sm btn-primary shadow-sm"><i class="fas fa-download fa-sm text-white-50"></i> Download Plot</a>'
    buttonSVG.onclick=function(){
        var svgString = getSVGString(document.querySelector("#"+chartContext.tagID+'_'+chartContext.cCol+"_svg"));
        svgString2Image( svgString, 2*width, 2*height, 'png', save ); // passes Blob and filesize String to the callback
	}

	function save( dataBlob, filesize ){
		saveAs( dataBlob, `${chartContext.tagID}_${chartContext.cCol}.png` ); // FileSaver.js function
	};

    colDiv.appendChild(buttonSVG);

}

// src: http://bl.ocks.org/Rokotyan/0556f8facbaf344507cdc45dc3622177
// Below are the functions that handle actual exporting:
// getSVGString ( svgNode ) and svgString2Image( svgString, width, height, format, callback )
function getSVGString( svgNode ) {
	svgNode.setAttribute('xlink', 'http://www.w3.org/1999/xlink');
	var cssStyleText = getCSSStyles( svgNode );
	appendCSS( cssStyleText, svgNode );

	var serializer = new XMLSerializer();
	var svgString = serializer.serializeToString(svgNode);
	svgString = svgString.replace(/(\w+)?:?xlink=/g, 'xmlns:xlink='); // Fix root xlink without namespace
	svgString = svgString.replace(/NS\d+:href/g, 'xlink:href'); // Safari NS namespace fix

	return svgString;

	function getCSSStyles( parentElement ) {
		var selectorTextArr = [];

		// Add Parent element Id and Classes to the list
		selectorTextArr.push( '#'+parentElement.id );
		for (var c = 0; c < parentElement.classList.length; c++)
				if ( !contains('.'+parentElement.classList[c], selectorTextArr) )
					selectorTextArr.push( '.'+parentElement.classList[c] );

		// Add Children element Ids and Classes to the list
		var nodes = parentElement.getElementsByTagName("*");
		for (var i = 0; i < nodes.length; i++) {
			var id = nodes[i].id;
			if ( !contains('#'+id, selectorTextArr) )
				selectorTextArr.push( '#'+id );

			var classes = nodes[i].classList;
			for (var c = 0; c < classes.length; c++)
				if ( !contains('.'+classes[c], selectorTextArr) )
					selectorTextArr.push( '.'+classes[c] );
		}

		// Extract CSS Rules
		var extractedCSSText = "";
		for (var i = 0; i < document.styleSheets.length; i++) {
			var s = document.styleSheets[i];
			
			try {
			    if(!s.cssRules) continue;
			} catch( e ) {
		    		if(e.name !== 'SecurityError') throw e; // for Firefox
		    		continue;
		    	}

			var cssRules = s.cssRules;
			for (var r = 0; r < cssRules.length; r++) {
				if ( contains( cssRules[r].selectorText, selectorTextArr ) )
					extractedCSSText += cssRules[r].cssText;
			}
		}
		

		return extractedCSSText;

		function contains(str,arr) {
			return arr.indexOf( str ) === -1 ? false : true;
		}

	}

	function appendCSS( cssText, element ) {
		var styleElement = document.createElement("style");
		styleElement.setAttribute("type","text/css"); 
		styleElement.innerHTML = cssText;
		var refNode = element.hasChildNodes() ? element.children[0] : null;
		element.insertBefore( styleElement, refNode );
	}
}


function svgString2Image( svgString, width, height, format, callback ) {
	var format = format ? format : 'png';

	var imgsrc = 'data:image/svg+xml;base64,'+ btoa( unescape( encodeURIComponent( svgString ) ) ); // Convert SVG string to data URL

	var canvas = document.createElement("canvas");
	var context = canvas.getContext("2d");

	canvas.width = width;
	canvas.height = height;

	var image = new Image();
	image.onload = function() {
		context.clearRect ( 0, 0, width, height );
		context.drawImage(image, 0, 0, width, height);

		canvas.toBlob( function(blob) {
			var filesize = Math.round( blob.length/1024 ) + ' KB';
			if ( callback ) callback( blob, filesize );
		});

		
	};

	image.src = imgsrc;
}
