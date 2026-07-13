function draw(){
  resize();cx.clearRect(0,0,c.width,c.height);
  if(!S||!S.segments)return;
  detect();
  var segs=S.segments,tc=S.segTrainColors||{},ts=S.trains||[],sigs=S.signals||[],i,j,n=segs.length;
  var segsByRow={};
  for(i=0;i<n;i++){var r=segs[i].row||0;if(!segsByRow[r])segsByRow[r]=[];segsByRow[r].push(segs[i])}
  var segEq={};for(i=0;i<n;i++)segEq[segs[i].id]=1;
  for(i=0;i<sigs.length;i++){var id=sigs[i].segId;if(segEq[id]!=null)segEq[id]++}
  for(i=0;i<n;i++){if(segs[i].sw)segEq[segs[i].id]+=0.5}
  var W2=w.clientWidth*2-BM*2-40;
  var segWidth={},rowX={},rowTotalW={};
  for(var row in segsByRow){var rsegs=segsByRow[row];rsegs.sort(function(a,b){return a.col-b.col});
    var tw=0;for(i=0;i<rsegs.length;i++)tw+=segEq[rsegs[i].id];
    rowX[row]=[];rowTotalW[row]=0;
    for(i=0;i<rsegs.length;i++){var sw3=Math.max(14,W2*segEq[rsegs[i].id]/tw);segWidth[rsegs[i].id]=sw3;rowX[row].push({segId:rsegs[i].id,x:rowTotalW[row],w:sw3});rowTotalW[row]+=sw3}}
  var ROW_H=56,rowY={},y2=80;
  for(var row in segsByRow){rowY[row]=y2;y2+=ROW_H}
  var totalH=y2+60;
  cx.save();cx.scale(2,2);cx.translate(ox,oy);cx.scale(sc,sc);
  cx.fillStyle='#040810';cx.fillRect(-50,-50,BM+Math.max(W2,1200)+100,totalH);
  var segMap={};
  for(var row in segsByRow){var ry=rowY[row];var rx2=BM;cx.strokeStyle=row==0?'#3a5a7a':'#2a3a4a';cx.lineWidth=row==0?3:2;cx.beginPath();cx.moveTo(BM,ry);cx.lineTo(BM+rowTotalW[row],ry);cx.stroke();
    for(i=0;i<rowX[row].length;i++){var rsi=rowX[row][i];var sid=rsi.segId,sx=BM+rsi.x,sw2=rsi.w;segMap[sid]={x:sx,w:sw2,y:ry,row:row};
      var cl2=tc[sid]||null;if(cl2){cx.fillStyle=cl2;cx.globalAlpha=0.3;cx.fillRect(sx,ry-3,sw2,6);cx.globalAlpha=1}
      cx.strokeStyle='#1a2a3a';cx.lineWidth=0.3;cx.beginPath();cx.moveTo(sx,ry-6);cx.lineTo(sx,ry+6);cx.stroke();
      cx.fillStyle='#3a4a60';cx.font='8px monospace';cx.textAlign='center';cx.fillText(sid,sx+sw2/2,ry+16)}}
  for(i=0;i<n;i++){var seg=segs[i];if(!seg.endDiv)continue;var p2=segMap[seg.id];if(!p2)continue;var c2=segMap[seg.endDiv];if(!c2)continue;
    cx.strokeStyle='#d29922';cx.lineWidth=1.2;cx.globalAlpha=0.6;cx.beginPath();cx.moveTo(p2.x+p2.w/2,p2.y);cx.lineTo(c2.x,c2.y);cx.stroke();cx.globalAlpha=1;
    cx.fillStyle='#d29922';cx.font='9px monospace';cx.textAlign='center';cx.fillText('W',p2.x+p2.w/2,p2.y-8)}
  for(i=0;i<segs.length;i++){if(!segs[i].stn)continue;var sp=segMap[segs[i].id];if(!sp)continue;
    cx.fillStyle='rgba(143,195,31,0.25)';cx.fillRect(sp.x,sp.y-12,sp.w,22);cx.fillStyle='#f0c040';cx.font='9px sans-serif';cx.textAlign='center';cx.fillText('P'+segs[i].stn,sp.x+sp.w/2,sp.y-14)}
  for(i=0;i<sigs.length;i++){var sg2=sigs[i];var sp5=segMap[sg2.segId];if(!sp5)continue;
    var cl3=sg2.aspect=='GREEN'?'#3fb950':sg2.aspect=='YELLOW'?'#d29922':'#f85149';
    var sy=sp5.y-16;cx.fillStyle=cl3;cx.globalAlpha=0.2;cx.beginPath();cx.arc(sp5.x+sp5.w*0.4,sy,8,0,2*Math.PI);cx.fill();cx.globalAlpha=1;
    cx.fillStyle=cl3;cx.beginPath();cx.arc(sp5.x+sp5.w*0.4,sy,4,0,2*Math.PI);cx.fill();cx.strokeStyle='#fff';cx.lineWidth=0.5;cx.stroke();
    cx.strokeStyle='#3a4a5a';cx.lineWidth=0.6;cx.beginPath();cx.moveTo(sp5.x+sp5.w*0.4,sp5.y);cx.lineTo(sp5.x+sp5.w*0.4,sy+3);cx.stroke();
    cx.fillStyle='#6a7a90';cx.font='8px monospace';cx.textAlign='center';cx.fillText(sg2.name||('S'+sg2.id),sp5.x+sp5.w*0.4,sy-8)}
  for(i=0;i<ts.length;i++){var t=ts[i],sp7=segMap[t.segId];if(!sp7)continue;
    var bW=Math.max(t.lengthM/100*40,20),tx=sp7.x+sp7.w/2-bW/2,ty=sp7.y-22;
    cx.fillStyle='rgba(0,0,0,0.5)';cx.fillRect(tx+2,ty+2,bW,28);
    cx.fillStyle=t.color;cx.fillRect(tx,ty,bW,28);cx.strokeStyle='#fff';cx.lineWidth=1;cx.strokeRect(tx,ty,bW,28);
    cx.fillStyle='#fff';cx.beginPath();cx.moveTo(tx+bW+2,ty+14);cx.lineTo(tx+bW-6,ty+6);cx.lineTo(tx+bW-6,ty+22);cx.fill();
    cx.fillStyle='#fff';cx.font='bold 10px monospace';cx.textAlign='left';cx.fillText(t.id+' '+t.speedMps.toFixed(0)+'m/s',tx,ty-4)}
  if(RD&&RD.segments){for(var rid2 in selRoutes){var srd=selRoutes[rid2];
    cx.fillStyle='rgba(88,166,255,0.3)';var ps=srd.pathSegs||[];
    for(j=0;j<ps.length;j++){var sp8=segMap[ps[j]];if(sp8)cx.fillRect(sp8.x-1,sp8.y-4,sp8.w+2,8)}}}
  cx.restore();
  document.getElementById('tk').textContent=S.tick;document.getElementById('oc').textContent=S.occupiedCount+'/'+S.totalAxleSections;
  document.getElementById('lc').textContent=S.lockedRouteCount;var gc2=0;for(i=0;i<sigs.length;i++)if(sigs[i].aspect!='RED')gc2++;
  document.getElementById('sc').textContent=gc2+'/'+sigs.length;
  var rc=Object.keys(segsByRow||{}).length;
  document.getElementById('foot').innerHTML=segs.length+' Seg · '+sigs.length+' 信号 · '+rc+' 行轨道';
}