/** Visualiseur 3D — Globe wireframe premium (blanc/cyan/purple). */
(function(){
if (typeof THREE==='undefined'){var fb=document.getElementById('globe-fallback');if(fb)fb.classList.add('active');return;}
var container=document.getElementById('globe-container');
var fallback=document.getElementById('globe-fallback');
var W=container.clientWidth,H=container.clientHeight;
var scene=new THREE.Scene();
var camera=new THREE.PerspectiveCamera(40,W/H,0.1,1000);
camera.position.set(0,1.2,4.8);camera.lookAt(0,0,0);
var renderer=new THREE.WebGLRenderer({antialias:true,alpha:true});
renderer.setSize(W,H);renderer.setPixelRatio(1);
container.insertBefore(renderer.domElement,container.firstChild);
if(fallback)fallback.style.display='none';
// Globe wireframe subtil
var gG=new THREE.SphereGeometry(1.1,72,44);
var gM=new THREE.MeshBasicMaterial({color:0xffffff,wireframe:true,transparent:true,opacity:0.06});
var globe=new THREE.Mesh(gG,gM);scene.add(globe);
// Meridiens
var latG=new THREE.Group();
for (var lat=-60;lat<=60;lat+=30){
  var rad=1.12*Math.cos(lat*Math.PI/180),y=1.12*Math.sin(lat*Math.PI/180);
  var rG=new THREE.TorusGeometry(rad,0.001,8,100);
  var rM=new THREE.MeshBasicMaterial({color:0xffffff,transparent:true,opacity:0.04});
  var ring=new THREE.Mesh(rG,rM);ring.position.y=y;ring.rotation.x=Math.PI/2;
  latG.add(ring);
}
scene.add(latG);
// Cities points
var cities=[
  {n:'Lille',lat:50.63,lon:3.06,c:0x06b6d4,s:1.6},
  {n:'Paris',lat:48.86,lon:2.35,c:0xffffff,s:1},
  {n:'New York',lat:40.71,lon:-74.01,c:0xffffff,s:1},
  {n:'Tokyo',lat:35.68,lon:139.76,c:0xffffff,s:1},
  {n:'Londres',lat:51.51,lon:-0.13,c:0xffffff,s:1},
  {n:'San Francisco',lat:37.77,lon:-122.42,c:0xffffff,s:1},
  {n:'Sydney',lat:-33.87,lon:151.21,c:0xffffff,s:1},
  {n:'Dubai',lat:25.20,lon:55.27,c:0xffffff,s:1},
  {n:'Moscou',lat:55.75,lon:37.62,c:0xffffff,s:1},
  {n:'Sao Paulo',lat:-23.55,lon:-46.63,c:0xffffff,s:1}
];
function llToVec(lat,lon,r){
  var ph=(90-lat)*(Math.PI/180),th=(lon+180)*(Math.PI/180);
  return new THREE.Vector3(-r*Math.sin(ph)*Math.cos(th),r*Math.cos(ph),r*Math.sin(ph)*Math.sin(th));
}
var ptsG=new THREE.Group();
for (var i=0;i<cities.length;i++){
  var c=cities[i],pos=llToVec(c.lat,c.lon,1.13);
  var s=c.s||1,dG=new THREE.SphereGeometry(0.015*s,8,8);
  var dM=new THREE.MeshBasicMaterial({color:c.c});
  var dot=new THREE.Mesh(dG,dM);dot.position.copy(pos);
  if (c.c===0x06b6d4){dot.material.opacity=1;} // Lille glow
  ptsG.add(dot);
}
scene.add(ptsG);
// Arcs
var arcsG=new THREE.Group();scene.add(arcsG);
var activeArcs=[];
function mkArc(fi,ti,color){
  var p1=llToVec(cities[fi].lat,cities[fi].lon,1.13);
  var p2=llToVec(cities[ti].lat,cities[ti].lon,1.13);
  var mid=p1.clone().add(p2).multiplyScalar(0.5).normalize().multiplyScalar(1.7);
  var curve=new THREE.QuadraticBezierCurve3(p1,mid,p2);
  var pts=curve.getPoints(50);
  var g=new THREE.BufferGeometry().setFromPoints(pts);
  var m=new THREE.LineBasicMaterial({color:color||0x06b6d4,transparent:true,opacity:0.3});
  var line=new THREE.Line(g,m);
  return {line:line,opacity:0.3,fadeIn:true};
}
for (var j=0;j<4;j++){
  var fi=Math.floor(Math.random()*cities.length),ti=Math.floor(Math.random()*cities.length);
  while(ti===fi)ti=Math.floor(Math.random()*cities.length);
  var cols=[0x06b6d4,0x8b5cf6,0x3b82f6,0x06b6d4];
  var arc=mkArc(fi,ti,j%2===0?0x06b6d4:0x8b5cf6);
  arcsG.add(arc.line);activeArcs.push(arc);
}
setInterval(function(){
  if(activeArcs.length===0)return;
  var idx=Math.floor(Math.random()*activeArcs.length);
  var old=activeArcs[idx];arcsG.remove(old.line);
  if(old.line.geometry)old.line.geometry.dispose();
  if(old.line.material)old.line.material.dispose();
  var fi=Math.floor(Math.random()*cities.length),ti=Math.floor(Math.random()*cities.length);
  while(ti===fi)ti=Math.floor(Math.random()*cities.length);
  var nc=Math.random()<0.5?0x06b6d4:0x8b5cf6;
  var nw=mkArc(fi,ti,nc);arcsG.add(nw.line);activeArcs[idx]=nw;
},4000);
// Particules
var pG=new THREE.BufferGeometry(),pN=300,posArr=new Float32Array(pN*3);
for (var p=0;p<pN*3;p+=3){
  var r=1.3+Math.random()*1.4,th=Math.random()*Math.PI*2,ph=Math.acos(2*Math.random()-1);
  posArr[p]=r*Math.sin(ph)*Math.cos(th);
  posArr[p+1]=r*Math.sin(ph)*Math.sin(th);
  posArr[p+2]=r*Math.cos(ph);
}
pG.setAttribute('position',new THREE.BufferAttribute(posArr,3));
var pM=new THREE.PointsMaterial({color:0xffffff,size:0.012,transparent:true,opacity:0.2});
var particles=new THREE.Points(pG,pM);scene.add(particles);
// Halo atmosphere
var hG=new THREE.SphereGeometry(1.16,64,40);
var hM=new THREE.MeshBasicMaterial({color:0x06b6d4,transparent:true,opacity:0.03,side:THREE.BackSide});
var halo=new THREE.Mesh(hG,hM);scene.add(halo);
// Animation
function animate(){
  requestAnimationFrame(animate);
  globe.rotation.y+=0.0006;latG.rotation.y+=0.0006;
  ptsG.rotation.y+=0.0006;arcsG.rotation.y+=0.0006;
  particles.rotation.y+=0.0002;
  for (var a=0;a<activeArcs.length;a++){
    var arc=activeArcs[a];
    if(arc.fadeIn){arc.opacity+=0.004;if(arc.opacity>=0.45){arc.opacity=0.45;arc.fadeIn=false;}}
    else{arc.opacity-=0.003;if(arc.opacity<=0.15){arc.opacity=0.15;arc.fadeIn=true;}}
    arc.line.material.opacity=arc.opacity;
  }
  renderer.render(scene,camera);
}
animate();
window.addEventListener('resize',function(){
  var w=container.clientWidth,h=container.clientHeight;
  camera.aspect=w/h;camera.updateProjectionMatrix();renderer.setSize(w,h);
});
})();
