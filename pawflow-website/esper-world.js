/* Generated photographs, measured portals, and the cyclic exploration graph.
 * Rectangles use normalized coordinates inside the original image. */
window.ESPER_WORLD = {
  root: 'study',
  scenes: {
    study: {shortLabel:'Discover',image:'assets/media/esper/scene-00.jpg',label:'Rain on the city. A photograph on the wall.',portals:[
      {target:'control',rect:[.298,.368,.218,.214],label:'Explore the runtime',href:'product.html'}
    ]},
    control: {shortLabel:'Runtime',image:'assets/media/esper/scene-01.jpg',label:'The control room. Choose a direction.',portals:[
      {target:'servers',rect:[.069,.224,.239,.18],label:'Real machines',href:'relays.html'},
      {target:'workshop',rect:[.361,.25,.186,.16],label:'Flows and automation',href:'flows.html'},
      {target:'garden',rect:[.066,.464,.24,.178],label:'Connections',href:'integrations.html'},
      {target:'archive',rect:[.361,.469,.185,.151],label:'The field-guide archive',href:'howtos.html'}
    ]},
    servers: {shortLabel:'Machines',image:'assets/media/esper/scene-02.jpg',label:'Beyond the glass. Your infrastructure.',portals:[
      {target:'workshop',rect:[.114,.346,.12,.184],label:'The workshop',href:'flows.html'}
    ]},
    workshop: {shortLabel:'Flows',image:'assets/media/esper/scene-03.jpg',label:'Ideas become working mechanisms.',portals:[
      {target:'garden',rect:[.116,.354,.108,.153],label:'The rooftop garden',href:'integrations.html'}
    ]},
    garden: {shortLabel:'Connections',image:'assets/media/esper/scene-04.jpg',label:'A living network above the city.',portals:[
      {target:'observatory',rect:[.03,.469,.14,.174],label:'Images, films and voice',href:'howtos.html#media-voice'}
    ]},
    archive: {shortLabel:'Guides',image:'assets/media/esper/scene-05.jpg',label:'Eight doors. Fifty-six field guides.',portals:[
      {target:'station',rect:[.05,.193,.166,.186],label:'Install and first steps',href:'howtos.html#install'},
      {target:'agents',rect:[.251,.208,.134,.178],label:'Agents and models',href:'howtos.html#agents-interop'},
      {target:'train',rect:[.418,.222,.124,.167],label:'Clients and interfaces',href:'howtos.html#clients'},
      {target:'servers',rect:[.569,.235,.108,.16],label:'Relays and workspaces',href:'howtos.html#relays-workspaces'},
      {target:'vault',rect:[.049,.446,.167,.19],label:'Identity and security',href:'howtos.html#identity'},
      {target:'resources',rect:[.251,.444,.134,.175],label:'Resources and packages',href:'howtos.html#resources'},
      {target:'workshop',rect:[.419,.447,.123,.165],label:'Flows and automation',href:'howtos.html#flows'},
      {target:'observatory',rect:[.569,.447,.108,.155],label:'Media and voice',href:'howtos.html#media-voice'}
    ]},
    observatory: {shortLabel:'Media',image:'assets/media/esper/scene-06.jpg',label:'A wider view. New possibilities.',portals:[
      {target:'archive',rect:[.056,.403,.216,.132],label:'Return to the field guides',href:'howtos.html'}
    ]},
    vault: {shortLabel:'Security',image:'assets/media/esper/scene-07.jpg',label:'Keep the important things yours.',portals:[
      {target:'resources',rect:[.081,.296,.153,.255],label:'The collection',href:'howtos.html#resources'}
    ]},
    station: {shortLabel:'Install',image:'assets/media/esper/scene-08.jpg',label:'Every journey begins somewhere.',portals:[
      {target:'study',rect:[.07,.465,.158,.122],label:'Another world within',href:'index.html'}
    ]},
    agents: {shortLabel:'Agents',image:'assets/media/esper/scene-09.jpg',label:'A place to think. A place to remember.',portals:[
      {target:'control',rect:[.106,.214,.165,.139],label:'The control room',href:'product.html'}
    ]},
    train: {shortLabel:'Clients',image:'assets/media/esper/scene-10.jpg',label:'Take the conversation with you.',portals:[
      {target:'servers',rect:[.181,.403,.116,.094],label:'Your connected machines',href:'relays.html'}
    ]},
    resources: {shortLabel:'Resources',image:'assets/media/esper/scene-11.jpg',label:'A collection of useful possibilities.',portals:[
      {target:'workshop',rect:[.043,.225,.232,.317],label:'Put the pieces to work',href:'flows.html'}
    ]}
  }
};
