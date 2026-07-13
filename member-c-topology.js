(function () {
  var topologyBounds = { width: 400, height: 240 };
  var staticSignalBySeg = {};
  var switchByFrog = {};
  var switchConnectionByPair = {};
  var selectedSegmentId = null;
  var chineseEvents = [];
  var seenServerEvents = {};
  var stationStartBySegment = {};
  var stationCount = 0;
  var LOG_STORAGE_KEY = 'member-c-topology-log-v1';
  try { chineseEvents = JSON.parse(window.sessionStorage.getItem(LOG_STORAGE_KEY) || '[]') || []; } catch (error) { chineseEvents = []; }

  function persistChineseLog() {
    try { window.sessionStorage.setItem(LOG_STORAGE_KEY, JSON.stringify(chineseEvents)); } catch (error) { /* session storage is optional */ }
  }

  function applyStartOptions(data) {
    stationStartBySegment = {};
    (data.startOptions || []).forEach(function (option) {
      stationStartBySegment[option.segmentId] = {
        code: option.stationCode,
        name: option.stationName,
        directions: option.directions || []
      };
    });
  }

  function loadStationStarts() {
    if (!window.ENGINE_MODE) return;
    fetch(A + '/api/sim/topology-state')
      .then(function (response) { return response.json(); })
      .then(function (data) {
        applyStartOptions(data);
        updateRouteList(document.getElementById('route-filter').value);
      })
      .catch(function (error) { console.error('station start data load failed', error); });
  }
  function nextTopologyTrainId() {
    var existing = {};
    ((S && S.trains) || []).forEach(function (train) { existing[train.id] = true; });
    var index = 1;
    while (existing['T-TOPO-' + String(index).padStart(3, '0')]) index += 1;
    return 'T-TOPO-' + String(index).padStart(3, '0');
  }

  function refreshEngineTopology() {
    return fetch(A + '/api/sim/topology-state')
      .then(function (response) { return response.json(); })
      .then(function (data) {
        prev = S;
        S = data;
        applyStartOptions(data);
        updateRouteList(document.getElementById('route-filter').value);
        draw();
      });
  }

  // The outer React header and this iframe can both start the engine.  Keep one
  // response-driven loop here so the canvas stays live for either entry point
  // without accumulating overlapping HTTP requests.
  function scheduleEngineRefresh() {
    if (!window.ENGINE_MODE) return;
    function refreshAfterResponse() {
      refreshEngineTopology()
        .catch(function (error) { console.error('engine topology refresh failed', error); })
        .then(function () { window.setTimeout(refreshAfterResponse, 150); });
    }
    refreshAfterResponse();
  }
  window.addEngineTrain = function (direction) {
    var start = stationStartBySegment[selectedSegmentId];
    if (!start) {
      addChineseEvent('提示', '主引擎只允许从站台 Seg 加车。', S ? S.tick : 0);
      renderChineseLog();
      return;
    }
    var trainId = nextTopologyTrainId();
    fetch(A + '/api/sim/train/add', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ trainId: trainId, initialStationCode: start.code, initialSegmentId: selectedSegmentId, direction: direction, operationMode: 'ATO' })
    }).then(function (response) { return response.json(); }).then(function (data) {
      if (!data.ok) {
        if (data.error === 'INITIAL_PLACEMENT_OCCUPIED') {
          data.error = '起点站台已有列车占用' + (data.conflictingTrainIds && data.conflictingTrainIds.length ? '（' + data.conflictingTrainIds.join('、') + '）' : '') + '，请等待其驶离后再加车';
        }
        addChineseEvent('失败', '主引擎加车失败：' + (data.error || '未知原因'), S ? S.tick : 0);
        renderChineseLog();
        return;
      }
      addChineseEvent('加车', trainId + ' 已在 ' + start.name + ' 站台加入，ATO 将按 MA 自动申请进路。', S ? S.tick : 0);
      if (S && S.clockState === 'RUNNING') { refreshEngineTopology(); return; }
      var action = S && S.clockState === 'PAUSED' ? '/api/sim/resume' : '/api/sim/start';
      fetch(A + action, { method: 'POST' }).then(function () { refreshEngineTopology(); });
    }).catch(function () {
      addChineseEvent('失败', '无法连接主引擎加车接口。', S ? S.tick : 0);
      renderChineseLog();
    });
  };

  function engineStartControls() {
    var start = stationStartBySegment[selectedSegmentId];
    if (!start) {
      var segment = (RD.segments || []).find(function (item) { return item.id === selectedSegmentId; });
      if (segment && segment.platformIds && segment.platformIds.length) {
        return '<div class="empty-note">该站台是到达或折返站台，当前没有可向相邻站发车的进路。</div>';
      }
      return '<div class="empty-note">主引擎仅允许从站台 Seg 加车；区间 Seg 仅用于查询进路。</div>';
    }
    var buttons = '';
    if (start.directions.indexOf('UP') >= 0) buttons += '<button onclick="addEngineTrain(\'UP\')">在 ' + start.name + ' 上行加 ATO 车</button>';
    if (start.directions.indexOf('DOWN') >= 0) buttons += '<button onclick="addEngineTrain(\'DOWN\')">在 ' + start.name + ' 下行加 ATO 车</button>';
    return buttons ? '<div class="route-actions">' + buttons + '</div>' : '<div class="empty-note">该站台方向没有可办理的相邻站进路。</div>';
  }
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

  function lockedRouteMap() {
    var result = {};
    if (!RD || !S) return result;
    ((S.routes || [])).forEach(function (state) {
      if (state.state !== 'LOCKED' && state.state !== 'APPROACH_LOCKED') return;
      var route = (RD.routes || []).find(function (item) { return item.id === state.routeId; });
      if (route) result[route.id] = route;
    });
    return result;
  }

  function segmentIdsForRoutes(routes) {
    var result = {};
    Object.keys(routes).forEach(function (routeId) {
      (routes[routeId].pathSegs || []).forEach(function (segmentId) { result[segmentId] = true; });
    });
    return result;
  }

  function selectedSignalRoles() {
    var result = {};
    Object.keys(selRoutes || {}).forEach(function (routeId) {
      var route = selRoutes[routeId];
      if (!route) return;
      if (route.startSig != null) result[route.startSig] = result[route.startSig] || 'start';
      if (route.endSig != null) result[route.endSig] = result[route.endSig] === 'start' ? 'both' : 'end';
    });
    return result;
  }

  function isReverseSignal(signal) {
    return String(signal.direction || '').toLowerCase() === '0xaa';
  }

  function signalOffsetRatio(signal, segment) {
    var length = Number(segment.lengthM) || 0;
    var offset = Number(signal.offsetM) || 0;
    if (length <= 0) return 0.5;
    return Math.max(0.08, Math.min(0.92, offset / length));
  }

  function colorForAspect(aspect) {
    return aspect === 'GREEN' ? '#3fb950' : aspect === 'YELLOW' ? '#d29922' : '#f85149';
  }

  function addChineseEvent(category, message, tick) {
    var key = String(tick) + '|' + category + '|' + message;
    if (seenServerEvents[key]) return;
    seenServerEvents[key] = true;
    chineseEvents.unshift({ category: category, message: message, tick: tick || 0 });
    chineseEvents = chineseEvents.slice(0, 100);
    persistChineseLog();
  }

  function failureText(reason) {
    return {
      CONFLICT_ROUTE_LOCKED: '存在敌对进路已锁闭',
      SECTION_OCCUPIED: '进路区段当前被占用',
      SWITCH_UNAVAILABLE: '所需道岔不可用',
      ROUTE_NOT_FOUND: '进路不存在'
    }[reason] || reason || '未知原因';
  }

  function renderChineseLog() {
    if (!S) return;
    ((S.events || [])).forEach(function (event) {
      addChineseEvent(event.category || '事件', event.message || '', event.tick);
    });
    if (prev && S.routes && prev.routes) {
      var before = {}, after = {};
      prev.routes.forEach(function (route) { before[route.routeId] = route; });
      S.routes.forEach(function (route) { after[route.routeId] = route; });
      Object.keys(after).forEach(function (routeId) {
        var oldState = before[routeId] || { state: 'IDLE' };
        var newState = after[routeId];
        if (oldState.state !== newState.state) {
          if (newState.state === 'LOCKED') addChineseEvent('锁闭', '进路 ' + routeId + ' 已锁闭', S.tick);
          else if (newState.state === 'IDLE' && oldState.state !== 'IDLE') addChineseEvent('释放', '进路 ' + routeId + ' 已释放', S.tick);
          else if (newState.state === 'FAILED') addChineseEvent('失败', '进路 ' + routeId + ' 办理失败：' + failureText(newState.failureReason), S.tick);
        }
      });
    }
    if (prev && S.axleSections && prev.axleSections) {
      var previousSections = {}, currentSections = {};
      prev.axleSections.forEach(function (section) { previousSections[section.sectionId] = section; });
      S.axleSections.forEach(function (section) { currentSections[section.sectionId] = section; });
      Object.keys(currentSections).forEach(function (sectionId) {
        if (currentSections[sectionId].occupied && !(previousSections[sectionId] || {}).occupied) {
          addChineseEvent('占压', '计轴区段 ' + sectionId + ' 进入占压', S.tick);
        }
      });
      Object.keys(previousSections).forEach(function (sectionId) {
        if (previousSections[sectionId].occupied && !(currentSections[sectionId] || {}).occupied) {
          addChineseEvent('出清', '计轴区段 ' + sectionId + ' 已出清', S.tick);
        }
      });
    }
    if (prev && S.trains && prev.trains) {
      var previousTrains = {}, currentTrains = {};
      prev.trains.forEach(function (train) { previousTrains[train.id] = train; });
      S.trains.forEach(function (train) { currentTrains[train.id] = train; });
      Object.keys(currentTrains).forEach(function (trainId) {
        var current = currentTrains[trainId];
        var previous = previousTrains[trainId] || {};
        if (current.phase === 'DWELLING' && previous.phase !== 'DWELLING') {
          addChineseEvent('到站', '列车 ' + trainId + ' 到达 ' + (current.currentStation || '站台') + '，停站 ' + Math.ceil(current.dwellRemainingSec || 0) + ' 秒', S.tick);
        }
        if (previous.phase === 'DWELLING' && current.phase === 'DEPARTING') {
          addChineseEvent('发车', '列车 ' + trainId + ' 停站结束，出站进路与 MA 已确认', S.tick);
        }
        if (current.phase === 'WAITING_ROUTE' && previous.phase !== 'WAITING_ROUTE') {
          addChineseEvent('等待', '列车 ' + trainId + ' 等待进路：' + (current.routeFailureReason || '当前不可办理'), S.tick);
        }
      });
    }    var list = document.getElementById('log-list');
    if (!list) return;
    list.innerHTML = chineseEvents.length ? chineseEvents.map(function (event) {
      return '<div class="' + (event.category === '失败' ? 'occ' : event.category === '锁闭' ? 'lock' : event.category === '释放' ? 'rel' : 'sig') + '">[' + event.tick + '] ' + event.message + '</div>';
    }).join('') : '<div class="empty-note">暂无事件。可点击轨道放置小车，再选择进路办理。</div>';
    document.getElementById('lcnt').textContent = chineseEvents.length;
  }

  function postManual(path, payload) {
    if (window.ENGINE_MODE) {
      addChineseEvent('提示', '旧演示的手工锁闭已禁用；请在站台 Seg 使用主引擎 ATO 加车。', S ? S.tick : 0);
      renderChineseLog();
      return Promise.resolve({ ok: false, error: 'USE_ENGINE_ATO' });
    }
    return fetch(A + path, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload || {})
    }).then(function (response) { return response.json(); }).then(function (data) {
      if (data.state) {
        prev = S;
        S = data.state;
      }
      if (!data.ok) addChineseEvent('失败', '操作失败：' + failureText(data.error), S ? S.tick : 0);
      updateRouteList(document.getElementById('route-filter').value);
      draw();
      return data;
    }).catch(function () {
      addChineseEvent('失败', '无法连接手动联锁接口', S ? S.tick : 0);
      renderChineseLog();
    });
  }

  window.placeFreeTrain = function () {
    if (selectedSegmentId == null) return;
    postManual('/api/phase2/member-c/manual/place', { segmentId: selectedSegmentId });
  };

  window.placeRouteTrain = function (routeId) {
    postManual('/api/phase2/member-c/manual/place-route', { routeId: routeId });
  };

  window.placeAndRequestRoute = function (routeId) {
    postManual('/api/phase2/member-c/manual/place-route', { routeId: routeId })
      .then(function (data) {
        if (data && data.ok) return postManual('/api/phase2/member-c/manual/request-route', { routeId: routeId });
        return null;
      });
  };

  window.requestRoute = function (routeId) {
    postManual('/api/phase2/member-c/manual/request-route', { routeId: routeId });
  };

  window.requestSelectedRoute = function () {
    var routeIds = Object.keys(selRoutes || {});
    var routeId = routeIds.length ? routeIds[routeIds.length - 1] : (S && S.manualRouteId);
    if (routeId) requestRoute(routeId);
  };

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
      var related = selectedSegmentId == null || (route.pathSegs || []).indexOf(selectedSegmentId) >= 0;
      var matches = !query || route.id.toLowerCase().indexOf(query) >= 0 || route.name.toLowerCase().indexOf(query) >= 0;
      return related && matches;
    });
    var inspector = document.getElementById('route-inspector');
    if (selectedSegmentId == null) {
      inspector.innerHTML = window.ENGINE_MODE
        ? '选择标绿站台 Seg 可向主引擎加 ATO 列车；列车会经 MA、ATO 和自动进路链运行。'
        : '点击图中的轨道以查看经过该 Seg 的进路。';
    } else {
      var segment = (RD.segments || []).find(function (item) { return item.id === selectedSegmentId; }) || {};
      var platformText = segment.platformIds && segment.platformIds.length ? '，站台 ' + segment.platformIds.join('、') : '';
      var detail = '<b>S' + selectedSegmentId + '</b>　长度 ' + (segment.lengthM || 0) + ' m' + platformText;
      inspector.innerHTML = window.ENGINE_MODE
        ? detail + engineStartControls()
        : detail + '<div class="route-actions"><button onclick="placeFreeTrain()">在 S' + selectedSegmentId + ' 起点放置小车</button>' +
          (S && S.manualMode === 'route' ? '<button class="warn" onclick="requestSelectedRoute()">办理当前选择进路</button>' : '') + '</div>';
    }
    document.getElementById('route-list').innerHTML = routes.length ? routes.map(function (route) {
      var selected = selRoutes[route.id] ? ' sel' : '';
      var detail = '信号 ' + route.startSigName + ' -> ' + route.endSigName + '，' + route.pathSegs.length + ' 个 Seg';
      var actions = window.ENGINE_MODE
        ? '<div class="route-actions"><span class="m">ATO 按进路链自动办理</span></div>'
        : '<div class="route-actions"><button onclick="event.stopPropagation();placeRouteTrain(\'' + route.id + '\')">在始端放车</button>' +
          '<button class="warn" onclick="event.stopPropagation();placeAndRequestRoute(\'' + route.id + '\')">放车并办理</button>' +
          '<button onclick="event.stopPropagation();requestRoute(\'' + route.id + '\')">仅办理（不放车）</button></div>';
      return '<div class="route-item' + selected + '" onclick="clickRoute(\'' + route.id + '\')">' +
        '<div class="route-title"><span><b>' + route.id + '</b> ' + route.name + '</span><span>' + detail + '</span></div>' + actions + '</div>';
    }).join('') : '<div class="empty-note">该 Seg 没有匹配的进路。</div>';
    document.getElementById('rcnt').textContent = selectedSegmentId == null ? routes.length + '/' + RD.routes.length : routes.length + ' 条相关进路';
  };

  window.draw = function () {
    resize();
    cx.clearRect(0, 0, c.width, c.height);
    if (!RD || !RD.segments) return;
    renderChineseLog();

    var byId = {};
    var liveSegments = {};
    var liveSignalById = dynamicSignals();
    var liveSwitchById = {};
    ((S && S.switches) || []).forEach(function (sw) { liveSwitchById[String(sw.switchId || sw.id)] = sw; });
    var chosen = selectedSegmentIds();
    var lockedRoutes = lockedRouteMap();
    var lockedSegments = segmentIdsForRoutes(lockedRoutes);
    var signalRoles = selectedSignalRoles();
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

    Object.keys(lockedRoutes).forEach(function (routeId) {
      var path = lockedRoutes[routeId].pathSegs || [];
      if (path.length < 2) return;
      cx.beginPath();
      path.forEach(function (segmentId, index) {
        var segment = byId[segmentId];
        if (!segment) return;
        var pos = topologyPosition(segment);
        if (index === 0) cx.moveTo(pos.x + pos.w / 2, pos.y);
        else cx.lineTo(pos.x + pos.w / 2, pos.y);
      });
      cx.strokeStyle = '#20c997';
      cx.globalAlpha = 0.9;
      cx.lineWidth = 3.2;
      cx.setLineDash([5, 3]);
      cx.stroke();
      cx.setLineDash([]);
      cx.globalAlpha = 1;
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
      var active = !!chosen[segment.id] || segment.id === selectedSegmentId;
      var locked = !!lockedSegments[segment.id];
      var trainColor = trainColors[segment.id];
      cx.strokeStyle = segment.id === selectedSegmentId ? '#f0c040' : active ? '#58a6ff' : trainColor || (locked ? '#20c997' : '#6e8599');
      cx.lineWidth = active ? 5 : trainColor ? 4 : locked ? 3.4 : 2.4;
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
        cx.fillStyle = stationStartBySegment[segment.id]
          ? 'rgba(143,195,31,0.38)'
          : 'rgba(143,195,31,0.12)';
        cx.fillRect(pos.x, pos.y - 5, pos.w, 10);
      }
      var sw = switchByFrog[segment.id];
      if (sw) {
        var liveSwitch = liveSwitchById[String(sw.id)] || {};
        var isLocked = !!liveSwitch.lockedByRouteId;
        var positionText = String(liveSwitch.actualPosition || 'NORMAL').toUpperCase() === 'REVERSE' ? 'R' : 'N';
        cx.fillStyle = isLocked ? '#20c997' : '#d29922';
        cx.beginPath();
        cx.arc(pos.x + pos.w, pos.y, 4, 0, Math.PI * 2);
        cx.fill();
        if (sc >= 0.48) {
          cx.fillStyle = isLocked ? '#6ee7c8' : '#f0c040';
          cx.font = '8px monospace';
          cx.textAlign = 'left';
          cx.fillText('W' + sw.id + ' ' + positionText + (isLocked ? ' locked' : ''), pos.x + pos.w + 5, pos.y - 6);
        }
      }
      (staticSignalBySeg[segment.id] || []).forEach(function (signal, index) {
        var live = liveSignalById[signal.id] || { aspect: 'RED' };
        var reverse = isReverseSignal(signal);
        var sameDirectionIndex = (staticSignalBySeg[segment.id] || [])
          .filter(function (item) { return isReverseSignal(item) === reverse; })
          .findIndex(function (item) { return item.id === signal.id; });
        var lane = sameDirectionIndex < 0 ? index : sameDirectionIndex;
        var sigX = pos.x + pos.w * signalOffsetRatio(signal, segment);
        var side = reverse ? 1 : -1;
        var sy = pos.y + side * (12 + (lane % 3) * 10);
        var aspectColor = colorForAspect(live.aspect);
        var role = signalRoles[signal.id];
        var selectedSignal = !!role;

        cx.strokeStyle = selectedSignal ? (role === 'end' ? '#a371f7' : '#58a6ff') : '#3a4a5a';
        cx.lineWidth = selectedSignal ? 1.3 : 0.6;
        cx.beginPath();
        cx.moveTo(sigX, pos.y);
        cx.lineTo(sigX, sy - side * 3);
        cx.stroke();

        cx.fillStyle = aspectColor;
        cx.globalAlpha = selectedSignal ? 0.24 : 0.16;
        cx.beginPath();
        cx.arc(sigX, sy, selectedSignal ? 6.8 : 5.2, 0, Math.PI * 2);
        cx.fill();
        cx.globalAlpha = 1;

        cx.fillStyle = aspectColor;
        cx.beginPath();
        cx.arc(sigX, sy, selectedSignal ? 3.2 : 2.5, 0, Math.PI * 2);
        cx.fill();

        if (selectedSignal) {
          cx.strokeStyle = role === 'end' ? '#a371f7' : '#58a6ff';
          cx.lineWidth = 1.2;
          cx.beginPath();
          cx.arc(sigX, sy, 7.4, 0, Math.PI * 2);
          cx.stroke();
        }

        cx.fillStyle = selectedSignal ? (role === 'end' ? '#d2b8ff' : '#b9dcff') : '#8094a8';
        cx.beginPath();
        if (reverse) {
          cx.moveTo(sigX - 7, sy);
          cx.lineTo(sigX - 2, sy - 3.5);
          cx.lineTo(sigX - 2, sy + 3.5);
        } else {
          cx.moveTo(sigX + 7, sy);
          cx.lineTo(sigX + 2, sy - 3.5);
          cx.lineTo(sigX + 2, sy + 3.5);
        }
        cx.closePath();
        cx.fill();

        if (sc >= 0.72 && (selectedSignal || chosen[segment.id] || index === 0)) {
          cx.fillStyle = selectedSignal ? '#d7ebff' : '#a7bacb';
          cx.font = '7px monospace';
          cx.textAlign = reverse ? 'right' : 'left';
          cx.fillText(signal.name || ('Sig' + signal.id), sigX + (reverse ? -10 : 10), sy - side * 5);
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
    document.getElementById('foot').textContent = RD.segments.length + ' Seg | ' + RD.signals.length + ' signals | ' + RD.switches.length + ' switches | ' + RD.routes.length + ' routes | cyan=dangqian locked route | grey=normal, amber=diverging';
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

  c.addEventListener('click', function (event) {
    if (!RD || !RD.segments) return;
    var bounds = c.getBoundingClientRect();
    var x = (event.clientX - bounds.left - ox) / sc;
    var y = (event.clientY - bounds.top - oy) / sc;
    var hit = null;
    (RD.segments || []).forEach(function (segment) {
      var pos = topologyPosition(segment);
      if (x >= pos.x - 4 && x <= pos.x + pos.w + 4 && y >= pos.y - 12 && y <= pos.y + 12) hit = segment;
    });
    if (!hit) return;
    selectedSegmentId = hit.id;
    switchTab('route');
    updateRouteList(document.getElementById('route-filter').value);
    draw();
  });

  loadStationStarts();
  loadTopology();
  scheduleEngineRefresh();
}());
