/** Horloge temps reel. */
(function(){
  function pad(n){return n<10?'0'+n:''+n;}
  setInterval(function(){
    var n=new Date();
    document.getElementById('clock').textContent=pad(n.getHours())+':'+pad(n.getMinutes())+':'+pad(n.getSeconds());
  },1000);
  var n=new Date();
  document.getElementById('clock').textContent=pad(n.getHours())+':'+pad(n.getMinutes())+':'+pad(n.getSeconds());
})();
