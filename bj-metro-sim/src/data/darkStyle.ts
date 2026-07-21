// 灰白中性底图降级方案（当 MapTiler 不可用时）
const darkStyle = {
  version: 8,
  name: 'Project RailSim',
  glyphs: 'https://fonts.openmaptiles.org/{fontstack}/{range}.pbf',
  sources: {
    amap: {
      type: 'raster' as const,
      tiles: [
        'https://webrd01.is.autonavi.com/appmaptile?lang=zh_cn&size=1&scale=1&style=8&x={x}&y={y}&z={z}',
      ],
      tileSize: 256,
      minzoom: 0,
      maxzoom: 18,
      attribution: '&copy; 高德地图',
    },
  },
  layers: [
    {
      id: 'amap-layer',
      type: 'raster',
      source: 'amap',
      paint: {
        'raster-opacity': 0.45,
        'raster-saturation': -1,
        'raster-brightness-min': 0.15,
        'raster-brightness-max': 0.45,
      },
    },
  ],
};

export default darkStyle;
