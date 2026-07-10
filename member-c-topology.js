(function () {
  var topologyBounds = { width: 400, height: 240 };
  var staticSignalBySeg = {};
  var switchByFrog = {};
  var switchConnectionByPair = {};

  function segmentPairKey(first, second) {
    return first < second ? first + ':' + second : second + ':' + first;
  }

  function rebuildIndexes() {
    staticSignalBySeg = {};
    switchByFrog = {};
    switchConnectionByPair = {};
    if (!RD) return;
    (RD.signals || []).forEach(function (signal) {
      (staticSignalBySeg[signal.segId] || (staticSignalBySeg[signal.segId] = [])).push(signal);
    });
    (RD.switches || []).forEach(function (sw) {
      if (sw.frogSeg != null) switchByFrog[sw.frogSeg] = sw;
      if (sw.frogSeg != null && sw.normSeg != null) {
        switchConnectionByPair[segmentPairKey(sw.frogSeg, sw.normSeg)] = {
          position: 'NORMAL', switchId: sw.id
        };
      }
      if (sw.frogSeg != null && sw.revSeg != null) {
        switchConnectionByPair[segmentPairKey(sw.frogSeg, sw.revSeg)] = {
          position: 'REVERSE', switchId: sw.id
        };
      }
    });
    var maxCol = 0;
    var maxRow = 0;
    (RD.segments || []).forEach(function (segment) {
      maxCol = Math.max(maxCol, segment.col || 0);
      maxRow = Math.max(maxRow, segment.row || 0);
    });
    topologyBounds = { width: 130 + (maxCol + 1) * 42, height: 120 + (maxRow + 1) * 58 };
  }

  function topologyPosition(segment) {
    return {
      x: 70 + (segment.col || 0) * 42,
      y: 68 + (segment.row || 0) * 58,
      w: 34
    };
  }

  function dynamicSignals() {
    var result = {};
    ((S && S.signals) || []).forEach(function (signal) { result[signal.id] = signal; });
    return result;
  }

  function selectedSegmentIds() {
    var result = {};
    Object.keys(selRoutes || {}).forEach(function (routeId) {
      (selRoutes[routeId].pathSegs || []).forEach(function (segmentId) { result[segmentId] = true; });
    });
    return result;
  }

  function colorForAspect(aspect) {
    return aspect === 'GREEN' ? '#3fb950' : aspect === 'YELLOW' ? '#d29922' : '#f85149';
  }

  window.fit = function () {
    if (!RD || !RD.segments) return;
    var availableWidth = Math.max(100, w.clientWidth - 36);
    var availableHeight = Math.max(100, w.clientHeight - 36);
    sc = Math.max(0.08, Math.min(1.2, availableWidth / topologyBounds.width, availableHeight / topologyBounds.height));
    ox = Math.max(14, (w.clientWidth - topologyBounds.width * sc) / 2);
    oy = Math.max(14, (w.clientHeight - topologyBounds.height * sc) / 2);
    document.getElementById('zl').textContent = Math.round(sc * 100) + '%';
    draw();
  };

  window.focusRoute = function (route) {
    if (!RD || !route || !route.pathSegs || !route.pathSegs.length) return;
    var byId = {};
    RD.segments.forEach(function (segment) { byId[segment.id] = segment; });
    var positions = route.pathSegs.map(function (id) { return byId[id]; }).filter(Boolean).map(topologyPosition);
    if (!positions.length) return;
    var minX = Math.min.apply(null, positions.map(function (p) { return p.x; }));
    var maxX = Math.max.apply(null, positions.map(function (p) { return p.x + p.w; }));
    var minY = Math.min.apply(null, positions.map(function (p) { return p.y; }));
    var maxY = Math.max.apply(null, positions.map(function (p) { return p.y; }));
    sc = Math.max(0.45, Math.min(2.2, (w.clientWidth - 150) / Math.max(160, maxX - minX + 100), (w.clientHeight - 150) / Math.max(120, maxY - minY + 100)));
    ox = w.clientWidth / 2 - ((minX + maxX) / 2) * sc;
    oy = w.clientHeight / 2 - ((minY + maxY) / 2) * sc;
    document.getElementById('zl').textContent = Math.round(sc * 100) + '%';
  };

  window.clickRoute = function (routeId) {
    if (selRoutes[routeId]) {
      delete selRoutes[routeId];
    } else {
      var route = (RD.routes || []).find(function (item) { return item.id === routeId; });
      if (route) {
        selRoutes[routeId] = route;
        focusRoute(route);
      }
    }
    updateRouteList(document.getElementById('route-filter').value);
    draw();
  };

  window.updateRouteList = function (filter) {
    if (!RD || !RD.routes) return;
    var query = (filter || '').toLowerCase();
    var routes = RD.routes.filter(function (route) {
      return !query || route.id.toLowerCase().indexOf(query) >= 0 || route.name.toLowerCase().indexOf(query) >= 0;
    });
    document.getElementById('route-list').innerHTML = routes.map(function (route) {
      var selected = selRoutes[route.id] ? ' sel' : '';
      var detail = 'sig' + route.startSig + ' -> sig' + route.endSig + ' | ' + route.pathSegs.length + ' Seg';
      return '<div class="' + selected + '" onclick="clickRoute(\'' + route.id + '\')"><span><b>' + route.id + '</b> ' + route.name + '</span><span class="m">' + detail + '</span></div>';
    }).join('');
    document.getElementById('rcnt').textContent = routes.length + '/' + RD.routes.length;
  };

  window.draw = function () {
    resize();
    cx.clearRect(0, 0, c.width, c.height);
    if (!RD || !RD.segments) return;

    var byId = {};
    var liveSegments = {};
    var liveSignalById = dynamicSignals();
    var chosen = selectedSegmentIds();
    var trainColors = (S && S.segTrainColors) || {};
    var trains = (S && S.trains) || [];
    RD.segments.forEach(function (segment) { byId[segment.id] = segment; });

    cx.save();
    cx.scale(2, 2);
    cx.translate(ox, oy);
    cx.scale(sc, sc);
    cx.fillStyle = '#040810';
    cx.fillRect(-ox / sc - 20, -oy / sc - 20, topologyBounds.width + 40, topologyBounds.height + 40);

    function drawLink(source, targetId, diverging) {
      var target = byId[targetId];
      if (!target) return;
      var switchConnection = switchConnectionByPair[segmentPairKey(source.id, targetId)];
      var isDiverging = switchConnection ? switchConnection.position === 'REVERSE' : diverging;
      var a = topologyPosition(source);
      var b = topologyPosition(target);
      cx.beginPath();
      cx.moveTo(a.x + a.w, a.y);
      if (isDiverging) {
        cx.quadraticCurveTo(a.x + a.w + 13, (a.y + b.y) / 2, b.x, b.y);
      } else {
        cx.lineTo(b.x, b.y);
      }
      cx.strokeStyle = isDiverging ? '#d29922' : '#496178';
      cx.lineWidth = isDiverging ? 1.8 : 1.15;
      cx.globalAlpha = isDiverging ? 0.95 : 0.65;
      cx.stroke();
      cx.globalAlpha = 1;
      if (switchConnection && sc >= 0.72) {
        cx.fillStyle = isDiverging ? '#f0c040' : '#9ab1c4';
        cx.font = '7px monospace';
        cx.textAlign = 'center';
        cx.fillText(isDiverging ? 'R' : 'N', (a.x + a.w + b.x) / 2, (a.y + b.y) / 2 - 4);
      }
    }

    RD.segments.forEach(function (segment) {
      drawLink(segment, segment.endForward, false);
      drawLink(segment, segment.endDiverging, true);
    });

    Object.keys(selRoutes || {}).forEach(function (routeId) {
      var route = selRoutes[routeId];
      var path = (route && route.pathSegs) || [];
      if (path.length < 2) return;
      cx.beginPath();
      path.forEach(function (segmentId, index) {
        var segment = byId[segmentId];
        if (!segment) return;
        var pos = topologyPosition(segment);
        if (index === 0) cx.moveTo(pos.x + pos.w / 2, pos.y);
        else cx.lineTo(pos.x + pos.w / 2, pos.y);
      });
      cx.strokeStyle = '#58a6ff';
      cx.globalAlpha = 0.8;
      cx.lineWidth = 3.5;
      cx.stroke();
      cx.globalAlpha = 1;
    });

    RD.segments.forEach(function (segment) {
      var pos = topologyPosition(segment);
      var active = !!chosen[segment.id];
      var trainColor = trainColors[segment.id];
      cx.strokeStyle = active ? '#58a6ff' : trainColor || '#6e8599';
      cx.lineWidth = active ? 5 : trainColor ? 4 : 2.4;
      cx.beginPath();
      cx.moveTo(pos.x, pos.y);
      cx.lineTo(pos.x + pos.w, pos.y);
      cx.stroke();
      cx.fillStyle = active ? '#b9dcff' : '#91a7ba';
      cx.beginPath();
      cx.arc(pos.x, pos.y, 1.8, 0, Math.PI * 2);
      cx.arc(pos.x + pos.w, pos.y, 1.8, 0, Math.PI * 2);
      cx.fill();
      if (sc >= 0.34) {
        cx.fillStyle = active ? '#d7ebff' : '#7f96a9';
        cx.font = '8px monospace';
        cx.textAlign = 'center';
        cx.fillText('S' + segment.id, pos.x + pos.w / 2, pos.y + 13);
      }
      if (segment.platformIds && segment.platformIds.length) {
        cx.fillStyle = 'rgba(143,195,31,0.32)';
        cx.fillRect(pos.x, pos.y - 5, pos.w, 10);
      }
      var sw = switchByFrog[segment.id];
      if (sw) {
        cx.fillStyle = '#d29922';
        cx.beginPath();
        cx.arc(pos.x + pos.w, pos.y, 4, 0, Math.PI * 2);
        cx.fill();
        if (sc >= 0.48) {
          cx.fillStyle = '#f0c040';
          cx.font = '8px monospace';
          cx.textAlign = 'left';
          cx.fillText('W' + sw.id + ' N/R', pos.x + pos.w + 5, pos.y - 6);
        }
      }
      (staticSignalBySeg[segment.id] || []).forEach(function (signal, index) {
        var live = liveSignalById[signal.id] || { aspect: 'RED' };
        var sy = pos.y - 10 - (index % 3) * 9;
        cx.fillStyle = colorForAspect(live.aspect);
        cx.beginPath();
        cx.arc(pos.x + 7 + (index % 2) * 10, sy, 2.5, 0, Math.PI * 2);
        cx.fill();
        if (sc >= 0.72 && (chosen[segment.id] || index === 0)) {
          cx.fillStyle = '#a7bacb';
          cx.font = '7px monospace';
          cx.textAlign = 'left';
          cx.fillText(signal.name || ('Sig' + signal.id), pos.x + 11, sy - 3);
        }
      });
    });

    trains.forEach(function (train) {
      var segment = byId[train.segId];
      if (!segment) return;
      var pos = topologyPosition(segment);
      cx.fillStyle = train.color || '#e74c3c';
      cx.fillRect(pos.x + 7, pos.y - 10, 20, 7);
      if (sc >= 0.45) {
        cx.fillStyle = '#ffffff';
        cx.font = '8px monospace';
        cx.textAlign = 'left';
        cx.fillText(train.id, pos.x + 7, pos.y - 14);
      }
    });
    cx.restore();

    document.getElementById('tk').textContent = S ? S.tick : '0';
    document.getElementById('oc').textContent = S ? S.occupiedCount + '/' + S.totalAxleSections : '0';
    document.getElementById('lc').textContent = S ? S.lockedRouteCount : '0';
    var openSignals = ((S && S.signals) || []).filter(function (signal) { return signal.aspect !== 'RED'; }).length;
    document.getElementById('sc').textContent = openSignals + '/' + ((RD && RD.signals) || []).length;
    document.getElementById('foot').textContent = RD.segments.length + ' Seg | ' + RD.signals.length + ' signals | ' + RD.switches.length + ' switches | ' + RD.routes.length + ' routes | grey=normal, amber=diverging';
  };

  function loadTopology() {
    fetch(A + '/api/phase2/member-c/static-routes')
      .then(function (response) { return response.json(); })
      .then(function (data) {
        RD = data;
        rebuildIndexes();
        updateRouteList();
        fit();
      })
      .catch(function (error) { console.error('topology load failed', error); });
  }

  loadTopology();
}());
